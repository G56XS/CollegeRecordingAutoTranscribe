"""
auto_transcribe.py
-------------------
Point this at your whole "College Archive" folder (or any parent folder
that contains one subfolder per subject). It walks the entire tree,
finds every new (untranscribed) audio file in every subject, and
transcribes them with Whisper (medium model, Indonesian, CUDA).

Long recordings are handled safely:
  1. The original file is moved into its own working folder FIRST.
  2. If it's long, it's cut into ~15-minute pieces at quiet points in
     the audio (so a cut never lands in the middle of a sentence).
  3. Each piece is transcribed separately (and progress is resumable --
     if the program is interrupted, re-running it picks up where it
     left off instead of redoing finished pieces).
  4. The pieces' transcripts are stitched back into one .txt file.

Once a recording is fully transcribed, its folder gets renamed into
"Pertemuan_N" (oldest recording first, numbered independently per
subject folder, continuing on from whatever already exists there).

USAGE:
    python auto_transcribe.py "C:\\path\\to\\College Archive"

If no folder is given, it uses the folder the script itself lives in.
Run it again any time you add new audio files anywhere in the tree.

Requirements (install once):
    pip install -U openai-whisper rich
    (a CUDA-enabled PyTorch build, and ffmpeg on PATH)
"""

import os
import re
import sys
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import (
        Progress, SpinnerColumn, TextColumn, BarColumn,
        TimeElapsedColumn, TimeRemainingColumn, TaskProgressColumn,
    )
    from rich import box
except ImportError:
    print("This program needs the 'rich' package for its interface.")
    print("Install it with:\n    pip install rich")
    input("\nPress Enter to close...")
    sys.exit(1)

console = Console()

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".mp4", ".aac", ".flac", ".ogg", ".m4b", ".wma"}
MODEL_NAME = "medium"
LANGUAGE = "Indonesian"
DEVICE = "cuda"

PERTEMUAN_RE = re.compile(r"^Pertemuan_\d+$")
CHUNKS_DIRNAME = "_chunks"
CHUNK_RE = re.compile(r"^chunk_(\d{4})\.wav$")

# --- chunking tuning knobs ---------------------------------------------
TARGET_CHUNK_SECONDS = 15 * 60      # aim for ~15 min per piece
SPLIT_THRESHOLD_SECONDS = TARGET_CHUNK_SECONDS * 1.2   # don't bother splitting under this
SILENCE_SEARCH_WINDOW = 3 * 60      # look +/-3 min around the target point for a quiet spot
MIN_LAST_CHUNK_SECONDS = 2 * 60     # don't leave a tiny trailing piece; merge it back in
SILENCE_NOISE_DB = -35              # how quiet counts as "silence"
SILENCE_MIN_DUR = 0.4               # minimum length of a quiet spot to count, in seconds
# ------------------------------------------------------------------------


def get_base_dir() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    return Path(__file__).resolve().parent


def format_time(seconds):
    if seconds is None or seconds < 0:
        return "unknown"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def get_audio_duration(path: Path):
    """Seconds, via ffprobe. None if it can't be read."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def walk_dirs(base_dir: Path):
    """Every directory under base_dir (incl. base_dir), skipping anything
    already organized into a Pertemuan_N folder."""
    for dirpath, dirnames, _ in os.walk(base_dir):
        dirnames[:] = [d for d in dirnames if not PERTEMUAN_RE.match(d)]
        yield Path(dirpath)


# ---------------------------------------------------------------- discovery

def find_audio_in_work_folder(d: Path):
    """If d is a working folder (holds the original moved-in recording),
    return that audio file's path."""
    for ext in AUDIO_EXTS:
        p = d / f"{d.name}{ext}"
        if p.exists():
            return p
    return None


def is_work_folder(d: Path) -> bool:
    if PERTEMUAN_RE.match(d.name):
        return False
    return find_audio_in_work_folder(d) is not None


def discover(base_dir: Path):
    """
    Returns:
      new_raw       - loose audio files not yet moved into a working folder
      pending       - working folders whose transcript isn't finished yet
      done          - working folders whose transcript IS finished
                       (waiting to be filed into Pertemuan_N)
    """
    new_raw, pending, done = [], [], []

    for d in walk_dirs(base_dir):
        if is_work_folder(d):
            final_txt = d / f"{d.name}.txt"
            if final_txt.exists():
                done.append(d)
            else:
                pending.append(d)
        else:
            for entry in sorted(d.iterdir()):
                if entry.is_file() and entry.suffix.lower() in AUDIO_EXTS:
                    if entry.with_suffix(".txt").exists():
                        continue  # old-style loose transcript, leave it alone
                    if (d / entry.stem).exists():
                        continue  # already has a working folder
                    new_raw.append(entry)

    return new_raw, pending, done


def setup_work_folder(raw_file: Path) -> Path:
    """Move a freshly-found audio file into its own same-named folder."""
    folder = raw_file.parent / raw_file.stem
    folder.mkdir(exist_ok=True)
    dest = folder / raw_file.name
    shutil.move(str(raw_file), str(dest))
    return folder


# ------------------------------------------------------------------ splitting

def detect_silences(path: Path):
    """Returns [(start_sec, end_sec), ...] of quiet stretches, via ffmpeg."""
    cmd = [
        "ffmpeg", "-i", str(path),
        "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_DUR}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        stderr = proc.stderr
    except Exception:
        return []
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", stderr)]
    return list(zip(starts, ends))


def choose_split_points(duration, silences):
    """Pick cut timestamps, preferring the middle of a nearby quiet stretch
    over a hard cut, and never leaving a too-short trailing piece."""
    remaining = [(s + e) / 2 for (s, e) in silences]
    points = []
    t = TARGET_CHUNK_SECONDS
    while t < duration - MIN_LAST_CHUNK_SECONDS:
        candidates = [
            m for m in remaining
            if (t - SILENCE_SEARCH_WINDOW) <= m <= (t + SILENCE_SEARCH_WINDOW)
            and (not points or m > points[-1] + 30)
        ]
        chosen = min(candidates, key=lambda c: abs(c - t)) if candidates else t
        if chosen in remaining:
            remaining.remove(chosen)  # don't reuse the same quiet spot twice
        if chosen >= duration - MIN_LAST_CHUNK_SECONDS:
            break
        points.append(chosen)
        t = chosen + TARGET_CHUNK_SECONDS
    return points


def cut_chunk(src: Path, start: float, duration, dest: Path):
    cmd = ["ffmpeg", "-y", "-i", str(src), "-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-ar", "16000", "-ac", "1", "-vn", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def existing_chunks(chunks_dir: Path):
    if not chunks_dir.exists():
        return []
    found = []
    for f in chunks_dir.iterdir():
        m = CHUNK_RE.match(f.name)
        if m:
            found.append((int(m.group(1)), f))
    found.sort(key=lambda x: x[0])
    return [f for _, f in found]


def split_into_chunks(folder: Path, audio_path: Path):
    """Returns a sorted list of chunk wav paths, splitting only if needed.
    Reuses chunks already on disk from a previous interrupted run."""
    chunks_dir = folder / CHUNKS_DIRNAME
    already = existing_chunks(chunks_dir)
    if already:
        return already

    duration = get_audio_duration(audio_path)
    if duration is None or duration <= SPLIT_THRESHOLD_SECONDS:
        return [audio_path]  # short enough, no splitting needed

    with console.status(
        f"[bold blue]Analyzing '{audio_path.name}' ({format_time(duration)}) for quiet spots to split at...[/bold blue]",
        spinner="dots",
    ):
        silences = detect_silences(audio_path)
        split_points = choose_split_points(duration, silences)
        boundaries = [0.0] + split_points + [duration]

        chunks_dir.mkdir(exist_ok=True)
        chunk_paths = []
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            is_last = (i == len(boundaries) - 2)
            dest = chunks_dir / f"chunk_{i + 1:04d}.wav"
            cut_chunk(audio_path, start, None if is_last else (end - start), dest)
            chunk_paths.append(dest)

    console.print(
        f"  [dim]↳ split '{audio_path.name}' into "
        f"[bold]{len(chunk_paths)}[/bold] piece(s) at quiet points[/dim]"
    )
    return chunk_paths


def merge_transcripts(folder: Path, chunk_paths):
    """Combine each chunk's .txt into the folder's final transcript."""
    audio_path = find_audio_in_work_folder(folder)
    final_txt = folder / f"{folder.name}.txt"

    if len(chunk_paths) == 1 and chunk_paths[0] == audio_path:
        # wasn't split -- its transcript is already the final one
        src_txt = audio_path.with_suffix(".txt")
        if src_txt != final_txt:
            shutil.move(str(src_txt), str(final_txt))
        return

    parts = []
    for c in chunk_paths:
        txt = c.with_suffix(".txt")
        parts.append(txt.read_text(encoding="utf-8").strip())
    final_txt.write_text("\n\n".join(parts).strip() + "\n", encoding="utf-8")

    shutil.rmtree(folder / CHUNKS_DIRNAME, ignore_errors=True)
    console.print(f"  [dim]↳ stitched pieces into[/dim] [bold]{final_txt.name}[/bold] [dim]and cleaned up the shards[/dim]")


# --------------------------------------------------------------- organizing

def find_existing_pertemuan_numbers(subject_dir: Path):
    nums = []
    for d in subject_dir.iterdir():
        if d.is_dir() and PERTEMUAN_RE.match(d.name):
            nums.append(int(d.name.split("_")[1]))
    return nums


def organize(base_dir: Path):
    _, _, done_folders = discover(base_dir)
    if not done_folders:
        return

    grouped = {}
    for folder in done_folders:
        grouped.setdefault(folder.parent, []).append(folder)

    table = Table(title="Filed into Pertemuan folders", box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Subject")
    table.add_column("Folder")
    table.add_column("Recording")
    table.add_column("Date", justify="right")

    for subject_dir, folders in grouped.items():
        audio_of = {f: find_audio_in_work_folder(f) for f in folders}
        folders.sort(key=lambda f: audio_of[f].stat().st_mtime)

        next_num = (max(find_existing_pertemuan_numbers(subject_dir), default=0)) + 1

        for f in folders:
            dest_name = f"Pertemuan_{next_num}"
            recorded_date = datetime.fromtimestamp(audio_of[f].stat().st_mtime).strftime("%Y-%m-%d")
            shutil.move(str(f), str(subject_dir / dest_name))
            table.add_row(subject_dir.name, f"[green]{dest_name}[/green]", audio_of[f].name, recorded_date)
            next_num += 1

    console.print(table)


# ------------------------------------------------------------- transcribing

def build_job_queue(pending_folders):
    """One job per audio piece that still needs a transcript.
    Each job: (folder, chunk_path, duration_or_None, part_no, total_parts)"""
    jobs = []
    for folder in pending_folders:
        audio_path = find_audio_in_work_folder(folder)
        if audio_path is None:
            continue
        chunk_paths = split_into_chunks(folder, audio_path)
        total = len(chunk_paths)
        for i, c in enumerate(chunk_paths, start=1):
            txt_path = c.with_suffix(".txt")
            if txt_path.exists():
                continue  # already transcribed (resumed run)
            duration = get_audio_duration(c)
            jobs.append((folder, c, duration, i, total))
    return jobs


def transcribe_all(base_dir: Path):
    new_raw, pending, _ = discover(base_dir)

    if not new_raw and not pending:
        console.print(Panel.fit(
            "[bold green]Nothing new to transcribe[/bold green]\n[dim]Everything is already up to date.[/dim]",
            border_style="green", box=box.ROUNDED,
        ))
        return

    if new_raw:
        table = Table(title="New recordings found", box=box.SIMPLE_HEAVY, header_style="bold cyan")
        table.add_column("Subject")
        table.add_column("Recording")
        for raw in new_raw:
            table.add_row(raw.parent.name, raw.name)
        console.print(table)

    for raw in new_raw:
        folder = setup_work_folder(raw)
        pending.append(folder)

    console.print("[dim]Preparing files (moving into place, splitting long ones at quiet points)...[/dim]\n")
    jobs = build_job_queue(pending)

    if not jobs:
        console.print("[green]Nothing left to transcribe — previously finished pieces were skipped.[/green]")
        return

    total = len(jobs)
    total_known_duration = sum((d or 0) for (_, _, d, _, _) in jobs)

    console.print(Panel.fit(
        f"[bold]{total}[/bold] audio piece(s) queued\n"
        f"[dim]~{format_time(total_known_duration)} of audio to process[/dim]",
        border_style="blue", box=box.ROUNDED,
    ))

    with console.status(f"[bold blue]Loading Whisper '{MODEL_NAME}' model on {DEVICE}...[/bold blue]", spinner="dots"):
        import whisper
        model = whisper.load_model(MODEL_NAME, device=DEVICE)
    console.print(f"[green]✓[/green] Model loaded on {DEVICE}\n")

    processed_duration = 0.0
    processed_time = 0.0
    folders_touched = set()

    progress_total = total_known_duration if total_known_duration > 0 else None

    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=36),
        TaskProgressColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("elapsed •"),
        TimeRemainingColumn(),
        TextColumn("left"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Starting...", total=progress_total)

        for idx, (folder, chunk_path, duration, part_no, total_parts) in enumerate(jobs, start=1):
            remaining_files = total - idx
            audio_name = find_audio_in_work_folder(folder).name
            part_note = f" (part {part_no}/{total_parts})" if total_parts > 1 else ""

            desc = (
                f"[{idx}/{total}] [cyan]{folder.parent.name}[/cyan] / "
                f"[bold]{audio_name}{part_note}[/bold]  [dim]({remaining_files} left)[/dim]"
            )
            progress.update(task, description=desc)

            start_t = time.time()
            result = model.transcribe(str(chunk_path), language=LANGUAGE, verbose=False)
            elapsed = time.time() - start_t

            chunk_path.with_suffix(".txt").write_text(result["text"].strip(), encoding="utf-8")

            if duration:
                processed_duration += duration
                processed_time += elapsed
                progress.advance(task, duration)

            progress.console.print(
                f"  [green]✓[/green] {audio_name}{part_note} "
                f"[dim]— done in {format_time(elapsed)}[/dim]"
            )
            folders_touched.add(folder)

        progress.update(task, description="[bold green]All pieces transcribed[/bold green]")

    console.print()
    for folder in folders_touched:
        chunk_paths = existing_chunks(folder / CHUNKS_DIRNAME)
        if not chunk_paths:
            chunk_paths = [find_audio_in_work_folder(folder)]
        if all(c.with_suffix(".txt").exists() for c in chunk_paths):
            merge_transcripts(folder, chunk_paths)
    console.print()


def main():
    console.print(Panel.fit(
        "[bold cyan]Auto-Transcriber[/bold cyan]\n"
        f"[dim]Whisper '{MODEL_NAME}' • Bahasa Indonesia • {DEVICE.upper()}[/dim]",
        border_style="cyan", box=box.ROUNDED,
    ))

    base_dir = get_base_dir()
    if not base_dir.is_dir():
        console.print(f"[bold red]ERROR:[/bold red] '{base_dir}' is not a valid folder.")
        return

    console.print(f"[bold]Folder:[/bold] {base_dir}\n")

    transcribe_all(base_dir)
    organize(base_dir)

    console.print(Panel.fit("[bold green]All done![/bold green]", border_style="green", box=box.ROUNDED))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        console.print(f"\n[bold red]Something went wrong:[/bold red] {e}")
    input("\nPress Enter to close...")
