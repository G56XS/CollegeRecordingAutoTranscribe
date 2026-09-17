"""
auto_transcribe.py  (College Archive version + smart silence splitting)
"""

import os
import re
import time
import shutil
from pathlib import Path
from datetime import datetime, timedelta

import whisper
import torch

# Optional but highly recommended
try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

try:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False
    print("WARNING: pydub not installed → no smart splitting")
    print("Run: pip install pydub")

# ====================== CONFIG ======================
MODEL_NAME = "medium"
LANGUAGE = "id"                  # Indonesian
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".wma", ".aac", ".mp4"}

# Splitting settings
SPLIT_IF_LONGER_THAN_MINUTES = 25      # only split if longer than this
MIN_SILENCE_LEN = 800                  # ms of silence to consider a cut point
SILENCE_THRESH = -40                   # dBFS (lower = more sensitive)
KEEP_CHUNKS = True                     # set False if you want to delete the split files after
# ====================================================

def get_audio_duration(path: Path) -> float:
    if HAS_MUTAGEN:
        try:
            audio = MutagenFile(str(path))
            if audio is not None and hasattr(audio.info, "length"):
                return float(audio.info.length)
        except Exception:
            pass
    return 0.0

def find_next_pertemuan_number(folder: Path) -> int:
    max_n = 0
    pattern = re.compile(r"^Pertemuan_(\d+)$", re.IGNORECASE)
    for item in folder.iterdir():
        if item.is_dir():
            m = pattern.match(item.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return max_n + 1

def format_time(seconds: float) -> str:
    if seconds < 0:
        return "??:??"
    return str(timedelta(seconds=int(seconds)))

def smart_split(audio_path: Path, output_folder: Path) -> list[Path]:
    """
    Split audio on silence. Returns list of chunk paths.
    Falls back to fixed-length chunks if silence splitting fails.
    """
    if not HAS_PYDUB:
        return [audio_path]

    print("  → Analyzing silence and splitting...")
    audio = AudioSegment.from_file(str(audio_path))

    # Try silence-based split
    chunks = split_on_silence(
        audio,
        min_silence_len=MIN_SILENCE_LEN,
        silence_thresh=SILENCE_THRESH,
        keep_silence=300,          # keep a bit of silence at edges
        seek_step=50
    )

    # If too few chunks (almost no silence), force fixed-length split
    if len(chunks) <= 1:
        print("  → Not enough silence found, using fixed 12-minute chunks...")
        chunk_length_ms = 12 * 60 * 1000
        chunks = [audio[i:i + chunk_length_ms] for i in range(0, len(audio), chunk_length_ms)]

    chunk_paths = []
    for i, chunk in enumerate(chunks, 1):
        chunk_path = output_folder / f"chunk_{i:02d}.mp3"
        chunk.export(str(chunk_path), format="mp3", bitrate="128k")
        chunk_paths.append(chunk_path)
        print(f"     created {chunk_path.name} ({len(chunk)/1000:.1f}s)")

    return chunk_paths

def collect_untranscribed(work_dir: Path):
    tasks = []
    for subject in work_dir.iterdir():
        if not subject.is_dir():
            continue
        if subject.name.lower() in {"__pycache__", ".git"}:
            continue
        for f in subject.iterdir():
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                tasks.append((subject, f))
    return tasks

def main():
    work_dir = Path(__file__).resolve().parent
    print(f"College Archive root: {work_dir}")
    print(f"Device: {DEVICE.upper()} | Model: {MODEL_NAME} | Language: Indonesian")
    print("-" * 70)

    tasks = collect_untranscribed(work_dir)
    if not tasks:
        print("No untranscribed audio files found.")
        input("\nPress Enter to exit...")
        return

    tasks.sort(key=lambda x: x[1].stat().st_mtime)

    print(f"Found {len(tasks)} untranscribed file(s):\n")
    for i, (subj, audio) in enumerate(tasks, 1):
        mtime = datetime.fromtimestamp(audio.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {i}. [{subj.name}] {audio.name}  ({mtime})")
    print()

    print("Loading Whisper model...")
    model = whisper.load_model(MODEL_NAME, device=DEVICE)
    print("Model ready!\n")

    total = len(tasks)
    speed_history = []

    for idx, (subject_folder, audio_path) in enumerate(tasks, 1):
        files_left = total - idx + 1
        print("=" * 70)
        print(f"[{idx}/{total}]  Remaining: {files_left}")
        print(f"Subject : {subject_folder.name}")
        print(f"File    : {audio_path.name}")

        next_n = find_next_pertemuan_number(subject_folder)
        pertemuan = subject_folder / f"Pertemuan_{next_n}"
        pertemuan.mkdir(exist_ok=True)

        # 1. Move original audio into the folder FIRST
        original_in_folder = pertemuan / audio_path.name
        shutil.move(str(audio_path), str(original_in_folder))
        print(f"Moved original → {original_in_folder.name}")

        duration = get_audio_duration(original_in_folder)
        if duration > 0:
            print(f"Duration: {format_time(duration)}")

        # 2. Decide whether to split
        need_split = duration > (SPLIT_IF_LONGER_THAN_MINUTES * 60) or original_in_folder.stat().st_size > 20 * 1024 * 1024

        if need_split and HAS_PYDUB:
            chunks = smart_split(original_in_folder, pertemuan)
        else:
            chunks = [original_in_folder]
            if need_split:
                print("  → File is long but pydub not available, transcribing whole file...")

        # 3. Transcribe every chunk
        full_text = []
        full_detailed = []
        start_time = time.time()

        for i, chunk in enumerate(chunks, 1):
            print(f"  Transcribing chunk {i}/{len(chunks)}: {chunk.name}")
            try:
                result = model.transcribe(
                    str(chunk),
                    language=LANGUAGE,
                    verbose=False,
                    fp16=(DEVICE == "cuda")
                )
                full_text.append(result["text"].strip())

                for seg in result.get("segments", []):
                    start_t = format_time(seg["start"])
                    end_t = format_time(seg["end"])
                    full_detailed.append(f"[{start_t} --> {end_t}] {seg['text'].strip()}")
            except Exception as e:
                print(f"    ❌ Error on chunk {i}: {e}")

        elapsed = time.time() - start_time

        # 4. Save combined transcripts
        with open(pertemuan / "transcript.txt", "w", encoding="utf-8") as f:
            f.write("\n\n".join(full_text) + "\n")

        with open(pertemuan / "transcript_detailed.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(full_detailed) + "\n")

        # Optional: remove the temporary chunks
        if not KEEP_CHUNKS and len(chunks) > 1:
            for c in chunks:
                if c != original_in_folder:
                    c.unlink(missing_ok=True)

        if duration > 0 and elapsed > 0:
            speed_history.append(duration / elapsed)

        print(f"✓ Finished in {format_time(elapsed)}")
        print(f"  → {pertemuan / 'transcript.txt'}")
        print(f"  → {pertemuan / 'transcript_detailed.txt'}\n")

    print("=" * 70)
    print("All done!")
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    main()
