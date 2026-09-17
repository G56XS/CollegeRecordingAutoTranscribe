from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any

# Optional polished UI. The program still works without Rich.
try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table
    from rich.text import Text
    from rich.align import Align
    from rich.rule import Rule
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# ============================================================
# CONFIG
# ============================================================
ROOT_DIR = Path(r"C:\Users\Iven\Documents\College Archive")
MODEL = "medium"
LANGUAGE = "Indonesian"
DEVICE = "cuda"

# Local Whisper does NOT have the hosted API's 25 MB upload limit.
# We still split long recordings into conservative chunks for reliability.
MAX_CHUNK_MINUTES = 20
SILENCE_SEARCH_RADIUS_MINUTES = 5
SILENCE_MIN_DURATION = 0.50
SILENCE_DB = -35
CHUNK_BITRATE = "96k"

AUDIO_EXTENSIONS = {
    ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus",
    ".webm", ".mp4", ".mkv"
}

SAVE_ALL_FORMATS = False
STATE_FILE = ROOT_DIR / ".college_archive_transcriber_state.json"
SESSION_RE = re.compile(r"^Pertemuan_(\d+)$", re.IGNORECASE)
CHUNK_DIR_NAME = "_whisper_chunks"

# UI tuning
MAX_LOG_LINES = 9
RICH_THEME = {
    "title": "bold bright_cyan",
    "accent": "cyan",
    "good": "bold green",
    "warn": "bold yellow",
    "bad": "bold red",
    "muted": "bright_black",
    "value": "bright_white",
}

console = Console() if RICH_AVAILABLE else None


def fmt_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:
        return "calculating..."
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def short_name(value: str, width: int = 36) -> str:
    value = str(value)
    if len(value) <= width:
        return value
    return value[: max(1, width - 1)] + "…"


def header() -> None:
    if not RICH_AVAILABLE:
        print("=" * 82)
        print(" College Archive Transcriber — Whisper Medium + CUDA")
        print("=" * 82)
        print(f"Archive : {ROOT_DIR}")
        print(f"Whisper : {MODEL} | Language: {LANGUAGE} | Device: {DEVICE}")
        print(f"Chunks  : max {MAX_CHUNK_MINUTES} min, cut near silence when possible")
        print()
        return

    console.clear()
    subtitle = Text("WHISPER MEDIUM  /  CUDA  /  SILENCE-AWARE CHUNKING", style="bold bright_white")
    body = Table.grid(padding=(0, 2))
    body.add_column(style="bright_cyan", justify="right")
    body.add_column(style="bright_white")
    body.add_row("Archive", str(ROOT_DIR))
    body.add_row("Engine", f"Whisper {MODEL}  •  {LANGUAGE}  •  {DEVICE}")
    body.add_row("Chunking", f"Up to {MAX_CHUNK_MINUTES} min  •  prefers low-volume / silence points")
    body.add_row("Output", "1 original audio + 1 combined transcript.txt per Pertemuan_N")
    console.print(Panel.fit(body, title="[title]COLLEGE ARCHIVE TRANSCRIBER[/title]", border_style="cyan", padding=(1, 2)))


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"files": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except Exception:
        pass
    warn("State file could not be read. A new state will be created.")
    return {"files": {}}


def save_state(state: dict[str, Any]) -> None:
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def is_session_folder(path: Path) -> bool:
    return SESSION_RE.match(path.name) is not None


def subject_for(path: Path) -> str:
    rel = path.resolve().relative_to(ROOT_DIR.resolve())
    return rel.parts[0] if rel.parts else "__ROOT__"


def subject_dir(subject: str) -> Path:
    return ROOT_DIR if subject == "__ROOT__" else ROOT_DIR / subject


def session_dir(subject: str, meeting: int) -> Path:
    return subject_dir(subject) / f"Pertemuan_{meeting}"


def in_generated_chunk_folder(path: Path) -> bool:
    return CHUNK_DIR_NAME.lower() in {p.lower() for p in path.parts}


def discover_audio() -> dict[str, list[Path]]:
    if not ROOT_DIR.exists():
        raise FileNotFoundError(f"Archive folder does not exist: {ROOT_DIR}")

    groups: dict[str, list[Path]] = defaultdict(list)
    for p in ROOT_DIR.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        if in_generated_chunk_folder(p):
            continue
        try:
            rel = p.relative_to(ROOT_DIR)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue

        subject = subject_for(p)
        groups[subject].append(p)

    for subject in groups:
        groups[subject].sort(key=lambda p: (p.stat().st_mtime, p.name.lower()))
    return dict(sorted(groups.items(), key=lambda item: item[0].lower()))


def get_whisper_command() -> list[str]:
    whisper_exe = shutil.which("whisper")
    if whisper_exe:
        return [whisper_exe]
    return [sys.executable, "-m", "whisper"]


def run_capture(cmd: list[str]) -> tuple[int, str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    return proc.returncode, proc.stdout


def ffprobe_duration(audio: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio)
    ]
    rc, out = run_capture(cmd)
    if rc != 0:
        return None
    try:
        value = float(out.strip())
        return value if value >= 0 else None
    except ValueError:
        return None


def detect_silences(audio: Path) -> list[tuple[float, float]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    cmd = [
        ffmpeg, "-hide_banner", "-nostats", "-i", str(audio),
        "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_DURATION}",
        "-f", "null", "-"
    ]
    rc, out = run_capture(cmd)
    if rc not in (0, 1):
        return []

    starts: list[float] = []
    silences: list[tuple[float, float]] = []
    for line in out.splitlines():
        m1 = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m1:
            starts.append(float(m1.group(1)))
        m2 = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m2 and starts:
            start = starts.pop(0)
            end = float(m2.group(1))
            if end > start:
                silences.append((start, end))
    return silences


def choose_cut(start: float, desired: float, duration: float, silences: list[tuple[float, float]]) -> float:
    radius = SILENCE_SEARCH_RADIUS_MINUTES * 60
    candidates: list[tuple[float, float]] = []
    for s, e in silences:
        if e <= start + 1 or s >= duration - 1:
            continue
        midpoint = (s + e) / 2
        if abs(midpoint - desired) <= radius:
            candidates.append((abs(midpoint - desired), midpoint))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return max(start + 30, min(duration, candidates[0][1]))
    return min(duration, desired)


def plan_segments(duration: float, silences: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if duration <= 0:
        return []
    max_len = MAX_CHUNK_MINUTES * 60
    if duration <= max_len + 2:
        return [(0.0, duration)]

    segments: list[tuple[float, float]] = []
    start = 0.0
    while duration - start > max_len + 2:
        desired = start + max_len
        cut = choose_cut(start, desired, duration, silences)
        if cut - start < 60:
            cut = desired
        segments.append((start, cut))
        start = cut
    if duration - start > 0.5:
        segments.append((start, duration))
    return segments


def safe_move(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dest.resolve():
        return dest
    if dest.exists():
        try:
            if src.stat().st_size == dest.stat().st_size:
                src.unlink()
                return dest
        except OSError:
            pass
        stem, suffix = dest.stem, dest.suffix
        i = 2
        while True:
            candidate = dest.with_name(f"{stem}_{i}{suffix}")
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    shutil.move(str(src), str(dest))
    return dest


def fingerprint(path: Path) -> str:
    st = path.stat()
    raw = f"{subject_for(path)}|{path.name.lower()}|{st.st_size}|{int(st.st_mtime_ns)}".encode()
    return hashlib.sha1(raw).hexdigest()


def build_whisper_args(audio: Path, out_dir: Path) -> list[str]:
    return get_whisper_command() + [
        str(audio),
        "--model", MODEL,
        "--language", LANGUAGE,
        "--device", DEVICE,
        "--output_dir", str(out_dir),
        "--fp16", "True",
        "--output_format", "all" if SAVE_ALL_FORMATS else "txt",
    ]


def transcript_exists(out_dir: Path, audio: Path) -> bool:
    return (out_dir / f"{audio.stem}.txt").exists()


def eta_from(history: list[float], remaining_units: int) -> float | None:
    if not history or remaining_units <= 0:
        return None
    recent = history[-6:]
    ordered = sorted(recent)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return median * remaining_units


def info(msg: str) -> None:
    if RICH_AVAILABLE:
        console.print(f"[cyan]•[/cyan] {msg}")
    else:
        print(f"• {msg}")


def success(msg: str) -> None:
    if RICH_AVAILABLE:
        console.print(f"[green]✓[/green] {msg}")
    else:
        print(f"[OK] {msg}")


def warn(msg: str) -> None:
    if RICH_AVAILABLE:
        console.print(f"[yellow]![/yellow] {msg}")
    else:
        print(f"WARNING: {msg}")


def fail(msg: str) -> None:
    if RICH_AVAILABLE:
        console.print(f"[red]✗[/red] {msg}")
    else:
        print(f"ERROR: {msg}")


def make_dashboard(
    subject: str,
    meeting: int,
    audio: Path,
    recording_pos: int,
    recording_total: int,
    chunks_done: int,
    chunks_total: int,
    duration: float | None,
    history: list[float],
    log_lines: deque[str],
    phase: str,
) -> Group:
    remaining_recordings = recording_total - recording_pos
    eta = eta_from(history, remaining_recordings + max(0, chunks_total - chunks_done))

    left = Table.grid(padding=(0, 1))
    left.add_column(style="bright_cyan", justify="right")
    left.add_column(style="bright_white")
    left.add_row("Subject", short_name(subject, 34))
    left.add_row("Meeting", f"Pertemuan_{meeting}")
    left.add_row("Recording", short_name(audio.name, 34))
    left.add_row("Duration", fmt_seconds(duration))
    left.add_row("Phase", phase)

    right = Table.grid(padding=(0, 1))
    right.add_column(style="bright_cyan", justify="right")
    right.add_column(style="bright_white")
    right.add_row("Recordings", f"{recording_pos}/{recording_total}")
    right.add_row("Remaining", f"{remaining_recordings}")
    right.add_row("Chunks", f"{chunks_done}/{chunks_total}" if chunks_total else "preparing")
    right.add_row("ETA", fmt_seconds(eta))

    details = Table.grid(expand=True)
    details.add_column(ratio=1)
    details.add_column(ratio=1)
    details.add_row(
        Panel(left, title="CURRENT RECORDING", border_style="bright_blue"),
        Panel(right, title="QUEUE", border_style="bright_blue"),
    )

    progress = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None, complete_style="bright_cyan", finished_style="green"),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        expand=True,
    )
    task = progress.add_task("Chunk progress", total=max(chunks_total, 1), completed=min(chunks_done, chunks_total))

    log_text = "\n".join(log_lines) if log_lines else "Waiting for transcription output..."
    logs = Panel(log_text, title="LIVE ACTIVITY", border_style="grey50", height=12)

    return Group(details, Panel(progress, title="PROGRESS", border_style="cyan"), logs)


def transcribe_one(
    audio: Path,
    out_dir: Path,
    subject: str,
    meeting: int,
    recording_pos: int,
    recording_total: int,
    chunks_done: int,
    chunks_total: int,
    duration: float | None,
    history: list[float],
    log_lines: deque[str],
) -> tuple[bool, float]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_whisper_args(audio, out_dir)
    start = time.monotonic()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        fail("Whisper could not be started. Install with: pip install -U openai-whisper")
        fail(str(exc))
        return False, time.monotonic() - start

    def render(phase: str) -> None:
        if RICH_AVAILABLE:
            live.update(make_dashboard(
                subject, meeting, audio, recording_pos, recording_total,
                chunks_done, chunks_total, duration, history, log_lines, phase
            ))

    if RICH_AVAILABLE:
        with Live(make_dashboard(
            subject, meeting, audio, recording_pos, recording_total,
            chunks_done, chunks_total, duration, history, log_lines, "Whisper is running"
        ), refresh_per_second=8, console=console, transient=False) as live:
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
                log_lines.append(clean)
                render("Whisper is running")
            rc = process.wait()
            elapsed = time.monotonic() - start
            render("Finished" if rc == 0 else "Failed")
    else:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
        rc = process.wait()
        elapsed = time.monotonic() - start

    ok = rc == 0 and transcript_exists(out_dir, audio)
    return ok, elapsed


def split_audio(source: Path, chunk_dir: Path) -> list[Path]:
    duration = ffprobe_duration(source)
    if duration is None:
        raise RuntimeError("ffprobe could not read the audio duration. Is FFmpeg installed?")
    if duration < 1.0:
        raise RuntimeError(f"Audio duration is only {duration:.2f}s; refusing to transcribe an empty/invalid recording.")

    silences = detect_silences(source)
    segments = plan_segments(duration, silences)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    if len(segments) == 1 and segments[0] == (0.0, duration):
        return [source]

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to split long recordings. Install FFmpeg and add it to PATH.")

    chunks: list[Path] = []
    for i, (start, end) in enumerate(segments, 1):
        dest = chunk_dir / f"chunk_{i:03d}.m4a"
        if dest.exists() and ffprobe_duration(dest):
            chunks.append(dest)
            continue
        info(f"Preparing chunk {i}/{len(segments)}  ({fmt_seconds(end-start)})  [{fmt_seconds(start)} -> {fmt_seconds(end)}]")
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", CHUNK_BITRATE,
            str(dest)
        ]
        rc, out = run_capture(cmd)
        if rc != 0 or not dest.exists():
            raise RuntimeError(f"FFmpeg failed while creating {dest.name}:\n{out[-1500:]}")
        chunks.append(dest)
    return chunks


def combine_transcripts(chunks: list[Path], meeting_dir: Path, source_name: str) -> Path:
    target = meeting_dir / "transcript.txt"
    pieces = []
    for i, chunk in enumerate(chunks, 1):
        txt = chunk.with_suffix(".txt")
        if not txt.exists():
            raise RuntimeError(f"Missing Whisper output: {txt.name}")
        body = txt.read_text(encoding="utf-8", errors="replace").strip()
        pieces.append(f"===== CHUNK {i}/{len(chunks)} =====\n{body}\n")

    content = (
        f"Source audio: {source_name}\n"
        f"Model: Whisper {MODEL}\n"
        f"Language: {LANGUAGE}\n\n"
        + "\n".join(pieces)
    )
    target.write_text(content, encoding="utf-8")
    return target


def cleanup_chunks(chunk_dir: Path) -> None:
    if chunk_dir.exists():
        shutil.rmtree(chunk_dir, ignore_errors=False)


def cleanup_short_whisper_txt(meeting_dir: Path, source: Path) -> None:
    intermediate = meeting_dir / f"{source.stem}.txt"
    target = meeting_dir / "transcript.txt"
    if intermediate.exists() and intermediate.resolve() != target.resolve():
        intermediate.unlink()


def reindex_subject(subject: str, ordered: list[Path], entries: dict[str, Any]) -> None:
    for idx, path in enumerate(ordered, 1):
        key = fingerprint(path)
        entries.setdefault(key, {"subject": subject, "name": path.name, "status": "pending"})
        if not isinstance(entries[key].get("meeting"), int):
            entries[key]["meeting"] = idx

    assignments = []
    for idx, path in enumerate(ordered, 1):
        key = fingerprint(path)
        ent = entries[key]
        old = int(ent.get("meeting", idx))
        assignments.append((path, old, idx))

    changes = [(old, new) for _, old, new in assignments if old != new]
    if not changes:
        return

    token = f".reindex_{int(time.time()*1000)}"
    subject_root = subject_dir(subject)
    staged: dict[int, Path] = {}
    try:
        for _, old, _ in changes:
            src = session_dir(subject, old)
            if src.exists() and old not in staged:
                tmp = subject_root / f"{token}_{old}"
                src.rename(tmp)
                staged[old] = tmp
        for _, old, new in changes:
            tmp = staged.get(old)
            if tmp is None:
                continue
            dest = session_dir(subject, new)
            if dest.exists():
                raise RuntimeError(f"Cannot reindex {subject}: {dest} already exists.")
            tmp.rename(dest)
    except Exception:
        for old, tmp in staged.items():
            original = session_dir(subject, old)
            if tmp.exists() and not original.exists():
                tmp.rename(original)
        raise

    for path, _, new in assignments:
        entries[fingerprint(path)]["meeting"] = new


def subject_overview(groups: dict[str, list[Path]], entries: dict[str, Any]) -> None:
    if not RICH_AVAILABLE:
        print(f"Subjects found: {len(groups)}")
        return

    table = Table(title="ARCHIVE OVERVIEW", show_header=True, header_style="bold bright_cyan", border_style="grey35")
    table.add_column("Subject", style="bright_white")
    table.add_column("Recordings", justify="right")
    table.add_column("Ready", justify="right", style="green")
    table.add_column("Pending", justify="right", style="yellow")
    for subject, files in groups.items():
        done = 0
        for p in files:
            key = fingerprint(p)
            meeting = entries.get(key, {}).get("meeting")
            if isinstance(meeting, int) and (session_dir(subject, meeting) / "transcript.txt").exists():
                done += 1
        table.add_row(short_name(subject, 48), str(len(files)), str(done), str(len(files) - done))
    console.print(table)
    console.print()


def main() -> int:
    header()
    if not ROOT_DIR.exists():
        fail(f"Archive folder not found: {ROOT_DIR}")
        input("\nPress Enter to exit...")
        return 1

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        fail("FFmpeg + ffprobe are required for long-file splitting.")
        print("Install FFmpeg and make sure both ffmpeg.exe and ffprobe.exe are in PATH.")
        input("\nPress Enter to exit...")
        return 1

    if RICH_AVAILABLE:
        console.print(Align.center(Text("Scanning your College Archive...", style="dim bright_white")))
        console.print()

    state = load_state()
    entries = state.setdefault("files", {})
    groups = discover_audio()
    total_audio = sum(len(v) for v in groups.values())
    if not total_audio:
        warn("No audio recordings found anywhere inside College Archive.")
        input("\nPress Enter to exit...")
        return 0

    # Register discovered recordings.
    for subject, files in groups.items():
        for path in files:
            key = fingerprint(path)
            st = path.stat()
            entries.setdefault(key, {
                "subject": subject,
                "name": path.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "status": "pending",
            })
            entries[key]["subject"] = subject
            entries[key]["name"] = path.name
            entries[key]["current_path"] = str(path)

    for subject, files in groups.items():
        reindex_subject(subject, files, entries)

    save_state(state)

    if RICH_AVAILABLE:
        subject_overview(groups, entries)

    pending: list[tuple[str, Path, int, str]] = []
    skipped = 0
    for subject, files in groups.items():
        for path in files:
            key = fingerprint(path)
            info_entry = entries[key]
            meeting = int(info_entry["meeting"])
            mdir = session_dir(subject, meeting)
            transcript = mdir / "transcript.txt"
            if transcript.exists():
                skipped += 1
                info_entry["status"] = "done"
                info_entry["meeting_dir"] = str(mdir)
                continue
            pending.append((subject, path, meeting, key))
            info_entry["status"] = "pending"
            info_entry["meeting_dir"] = str(mdir)

    save_state(state)

    if RICH_AVAILABLE:
        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="bright_cyan", justify="right")
        summary.add_column(style="bright_white")
        summary.add_row("Subjects", str(len(groups)))
        summary.add_row("Recordings found", str(total_audio))
        summary.add_row("Already complete", str(skipped))
        summary.add_row("New recordings", str(len(pending)))
        console.print(Panel.fit(summary, title="SCAN COMPLETE", border_style="bright_blue"))
    else:
        print(f"Subjects found      : {len(groups)}")
        print(f"Audio recordings    : {total_audio}")
        print(f"Already complete    : {skipped}")
        print(f"Recordings left     : {len(pending)}")

    if not pending:
        success("Everything is already transcribed. Add new recordings and run the launcher again.")
        input("\nPress Enter to exit...")
        return 0

    history: list[float] = []
    overall_start = time.monotonic()
    completed = 0

    for pos, (subject, audio, meeting, key) in enumerate(pending, 1):
        info_entry = entries[key]
        mdir = session_dir(subject, meeting)
        mdir.mkdir(parents=True, exist_ok=True)

        # Move original recording into its meeting folder BEFORE splitting.
        moved = mdir / audio.name
        if audio.resolve() != moved.resolve():
            moved = safe_move(audio, moved)
            info_entry["current_path"] = str(moved)
            save_state(state)
        source = moved

        duration = ffprobe_duration(source)
        if duration is None or duration < 1.0:
            info_entry["status"] = "failed"
            info_entry["last_error"] = "The recording is empty or too short to transcribe."
            save_state(state)
            fail(f"{subject} / Pertemuan_{meeting}: invalid or empty audio.")
            input("\nPress Enter to exit...")
            return 2

        log_lines: deque[str] = deque(maxlen=MAX_LOG_LINES)
        log_lines.append("Recording moved into its Pertemuan folder.")
        log_lines.append(f"Duration detected: {fmt_seconds(duration)}")
        history_eta = history.copy()

        if RICH_AVAILABLE:
            console.print()
            console.print(Rule(f"RECORDING {pos}/{len(pending)}  •  {subject}  •  Pertemuan_{meeting}", style="bright_cyan"))
        else:
            print("\n" + "=" * 82)
            print(f"RECORDING {pos}/{len(pending)} | {len(pending)-pos+1} remaining")
            print(f"Subject : {subject}")
            print(f"Meeting : Pertemuan_{meeting}")
            print(f"Audio   : {audio.name}")
            print(f"Date    : {datetime.fromtimestamp(source.stat().st_mtime):%Y-%m-%d %H:%M:%S}")
            print(f"Duration: {fmt_seconds(duration)}")

        try:
            chunk_dir = mdir / CHUNK_DIR_NAME
            chunks = split_audio(source, chunk_dir)
            log_lines.append(f"Prepared {len(chunks)} chunk(s).")

            if len(chunks) == 1 and chunks[0].resolve() == source.resolve():
                if RICH_AVAILABLE:
                    console.print(Panel.fit(
                        f"[bold white]{short_name(source.name, 70)}[/bold white]\n[cyan]No split needed[/cyan]  •  {fmt_seconds(duration)}",
                        title="TRANSCRIBING FULL RECORDING", border_style="cyan"
                    ))
                else:
                    print("Short recording — no split needed.")

                if not transcript_exists(mdir, source):
                    ok, elapsed = transcribe_one(
                        source, mdir, subject, meeting, pos, len(pending),
                        0, 1, duration, history_eta, log_lines
                    )
                    if not ok:
                        raise RuntimeError(f"Whisper failed on {source.name}.")
                    history.append(elapsed)

                txt = mdir / f"{source.stem}.txt"
                body = txt.read_text(encoding="utf-8", errors="replace").strip()
                transcript = mdir / "transcript.txt"
                transcript.write_text(
                    f"Source audio: {source.name}\n"
                    f"Model: Whisper {MODEL}\n"
                    f"Language: {LANGUAGE}\n\n"
                    f"===== FULL RECORDING =====\n{body}\n",
                    encoding="utf-8"
                )
                cleanup_short_whisper_txt(mdir, source)
                log_lines.append("Combined transcript written.")
            else:
                if RICH_AVAILABLE:
                    console.print(Panel.fit(
                        f"[bold white]{len(chunks)} chunks[/bold white]  •  maximum {MAX_CHUNK_MINUTES} minutes each\n"
                        "Cuts are placed near low-volume / silence regions when possible.",
                        title="LONG RECORDING", border_style="bright_blue"
                    ))

                for cidx, chunk in enumerate(chunks, 1):
                    if transcript_exists(chunk_dir, chunk):
                        log_lines.append(f"Chunk {cidx}/{len(chunks)} already complete; reusing it.")
                        continue
                    log_lines.append(f"Starting chunk {cidx}/{len(chunks)}: {chunk.name}")
                    ok, elapsed = transcribe_one(
                        chunk, chunk_dir, subject, meeting, pos, len(pending),
                        cidx - 1, len(chunks), duration, history_eta, log_lines
                    )
                    if not ok:
                        raise RuntimeError(f"Whisper failed on {chunk.name}.")
                    history.append(elapsed)
                    log_lines.append(f"Chunk {cidx}/{len(chunks)} complete in {fmt_seconds(elapsed)}.")

                transcript = combine_transcripts(chunks, mdir, source.name)
                # IMPORTANT: cleanup happens only after the final merged transcript exists.
                cleanup_chunks(chunk_dir)
                log_lines.append("Merged transcript saved; temporary chunks deleted.")

            info_entry["status"] = "done"
            info_entry["completed_at"] = datetime.now().isoformat(timespec="seconds")
            info_entry["transcript"] = str(transcript)
            info_entry["chunks"] = len(chunks)
            info_entry["source"] = str(source)
            save_state(state)
            completed += 1

            success(f"{subject} / Pertemuan_{meeting} complete -> {transcript.name}")

        except Exception as exc:
            info_entry["status"] = "failed"
            info_entry["last_error"] = str(exc)
            info_entry["last_attempt"] = datetime.now().isoformat(timespec="seconds")
            info_entry["source"] = str(source)
            save_state(state)
            fail(f"{subject} / Pertemuan_{meeting}: {exc}")
            print("The recording and any unfinished chunk files were kept so the next run can resume.")
            input("\nPress Enter to exit...")
            return 2

    total_elapsed = time.monotonic() - overall_start

    if RICH_AVAILABLE:
        final = Table.grid(padding=(0, 1))
        final.add_column(style="bright_cyan", justify="right")
        final.add_column(style="bright_white")
        final.add_row("Processed", f"{completed} recording(s)")
        final.add_row("Total time", fmt_seconds(total_elapsed))
        final.add_row("Archive", str(ROOT_DIR))
        final.add_row("Output", "Original audio + combined transcript.txt")
        final.add_row("Cleanup", "Temporary chunk audio/TXT removed after successful merge")
        console.print(Panel.fit(final, title="ALL NEW TRANSCRIPTIONS COMPLETE", border_style="green", padding=(1, 2)))
        console.print()
        console.print(Text("Run the same launcher whenever you add new recordings.", style="dim bright_white"))
    else:
        print("\n" + "=" * 82)
        print("ALL NEW TRANSCRIPTIONS COMPLETE")
        print(f"Processed        : {completed} recording(s)")
        print(f"Total time       : {fmt_seconds(total_elapsed)}")
        print(f"Archive          : {ROOT_DIR}")
        print("Temporary audio shards and per-chunk TXT files were deleted after successful merging.")
        print("Run this again whenever you add new recordings.")
        print("=" * 82)

    input("\nPress Enter to exit...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
