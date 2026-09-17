#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
College Archive Transcriber
===========================

One script that watches your whole archive tree, finds every recording that
doesn't have a transcript yet, transcribes it with Whisper, and files it into
per-subject `Pertemuan_N` folders.

Design notes (why it works the way it does):

* The model is loaded ONCE and reused for every file. Calling the `whisper`
  command line per chunk reloads ~1.5 GB of weights each time; this doesn't.
* Splitting is done with ffmpeg, not by loading the file into RAM. A 3-hour
  recording costs a few MB of memory instead of a few GB.
* Cuts land in the middle of a silent stretch near the target length, so a
  sentence is never chopped in half.
* Chunks are cut, transcribed, and deleted ONE AT A TIME, and each chunk's
  result is saved as JSON the moment it finishes. Kill the program at any
  point and re-running picks up exactly where it stopped.
* Chunk timestamps are shifted by the chunk's real start offset, so the
  timestamped transcript and the .srt are correct for the whole recording.

Usage:
    python archive_transcriber.py "C:\\Users\\Iven\\Documents\\College Archive"
    python archive_transcriber.py            (uses the script's own folder)
    python archive_transcriber.py --help     (all options)

Install once:
    pip install -U openai-whisper rich
    ffmpeg + ffprobe on PATH
    optional: pip install plyer   # desktop notification when a run finishes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Optional dependencies. The program degrades gracefully without any of them.
# --------------------------------------------------------------------------
try:
    from rich.align import Align
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
    from rich import box
    HAS_RICH = True
except ImportError:  # pragma: no cover
    HAS_RICH = False

try:
    from plyer import notification as _desktop_notification
except Exception:  # pragma: no cover
    _desktop_notification = None


# ==========================================================================
# CONFIG (every one of these is also a command-line flag)
# ==========================================================================
DEFAULTS = {
    "model": "medium",
    "language": "id",
    "device": "auto",              # auto | cuda | cpu
    "chunk_minutes": 15.0,         # target length of each piece
    "split_over_minutes": 18.0,    # leave anything shorter than this alone
    "silence_window_minutes": 4.0, # how far to hunt for a quiet spot
    "min_chunk_minutes": 2.0,      # never leave a sliver at the end
    "silence_db": -35,             # what counts as "quiet"
    "silence_min_dur": 0.45,       # seconds of quiet needed to be a cut point
}

AUDIO_EXTS = {
    ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma",
    ".m4b", ".mp4", ".mkv", ".webm", ".amr", ".3gp",
}

PERTEMUAN_RE = re.compile(r"^Pertemuan_(\d+)$", re.IGNORECASE)
WORK_DIR_NAME = "_work"
SKIP_DIR_NAMES = {WORK_DIR_NAME, "__pycache__", ".git", "node_modules", "$RECYCLE.BIN"}

TRANSCRIPT_NAME = "transcript.txt"
TIMESTAMPED_NAME = "transcript_timestamped.txt"
SUBTITLE_NAME = "transcript.srt"
INDEX_NAME = "_Archive_Index.md"

# A short prompt in the target language nudges Whisper into producing proper
# punctuation and capitalisation instead of one endless lowercase run-on.
INITIAL_PROMPTS = {
    "id": "Berikut adalah rekaman perkuliahan dalam Bahasa Indonesia, "
          "lengkap dengan tanda baca yang rapi.",
    "en": "The following is a university lecture recording, "
          "transcribed with proper punctuation.",
}

THEME = {
    "brand": "bold bright_cyan",
    "ok": "bold green",
    "warn": "bold yellow",
    "bad": "bold red",
    "dim": "bright_black",
}

console = Console(highlight=False) if HAS_RICH else None


# ==========================================================================
# Small helpers
# ==========================================================================
def fmt_duration(seconds) -> str:
    """3725 -> '1h 02m 05s'. None -> 'unknown'."""
    if seconds is None or seconds != seconds or seconds < 0:
        return "unknown"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def srt_stamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def clock_stamp(seconds: float) -> str:
    total = int(max(0.0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def ellipsize(value: str, width: int = 40) -> str:
    value = str(value)
    return value if len(value) <= width else value[: width - 1] + "…"


def human_size(num_bytes: int) -> str:
    mb = num_bytes / (1024 * 1024)
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def run_quiet(cmd: list[str]) -> tuple[int, str]:
    """Run a command, swallow its window on Windows, return (code, output)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=flags,
        )
        return proc.returncode, proc.stdout or ""
    except FileNotFoundError:
        return 127, ""
    except Exception as exc:  # pragma: no cover
        return 1, str(exc)


# --------------------------------------------------------------------------
# Plain-text fallbacks so the whole program works with no Rich installed
# --------------------------------------------------------------------------
MARKUP_RE = re.compile(r"\[/?[a-z_ #0-9]+\]")


def strip_markup(msg: str) -> str:
    return MARKUP_RE.sub("", msg)


def say(msg: str, style: str = "") -> None:
    if HAS_RICH:
        console.print(f"[{style}]{msg}[/{style}]" if style else msg)
    else:
        print(strip_markup(msg))


def ok(msg: str) -> None:
    say(f"[green]✓[/green] {msg}") if HAS_RICH else print(f"[OK] {strip_markup(msg)}")


def warn(msg: str) -> None:
    say(f"[yellow]![/yellow] {msg}") if HAS_RICH else print(f"[!] {strip_markup(msg)}")


def bad(msg: str) -> None:
    say(f"[red]✗[/red] {msg}") if HAS_RICH else print(f"[X] {strip_markup(msg)}")


# ==========================================================================
# ffmpeg / ffprobe layer
# ==========================================================================
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def probe_duration(path: Path):
    if not FFPROBE:
        return None
    code, out = run_quiet([
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    if code != 0:
        return None
    try:
        value = float(out.strip().splitlines()[-1])
        return value if value > 0 else None
    except (ValueError, IndexError):
        return None


def detect_silences(path: Path, noise_db: int, min_dur: float):
    """Returns [(start, end), ...] of quiet stretches, in seconds."""
    if not FFMPEG:
        return []
    code, out = run_quiet([
        FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f", "null", "-",
    ])
    if code not in (0, 1):
        return []
    starts, pairs = [], []
    for line in out.splitlines():
        m = re.search(r"silence_start:\s*(-?[0-9.]+)", line)
        if m:
            starts.append(float(m.group(1)))
        m = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m and starts:
            start = starts.pop(0)
            end = float(m.group(1))
            if end > start:
                pairs.append((start, end))
    return pairs


def plan_segments(duration: float, silences, cfg) -> list[tuple[float, float]]:
    """
    Decide where to cut. Walks forward in `chunk_minutes` steps and, at each
    step, slides to the middle of the nearest silent stretch within
    `silence_window_minutes`. Falls back to a hard cut if the audio has no
    usable pause anywhere near.
    """
    target = cfg.chunk_minutes * 60
    window = cfg.silence_window_minutes * 60
    min_tail = cfg.min_chunk_minutes * 60

    if duration <= cfg.split_over_minutes * 60:
        return [(0.0, duration)]

    midpoints = sorted((s + e) / 2 for s, e in silences)
    segments: list[tuple[float, float]] = []
    start = 0.0

    while duration - start > target + min_tail:
        desired = start + target
        candidates = [
            m for m in midpoints
            if abs(m - desired) <= window and m > start + min_tail
            and m < duration - min_tail
        ]
        cut = min(candidates, key=lambda m: abs(m - desired)) if candidates else desired
        segments.append((start, cut))
        start = cut

    segments.append((start, duration))
    return segments


def cut_segment(src: Path, start: float, end: float, dest: Path) -> bool:
    """Extract one piece as 16 kHz mono wav — exactly what Whisper wants."""
    if not FFMPEG:
        return False
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(src),
        "-t", f"{max(0.05, end - start):.3f}",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dest),
    ]
    code, _ = run_quiet(cmd)
    return code == 0 and dest.exists() and dest.stat().st_size > 1024


# ==========================================================================
# Transcription engines
# ==========================================================================
class Engine:
    name = "engine"

    def transcribe(self, path: Path, on_progress, on_text) -> list[dict]:
        raise NotImplementedError


class OpenAIWhisperEngine(Engine):
    name = "openai-whisper"

    def __init__(self, model_name: str, device: str, language: str):
        import whisper
        self._whisper = whisper
        self.device = device
        self.language = language
        self.model = whisper.load_model(model_name, device=device)

    def transcribe(self, path: Path, on_progress, on_text) -> list[dict]:
        restore = self._hook_progress(on_progress)
        try:
            result = self.model.transcribe(
                str(path),
                language=self.language,
                task="transcribe",
                verbose=False,
                fp16=(self.device == "cuda"),
                condition_on_previous_text=False,  # stops runaway loops
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                compression_ratio_threshold=2.4,
                no_speech_threshold=0.6,
                initial_prompt=INITIAL_PROMPTS.get(self.language),
            )
        finally:
            restore()

        segments = [
            {"start": float(s["start"]), "end": float(s["end"]),
             "text": str(s["text"]).strip()}
            for s in result.get("segments", []) if str(s.get("text", "")).strip()
        ]
        if not segments and str(result.get("text", "")).strip():
            segments = [{"start": 0.0, "end": 0.0, "text": result["text"].strip()}]
        if segments:
            on_text(segments[-1]["text"])
        return segments

    def _hook_progress(self, on_progress):
        """
        openai-whisper drives an internal tqdm bar over audio frames. We swap
        in a silent subclass that reports the fraction back to our own UI, so
        the progress bar moves *inside* a chunk instead of jumping per chunk.
        Entirely best-effort: if whisper's internals change, we just lose the
        fine-grained updates.
        """
        try:
            import whisper.transcribe as wt
            original = wt.tqdm.tqdm

            class Hooked(original):  # type: ignore[misc, valid-type]
                def __init__(self, *args, **kwargs):
                    # disable=True would also stop tqdm from counting (its
                    # update() bails out immediately when disabled), which
                    # is why this bar previously never moved — no-op the
                    # display instead so counting still works.
                    super().__init__(*args, **kwargs)

                def display(self, *args, **kwargs):
                    return

                def update(self, n=1):
                    super().update(n)
                    try:
                        if self.total:
                            on_progress(min(1.0, self.n / self.total))
                    except Exception:
                        pass

            wt.tqdm.tqdm = Hooked
            return lambda: setattr(wt.tqdm, "tqdm", original)
        except Exception:
            return lambda: None


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def load_engine(cfg) -> Engine:
    """
    Always openai-whisper. faster-whisper used to be tried first here, but it
    pulls its own separately-hosted copy of the model from Hugging Face Hub
    (not the same file as openai-whisper's, even for the same model name),
    which is exactly what caused the confusing extra download and the
    hf_xet/symlink warnings. Plain openai-whisper doesn't have that problem
    and is what's proven reliable, so that's the only engine now.
    """
    device = pick_device(cfg.device)
    return OpenAIWhisperEngine(cfg.model, device, cfg.language)


def hook_download_progress():
    """
    openai-whisper fetches model weights with a plain tqdm bar buried inside
    its own downloader. Left alone that bar either prints raw and ugly, or —
    if it happens to fire while some other Rich Live region is open — gets
    swallowed entirely, which is exactly what makes a first-time model
    download look like a silent hang.

    This temporarily replaces the global tqdm class with one that reports into
    our own Rich progress bar instead, so a download always has visible
    progress: file name, size, percentage, ETA. One bar per file, appearing
    only while something is actually downloading. Restores the original tqdm
    class afterwards no matter what happens.
    """
    if not HAS_RICH:
        return lambda: None
    try:
        import tqdm as tqdm_module
        original = tqdm_module.tqdm

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[cyan]{task.fields[label]}"),
            BarColumn(),
            TextColumn("[bold]{task.percentage:>5.1f}%"),
            TextColumn("[bright_black]{task.fields[size]}"),
            TimeRemainingColumn(),
            console=console, transient=True,
        )
        started = {"live": False}

        class Hooked(original):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                # NOTE: disable=True would stop tqdm from counting at all
                # (tqdm.update() returns immediately when disabled, so self.n
                # never advances) — we only want to suppress the *printed*
                # bar, not the counting, so we no-op display() instead.
                super().__init__(*args, **kwargs)
                if not started["live"]:
                    progress.start()
                    started["live"] = True
                total = self.total or 0
                label = ellipsize(str(kwargs.get("desc") or "Downloading model"), 34)
                self._dash_task = progress.add_task(
                    "dl", label=label,
                    size=human_size(total) if total else "…", total=total or 1,
                )

            def display(self, *args, **kwargs):
                return  # suppress tqdm's own printed bar; Rich renders ours

            def update(self, n=1):
                super().update(n)
                try:
                    progress.update(self._dash_task, completed=self.n,
                                    size=human_size(self.total) if self.total else "…")
                except Exception:
                    pass

            def close(self):
                super().close()
                try:
                    progress.remove_task(self._dash_task)
                except Exception:
                    pass

        tqdm_module.tqdm = Hooked
        patched_auto = None
        try:
            import tqdm.auto as tqdm_auto
            patched_auto = tqdm_auto.tqdm
            tqdm_auto.tqdm = Hooked
        except Exception:
            pass

        def restore():
            tqdm_module.tqdm = original
            if patched_auto is not None:
                try:
                    import tqdm.auto as tqdm_auto
                    tqdm_auto.tqdm = patched_auto
                except Exception:
                    pass
            if started["live"]:
                progress.stop()

        return restore
    except Exception:
        return lambda: None


# ==========================================================================
# Discovery
# ==========================================================================
@dataclass
class Task:
    audio: Path              # where the recording currently lives
    parent: Path             # the subject folder it belongs to
    meeting: int             # its Pertemuan number
    folder: Path             # Pertemuan_N folder (may not exist yet)
    duration: float | None = None
    resumed: bool = False
    chunks_total: int = 0
    chunks_done: int = 0
    elapsed: float = 0.0
    status: str = "pending"


def is_skippable(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def pick_main_audio(folder: Path):
    """The recording inside a Pertemuan folder (biggest audio file wins)."""
    files = [f for f in folder.iterdir()
             if f.is_file() and f.suffix.lower() in AUDIO_EXTS]
    return max(files, key=lambda f: f.stat().st_size) if files else None


def discover(root: Path) -> tuple[list[Task], int]:
    """Returns (pending tasks, count of already-finished meetings)."""
    unfinished: list[Task] = []
    fresh: dict[Path, list[Path]] = {}
    used: dict[Path, set[int]] = {}
    finished = 0

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not is_skippable(d))

        match = PERTEMUAN_RE.match(here.name)
        if match:
            dirnames[:] = []  # never descend into an organised meeting
            number = int(match.group(1))
            used.setdefault(here.parent, set()).add(number)
            audio = pick_main_audio(here)
            if audio is None:
                continue
            if (here / TRANSCRIPT_NAME).exists():
                finished += 1
                continue
            unfinished.append(Task(audio=audio, parent=here.parent,
                                   meeting=number, folder=here, resumed=True))
            continue

        for name in sorted(filenames):
            candidate = here / name
            if candidate.suffix.lower() in AUDIO_EXTS:
                fresh.setdefault(here, []).append(candidate)

    # Assign meeting numbers to new recordings: oldest recording gets the
    # lowest number, continuing from whatever already exists in that subject.
    new_tasks: list[Task] = []
    for parent, files in fresh.items():
        taken = used.setdefault(parent, set())
        for folder in parent.iterdir():
            m = PERTEMUAN_RE.match(folder.name) if folder.is_dir() else None
            if m:
                taken.add(int(m.group(1)))
        next_number = (max(taken) if taken else 0) + 1
        for audio in sorted(files, key=lambda f: (f.stat().st_mtime, f.name.lower())):
            while next_number in taken:
                next_number += 1
            taken.add(next_number)
            new_tasks.append(Task(audio=audio, parent=parent, meeting=next_number,
                                  folder=parent / f"Pertemuan_{next_number}"))
            next_number += 1

    tasks = unfinished + new_tasks
    for task in tasks:
        task.duration = probe_duration(task.audio)
    return tasks, finished


def safe_move(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dest.resolve():
        return dest
    if dest.exists():
        if dest.stat().st_size == src.stat().st_size:
            src.unlink()
            return dest
        stem, suffix, i = dest.stem, dest.suffix, 2
        while dest.exists():
            dest = dest.with_name(f"{stem}_{i}{suffix}")
            i += 1
    shutil.move(str(src), str(dest))
    return dest


# ==========================================================================
# Output writers
# ==========================================================================
def write_outputs(folder: Path, audio: Path, segments: list[dict],
                  duration, cfg, engine_name: str) -> None:
    subject = folder.parent.name
    recorded = datetime.fromtimestamp(audio.stat().st_mtime)

    paragraphs, buffer = [], []
    for seg in segments:
        buffer.append(seg["text"])
        if seg["text"].endswith((".", "?", "!")) and len(" ".join(buffer)) > 350:
            paragraphs.append(" ".join(buffer))
            buffer = []
    if buffer:
        paragraphs.append(" ".join(buffer))
    body = "\n\n".join(p.strip() for p in paragraphs if p.strip())

    header = ""
    if not cfg.no_header:
        header = (
            f"# {subject} — {folder.name}\n"
            f"Recording : {audio.name}\n"
            f"Recorded  : {recorded:%A, %d %B %Y, %H:%M}\n"
            f"Duration  : {fmt_duration(duration)}\n"
            f"Engine    : {engine_name} · {cfg.model} · {cfg.language}\n"
            f"{'-' * 64}\n\n"
        )
    (folder / TRANSCRIPT_NAME).write_text(header + body + "\n", encoding="utf-8")

    lines = [f"[{clock_stamp(s['start'])} → {clock_stamp(s['end'])}]  {s['text']}"
             for s in segments]
    (folder / TIMESTAMPED_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not cfg.no_srt:
        blocks = []
        for i, s in enumerate(segments, 1):
            blocks.append(
                f"{i}\n{srt_stamp(s['start'])} --> {srt_stamp(max(s['end'], s['start'] + 0.2))}\n{s['text']}\n"
            )
        (folder / SUBTITLE_NAME).write_text("\n".join(blocks), encoding="utf-8")


def rebuild_index(root: Path) -> Path | None:
    """A one-page map of the whole archive, regenerated after every run."""
    rows: dict[str, list[tuple[int, Path]]] = {}
    for dirpath, dirnames, _ in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_skippable(d)]
        m = PERTEMUAN_RE.match(here.name)
        if m:
            dirnames[:] = []
            rows.setdefault(str(here.parent.relative_to(root)) or ".", []).append(
                (int(m.group(1)), here))
    if not rows:
        return None

    out = [f"# Archive Index", "",
           f"_Last updated {datetime.now():%d %B %Y, %H:%M}_", ""]
    for subject in sorted(rows):
        out.append(f"## {subject}")
        out.append("")
        out.append("| Meeting | Date | Length | Transcript |")
        out.append("|---|---|---|---|")
        for number, folder in sorted(rows[subject]):
            audio = pick_main_audio(folder)
            when = (datetime.fromtimestamp(audio.stat().st_mtime).strftime("%Y-%m-%d")
                    if audio else "—")
            length = fmt_duration(probe_duration(audio)) if audio else "—"
            done = "✅" if (folder / TRANSCRIPT_NAME).exists() else "⏳ pending"
            out.append(f"| Pertemuan_{number} | {when} | {length} | {done} |")
        out.append("")
    path = root / INDEX_NAME
    path.write_text("\n".join(out), encoding="utf-8")
    return path


# ==========================================================================
# The live dashboard
# ==========================================================================
class Dashboard:
    """Everything the user sees while a run is in flight."""

    def __init__(self, total_audio_seconds: float, total_tasks: int):
        self.total_audio = max(1.0, total_audio_seconds)
        self.total_tasks = total_tasks
        self.done_audio = 0.0
        self.task_index = 0
        self.subject = ""
        self.meeting = ""
        self.filename = ""
        self.duration = None
        self.phase = "starting"
        self.chunk_done = 0
        self.chunk_total = 0
        self.chunk_fraction = 0.0
        self.chunk_audio = 0.0
        self.started = time.monotonic()
        self.log: deque[str] = deque(maxlen=7)
        self.preview: str = ""
        self.live = None

    # ---- plumbing --------------------------------------------------------
    def start(self):
        if HAS_RICH:
            self.live = Live(self.render(), console=console, refresh_per_second=8,
                             transient=False)
            self.live.start()
        return self

    def stop(self):
        if self.live:
            self.live.stop()
            self.live = None

    def refresh(self):
        if self.live:
            self.live.update(self.render())

    def note(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[bright_black]{stamp}[/bright_black]  {message}")
        if not HAS_RICH:
            print("    " + stamp + "  " + strip_markup(message))
        self.refresh()

    # ---- progress maths --------------------------------------------------
    @property
    def processed(self) -> float:
        return self.done_audio + self.chunk_audio * self.chunk_fraction

    @property
    def fraction(self) -> float:
        return min(1.0, self.processed / self.total_audio)

    @property
    def eta(self):
        elapsed = time.monotonic() - self.started
        if self.processed < 30 or elapsed < 5:
            return None
        rate = self.processed / elapsed          # audio-seconds per real second
        return (self.total_audio - self.processed) / max(rate, 1e-6)

    # ---- rendering -------------------------------------------------------
    def render(self):
        left = Table.grid(padding=(0, 2))
        left.add_column(style="bright_cyan", justify="right")
        left.add_column(style="bright_white")
        left.add_row("Subject", ellipsize(self.subject, 34))
        left.add_row("Meeting", self.meeting)
        left.add_row("Recording", ellipsize(self.filename, 34))
        left.add_row("Length", fmt_duration(self.duration))
        left.add_row("Phase", f"[bold yellow]{self.phase}[/bold yellow]")

        right = Table.grid(padding=(0, 2))
        right.add_column(style="bright_cyan", justify="right")
        right.add_column(style="bright_white")
        right.add_row("Queue", f"{self.task_index}/{self.total_tasks}")
        right.add_row("Remaining", f"{max(0, self.total_tasks - self.task_index)} recording(s)")
        right.add_row("Pieces", f"{self.chunk_done}/{self.chunk_total}" if self.chunk_total else "—")
        right.add_row("Audio done", f"{fmt_duration(self.processed)} of {fmt_duration(self.total_audio)}")
        right.add_row("ETA", f"[bold green]{fmt_duration(self.eta)}[/bold green]")

        columns = Table.grid(expand=True)
        columns.add_column(ratio=1)
        columns.add_column(ratio=1)
        columns.add_row(
            Panel(left, title="[bold]NOW TRANSCRIBING[/bold]",
                  border_style="bright_blue", box=box.ROUNDED, padding=(0, 1)),
            Panel(right, title="[bold]PROGRESS[/bold]",
                  border_style="bright_blue", box=box.ROUNDED, padding=(0, 1)),
        )

        width = 46
        filled = int(self.fraction * width)
        bar = Text()
        bar.append("█" * filled, style="bright_cyan")
        bar.append("░" * (width - filled), style="grey30")
        bar.append(f"  {self.fraction * 100:5.1f}%", style="bold bright_white")
        bar.append(f"   elapsed {fmt_duration(time.monotonic() - self.started)}",
                   style="bright_black")

        preview = Text(self.preview or "…", style="italic bright_white", overflow="ellipsis")
        panels = [
            columns,
            Panel(Align.center(bar), border_style="cyan", box=box.ROUNDED, padding=(0, 1)),
            Panel(preview, title="[bold]LIVE TEXT[/bold]", border_style="grey37",
                  box=box.ROUNDED, height=3, padding=(0, 1)),
            Panel(Group(*[Text.from_markup(line) for line in self.log]) if self.log
                  else Text("waiting…", style="dim"),
                  title="[bold]ACTIVITY[/bold]", border_style="grey37",
                  box=box.ROUNDED, height=9, padding=(0, 1)),
        ]
        return Group(*panels)


# ==========================================================================
# Processing one recording
# ==========================================================================
def process_task(task: Task, engine: Engine, cfg, dash: Dashboard) -> None:
    task.folder.mkdir(parents=True, exist_ok=True)

    # 1. The original recording moves into its meeting folder FIRST, so an
    #    interruption can never leave it orphaned halfway through.
    moved = safe_move(task.audio, task.folder / task.audio.name)
    if moved != task.audio:
        dash.note(f"moved into [bold]{task.folder.name}/[/bold]")
    task.audio = moved
    if task.duration is None:
        task.duration = probe_duration(moved)
    dash.duration = task.duration
    dash.filename = moved.name

    work = task.folder / WORK_DIR_NAME
    work.mkdir(exist_ok=True)
    plan_file = work / "plan.json"

    # 2. Work out the cut points once, then remember them across restarts.
    if plan_file.exists():
        plan = [tuple(p) for p in json.loads(plan_file.read_text(encoding="utf-8"))]
        dash.note(f"resuming an earlier run · {len(plan)} piece(s) planned")
    else:
        if task.duration and task.duration > cfg.split_over_minutes * 60 and FFMPEG:
            dash.phase = "finding quiet spots to cut at"
            dash.refresh()
            silences = detect_silences(task.audio, cfg.silence_db, cfg.silence_min_dur)
            plan = plan_segments(task.duration, silences, cfg)
            dash.note(f"split into [bold]{len(plan)}[/bold] piece(s) "
                      f"at {len(silences)} detected pause(s)")
        else:
            plan = [(0.0, task.duration or 0.0)]
        plan_file.write_text(json.dumps(plan), encoding="utf-8")

    task.chunks_total = len(plan)
    dash.chunk_total = len(plan)
    single = len(plan) == 1

    # 3. Cut → transcribe → save → delete, one piece at a time.
    all_segments: list[dict] = []
    started = time.monotonic()

    for index, (start, end) in enumerate(plan, 1):
        piece_json = work / f"piece_{index:03d}.json"
        dash.chunk_done = index - 1
        dash.chunk_audio = max(1.0, end - start)
        dash.chunk_fraction = 0.0

        if piece_json.exists():
            all_segments.extend(json.loads(piece_json.read_text(encoding="utf-8")))
            dash.note(f"piece {index}/{len(plan)} already done — reused")
            dash.done_audio += dash.chunk_audio
            dash.chunk_done = index
            continue

        if single:
            source = task.audio
        else:
            dash.phase = f"cutting piece {index}/{len(plan)}"
            dash.refresh()
            source = work / f"piece_{index:03d}.wav"
            if not source.exists() and not cut_segment(task.audio, start, end, source):
                raise RuntimeError(f"ffmpeg could not cut piece {index}")

        dash.phase = (f"transcribing piece {index}/{len(plan)}"
                      if not single else "transcribing")
        dash.refresh()

        def on_progress(fraction: float):
            dash.chunk_fraction = fraction
            dash.refresh()

        def on_text(text: str):
            dash.preview = text
            dash.refresh()

        segments = engine.transcribe(source, on_progress, on_text)
        for seg in segments:                      # shift into whole-file time
            seg["start"] += start
            seg["end"] += start

        piece_json.write_text(json.dumps(segments, ensure_ascii=False),
                              encoding="utf-8")
        all_segments.extend(segments)

        if not single and not cfg.keep_chunks:
            source.unlink(missing_ok=True)

        dash.chunk_fraction = 0.0
        dash.done_audio += dash.chunk_audio
        dash.chunk_done = index
        task.chunks_done = index
        if not single:
            dash.note(f"piece {index}/{len(plan)} done "
                      f"[bright_black]({fmt_duration(end - start)} of audio)[/bright_black]")

    # 4. Stitch, write every output format, clean up.
    dash.phase = "writing transcript"
    dash.refresh()
    all_segments.sort(key=lambda s: s["start"])
    write_outputs(task.folder, task.audio, all_segments, task.duration,
                  cfg, engine.name)

    if not cfg.keep_chunks:
        shutil.rmtree(work, ignore_errors=True)

    task.elapsed = time.monotonic() - started
    task.status = "done"
    dash.note(f"[green]✓[/green] [bold]{task.folder.name}[/bold] · "
              f"{len(all_segments)} segments · {fmt_duration(task.elapsed)}")


# ==========================================================================
# Screens
# ==========================================================================
def banner(root: Path, cfg) -> None:
    if not HAS_RICH:
        print("=" * 74)
        print(" COLLEGE ARCHIVE TRANSCRIBER")
        print("=" * 74)
        print(f" Archive : {root}")
        print(f" Engine  : whisper {cfg.model} · {cfg.language} · {pick_device(cfg.device)}")
        print()
        return

    console.print()
    title = Text("COLLEGE  ARCHIVE  TRANSCRIBER", style="bold bright_white")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bright_cyan", justify="right")
    grid.add_column(style="bright_white")
    grid.add_row("Archive", str(root))
    grid.add_row("Engine", f"whisper [bold]{cfg.model}[/bold] · {cfg.language} · "
                           f"{pick_device(cfg.device).upper()}")
    grid.add_row("Splitting", f"~{cfg.chunk_minutes:g} min pieces, cut at silence · "
                              f"resumable · one piece in memory at a time")
    grid.add_row("Output", f"{TRANSCRIPT_NAME} · {TIMESTAMPED_NAME}"
                           + ("" if cfg.no_srt else f" · {SUBTITLE_NAME}"))
    console.print(Panel(Group(Align.center(title), Text(""), grid),
                        border_style="bright_cyan", box=box.DOUBLE, padding=(1, 3)))


def scan_table(tasks: list[Task], finished: int) -> None:
    if not tasks:
        return
    if not HAS_RICH:
        print(f"{len(tasks)} recording(s) to do, {finished} already complete:")
        for t in tasks:
            print(f"  [{t.parent.name}] {t.audio.name} -> Pertemuan_{t.meeting} "
                  f"({fmt_duration(t.duration)})")
        print()
        return

    table = Table(title="Queued recordings", box=box.SIMPLE_HEAVY,
                  header_style="bold bright_cyan", title_style="bold bright_white",
                  expand=True)
    table.add_column("#", justify="right", style="bright_black", width=3)
    table.add_column("Subject", style="bright_white")
    table.add_column("Recording")
    table.add_column("Recorded", justify="center")
    table.add_column("Length", justify="right", style="cyan")
    table.add_column("Size", justify="right", style="bright_black")
    table.add_column("Destination", style="green")

    for i, t in enumerate(tasks, 1):
        recorded = datetime.fromtimestamp(t.audio.stat().st_mtime).strftime("%Y-%m-%d")
        label = f"Pertemuan_{t.meeting}"
        if t.resumed:
            label += " [yellow](resume)[/yellow]"
        table.add_row(str(i), t.parent.name, ellipsize(t.audio.name, 34), recorded,
                      fmt_duration(t.duration), human_size(t.audio.stat().st_size), label)
    console.print(table)

    total = sum(t.duration or 0 for t in tasks)
    console.print(
        f"[bright_black]{len(tasks)} queued · {fmt_duration(total)} of audio · "
        f"{finished} meeting(s) already complete[/bright_black]\n")


def final_report(tasks: list[Task], wall: float, index_path) -> None:
    done = [t for t in tasks if t.status == "done"]
    failed = [t for t in tasks if t.status == "failed"]

    if not HAS_RICH:
        print(f"\nFinished {len(done)} recording(s) in {fmt_duration(wall)}.")
        for t in failed:
            print(f"  FAILED: {t.parent.name}/{t.folder.name}")
        return

    table = Table(box=box.SIMPLE_HEAVY, header_style="bold bright_cyan", expand=True)
    table.add_column("Subject", style="bright_white")
    table.add_column("Meeting", style="green")
    table.add_column("Pieces", justify="center")
    table.add_column("Audio", justify="right", style="cyan")
    table.add_column("Took", justify="right")
    table.add_column("Speed", justify="right", style="bright_black")
    for t in done:
        speed = (f"{(t.duration or 0) / t.elapsed:.1f}× realtime"
                 if t.elapsed > 0 and t.duration else "—")
        table.add_row(t.parent.name, t.folder.name, str(t.chunks_total),
                      fmt_duration(t.duration), fmt_duration(t.elapsed), speed)

    audio_total = sum(t.duration or 0 for t in done)
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bright_cyan", justify="right")
    summary.add_column(style="bright_white")
    summary.add_row("Completed", f"{len(done)} recording(s)")
    summary.add_row("Audio transcribed", fmt_duration(audio_total))
    summary.add_row("Wall clock", fmt_duration(wall))
    if audio_total and wall:
        summary.add_row("Average speed", f"{audio_total / wall:.1f}× realtime")
    if index_path:
        summary.add_row("Index", str(index_path.name))
    if failed:
        summary.add_row("Failed", f"[bold red]{len(failed)}[/bold red] — "
                                  "re-run to retry, nothing was lost")

    console.print()
    if done:
        console.print(table)
    console.print(Panel(summary, title="[bold green]RUN COMPLETE[/bold green]",
                        border_style="green", box=box.DOUBLE, padding=(1, 3)))
    for t in failed:
        bad(f"{t.parent.name}/{t.folder.name}: {t.status_detail}"
            if hasattr(t, "status_detail") else f"{t.parent.name}/{t.folder.name}")


def notify(done: int, wall: float) -> None:
    if not _desktop_notification or done == 0:
        return
    try:
        _desktop_notification.notify(
            title="College Archive Transcriber",
            message=f"{done} recording(s) transcribed in {fmt_duration(wall)}.",
            app_name="Archive Transcriber", timeout=8,
        )
    except Exception:
        pass


# ==========================================================================
# Entry point
# ==========================================================================
def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="archive_transcriber",
        description="Transcribe every new recording in an archive tree with Whisper.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("root", nargs="?", default=None,
                   help="archive folder (default: the folder this script sits in)")
    p.add_argument("--model", default=DEFAULTS["model"],
                   help="tiny, base, small, medium, large-v3, turbo…")
    p.add_argument("--language", default=DEFAULTS["language"])
    p.add_argument("--device", default=DEFAULTS["device"], choices=["auto", "cuda", "cpu"])
    p.add_argument("--chunk-minutes", type=float, default=DEFAULTS["chunk_minutes"])
    p.add_argument("--split-over-minutes", type=float, default=DEFAULTS["split_over_minutes"])
    p.add_argument("--silence-window-minutes", type=float,
                   default=DEFAULTS["silence_window_minutes"])
    p.add_argument("--min-chunk-minutes", type=float, default=DEFAULTS["min_chunk_minutes"])
    p.add_argument("--silence-db", type=int, default=DEFAULTS["silence_db"])
    p.add_argument("--silence-min-dur", type=float, default=DEFAULTS["silence_min_dur"])
    p.add_argument("--keep-chunks", action="store_true",
                   help="keep the temporary pieces instead of deleting them")
    p.add_argument("--no-srt", action="store_true", help="skip the .srt subtitle file")
    p.add_argument("--no-header", action="store_true",
                   help="write a bare transcript with no metadata header")
    p.add_argument("--no-index", action="store_true", help="skip rebuilding the archive index")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be done, then stop")
    p.add_argument("--no-pause", action="store_true",
                   help="don't wait for Enter at the end")
    return p.parse_args(argv)


def main(argv=None) -> int:
    cfg = parse_args(argv or sys.argv[1:])
    root = Path(cfg.root).expanduser().resolve() if cfg.root else Path(__file__).resolve().parent

    banner(root, cfg)

    if not root.is_dir():
        bad(f"'{root}' is not a folder.")
        return 1
    if not FFMPEG or not FFPROBE:
        bad("ffmpeg and ffprobe must be on your PATH — Whisper needs them to "
            "read audio, and this script needs them to split and measure it.")
        say("[bright_black]Download: https://ffmpeg.org/download.html[/bright_black]")
        return 1

    if HAS_RICH:
        with console.status("[bold cyan]Scanning the archive…", spinner="dots"):
            tasks, finished = discover(root)
    else:
        print("Scanning…")
        tasks, finished = discover(root)

    if not tasks:
        msg = (f"Everything is transcribed — {finished} meeting(s) on file.\n"
               "Drop new recordings into any subject folder and run this again.")
        if HAS_RICH:
            console.print(Panel(msg, border_style="green", box=box.ROUNDED,
                                title="[bold green]NOTHING TO DO[/bold green]",
                                padding=(1, 3)))
        else:
            print(msg)
        if not cfg.no_index:
            rebuild_index(root)
        return 0

    scan_table(tasks, finished)

    if cfg.dry_run:
        say("[yellow]Dry run — nothing was moved or transcribed.[/yellow]")
        return 0

    device_label = pick_device(cfg.device).upper()
    say(f"[cyan]Loading Whisper[/cyan] [bold]{cfg.model}[/bold] on [bold]{device_label}[/bold]"
        f"  [bright_black](only downloads on first use — cached after that)[/bright_black]")
    restore_dl = hook_download_progress()
    try:
        engine = load_engine(cfg)
    except Exception as exc:
        bad(str(exc))
        return 1
    finally:
        restore_dl()
    ok(f"{engine.name} ready · model [bold]{cfg.model}[/bold] on "
       f"[bold]{pick_device(cfg.device).upper()}[/bold]\n")

    total_audio = sum(t.duration or 0 for t in tasks) or float(len(tasks) * 600)
    dash = Dashboard(total_audio, len(tasks)).start()
    wall_start = time.monotonic()
    interrupted = False

    try:
        for index, task in enumerate(tasks, 1):
            dash.task_index = index
            dash.subject = task.parent.name
            dash.meeting = f"Pertemuan_{task.meeting}"
            dash.filename = task.audio.name
            dash.duration = task.duration
            dash.chunk_done = dash.chunk_total = 0
            dash.preview = ""
            dash.phase = "preparing"
            dash.refresh()
            try:
                process_task(task, engine, cfg, dash)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                task.status = "failed"
                task.status_detail = str(exc)
                dash.note(f"[red]✗ {ellipsize(str(exc), 70)}[/red]")
            # keep the overall bar honest even if a file failed
            dash.done_audio = max(dash.done_audio,
                                  sum(t.duration or 0 for t in tasks[:index]))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        dash.phase = "done"
        dash.refresh()
        dash.stop()

    wall = time.monotonic() - wall_start
    index_path = None if cfg.no_index else rebuild_index(root)
    final_report(tasks, wall, index_path)

    if interrupted:
        warn("Stopped early. Finished pieces are saved — run again to carry on "
             "exactly where you left off.")
    notify(sum(1 for t in tasks if t.status == "done"), wall)
    return 0


if __name__ == "__main__":
    code = 0
    try:
        code = main()
    except KeyboardInterrupt:
        print("\nInterrupted. Nothing was lost — run again to resume.")
        code = 130
    except Exception as exc:  # last-resort net so the window never vanishes
        bad(f"Unexpected error: {exc}")
        import traceback
        traceback.print_exc()
        code = 1
    if "--no-pause" not in sys.argv:
        try:
            input("\nPress Enter to close…")
        except EOFError:
            pass
    sys.exit(code)
