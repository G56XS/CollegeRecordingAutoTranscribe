import os
import time
import json
import shutil
import subprocess
from datetime import datetime
import whisper
from pydub import AudioSegment
from pydub.silence import split_on_silence

# --- RICH UI IMPORTS ---
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn, 
    TimeElapsedColumn, TimeRemainingColumn
)
from rich.table import Table
from rich.theme import Theme

# ================= CONFIGURATION =================
BASE_DIR = r"C:\Users\Iven\Documents\College Archive"
MODEL_NAME = "medium"
LANGUAGE = "id"
DEVICE = "cuda"
AUDIO_EXTENSIONS = {'.m4a', '.mp3', '.wav', '.flac', '.ogg', '.aac'}
MAX_FILE_SIZE_MB = 24
# =================================================

# Initialize Rich Console with a custom professional theme
console = Console(theme=Theme({
    "info": "cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "header": "bold bright_blue",
    "subheader": "bold magenta"
}))

def get_file_size_mb(path):
    return os.path.getsize(path) / (1024 * 1024)

def is_transcribed(audio_path, parent_dir):
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    if os.path.exists(os.path.join(parent_dir, base_name + ".txt")):
        return True
    for item in os.listdir(parent_dir):
        if item.startswith("Pertemuan_") and os.path.isdir(os.path.join(parent_dir, item)):
            if os.path.exists(os.path.join(parent_dir, item, base_name + ".txt")):
                return True
    return False

def format_time(seconds):
    if seconds is None: return "Calculating..."
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"

def get_next_pertemuan_number(parent_dir):
    max_num = 0
    if not os.path.exists(parent_dir): return 1
    for item in os.listdir(parent_dir):
        if item.startswith("Pertemuan_") and os.path.isdir(os.path.join(parent_dir, item)):
            try:
                num = int(item.split("_")[1])
                max_num = max(max_num, num)
            except ValueError:
                continue
    return max_num + 1

def smart_split_audio(audio_path, output_dir, max_size_mb=24):
    file_size_mb = get_file_size_mb(audio_path)
    console.print(f"     [warning]⚠️  File is {file_size_mb:.1f} MB — over the {max_size_mb} MB limit.[/]")
    console.print(f"     [info]🔇 Scanning for silent gaps to split cleanly...[/]")

    audio = AudioSegment.from_file(audio_path)
    duration_ms = len(audio)

    if duration_ms == 0:
        console.print("     [warning]⚠️  Audio is empty (0 s). Skipping.[/]")
        return [audio_path]

    bitrate_kbps = (file_size_mb * 8 * 1024) / (duration_ms / 1000)
    max_duration_ms = int((max_size_mb * 8 * 1024) / bitrate_kbps * 1000)

    chunks = split_on_silence(audio, min_silence_len=1500, silence_thresh=-40, keep_silence=500)
    if len(chunks) <= 1:
        chunks = split_on_silence(audio, min_silence_len=700, silence_thresh=-35, keep_silence=300)

    final_chunks = []
    current_group = AudioSegment.empty()
    for chunk in chunks:
        if len(current_group) + len(chunk) > max_duration_ms and len(current_group) > 0:
            final_chunks.append(current_group)
            current_group = chunk
        else:
            current_group = current_group + chunk if len(current_group) > 0 else chunk
    if len(current_group) > 0:
        final_chunks.append(current_group)

    if len(final_chunks) <= 1 and duration_ms > max_duration_ms:
        console.print("     [warning]⚠️  No silence detected — force-splitting by time...[/]")
        final_chunks = []
        start = 0
        while start < duration_ms:
            end = min(start + max_duration_ms, duration_ms)
            final_chunks.append(audio[start:end])
            start = end

    chunk_paths = []
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    ext = os.path.splitext(audio_path)[1].lstrip('.')
    fmt_map = {'m4a': 'ipod', 'mp3': 'mp3', 'wav': 'wav', 'flac': 'flac', 'ogg': 'ogg', 'aac': 'adts'}
    export_fmt = fmt_map.get(ext, ext)

    # --- Beautiful Table for Shards ---
    table = Table(title="🔪 Audio Shards Created", show_header=True, header_style="bold magenta", border_style="dim")
    table.add_column("Shard", style="cyan", justify="center")
    table.add_column("Duration", justify="right", style="green")
    table.add_column("Size", justify="right", style="yellow")

    for i, chunk in enumerate(final_chunks, 1):
        chunk_path = os.path.join(output_dir, f"{base_name}_part{i}.{ext}")
        chunk.export(chunk_path, format=export_fmt)
        chunk_paths.append(chunk_path)
        table.add_row(
            f"Part {i}", 
            format_time(len(chunk) / 1000), 
            f"{get_file_size_mb(chunk_path):.1f} MB"
        )
    
    console.print(table)
    return chunk_paths

def main():
    # --- Beautiful Header ---
    console.print(Panel.fit(
        "[bold white]🎓 COLLEGE ARCHIVE AUTO-TRANSCRIBER[/]\n"
        "[dim]Powered by Whisper • Smart Silence Splitting • Auto-Cleanup[/]",
        border_style="bright_blue",
        padding=(1, 4)
    ))

    # --- 1. Scan ---
    with console.status("[bold cyan]Scanning archive for untranscribed audio..."):
        execution_queue = []
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if not d.startswith("Pertemuan_")]
            for f in files:
                if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS:
                    full = os.path.join(root, f)
                    if not is_transcribed(full, root):
                        execution_queue.append((full, root))

    if not execution_queue:
        console.print("\n[success]✅ Everything is already transcribed! Nothing to do.[/]")
        return

    console.print(f"\n[success]✅ Found[/] [bold]{len(execution_queue)}[/] [success]untranscribed file(s).[/]")

    # --- 2. Group ---
    with console.status("[bold cyan]Grouping by Subject and Date..."):
        grouped = {}
        for fpath, sdir in execution_queue:
            date_str = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d")
            grouped.setdefault(sdir, {}).setdefault(date_str, []).append(fpath)

        final_queue = []
        for subj in sorted(grouped):
            for date in sorted(grouped[subj]):
                for f in sorted(grouped[subj][date], key=os.path.getmtime):
                    final_queue.append((f, subj, date))

    # --- 3. Load Model ---
    console.print(f"\n[info]🧠 Loading Whisper '{MODEL_NAME}' on {DEVICE.upper()}...[/]")
    model = whisper.load_model(MODEL_NAME, device=DEVICE)
    console.print("[success]✅ Model ready![/]\n")

    # --- 4. Transcribe with Beautiful Progress Bar ---
    total_files = len(final_queue)
    pertemuan_map = {s: get_next_pertemuan_number(s) for s in sorted(grouped)}
    last_subj = None
    last_date = None

    # The Rich Progress Bar
    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[bold]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
        expand=True
    ) as progress:
        
        task = progress.add_task("[cyan]Processing files...", total=total_files)

        for audio_path, subject_dir, date_str in final_queue:
            file_name = os.path.basename(audio_path)
            subject_name = os.path.basename(subject_dir)
            
            # Update Progress Bar Description
            progress.update(task, description=f"[cyan]Transcribing:[/] [bold white]{file_name}[/] [dim]({subject_name})[/]")

            # Subject Header
            if subject_dir != last_subj:
                console.print(f"\n{'━'*25} [subheader]📚 {subject_name}[/] {'━'*25}")
                last_subj = subject_dir
                last_date = None

            # Date / Pertemuan Header
            if date_str != last_date:
                pnum = pertemuan_map[subject_dir]
                pertemuan_name = f"Pertemuan_{pnum}"
                pertemuan_path = os.path.join(subject_dir, pertemuan_name)
                os.makedirs(pertemuan_path, exist_ok=True)
                console.print(f"\n[header]📅 {pertemuan_name}[/]  [dim]({date_str})[/]")
                last_date = date_str
                pertemuan_map[subject_dir] += 1

            # Move original audio
            dest = os.path.join(pertemuan_path, file_name)
            if os.path.abspath(audio_path) != os.path.abspath(dest):
                if not os.path.exists(dest):
                    shutil.move(audio_path, dest)
                audio_path = dest
                console.print(f"     [dim]📁 Moved into {pertemuan_name}/[/]")

            # Split if too large
            if get_file_size_mb(audio_path) > MAX_FILE_SIZE_MB:
                chunk_paths = smart_split_audio(audio_path, pertemuan_path, MAX_FILE_SIZE_MB)
            else:
                chunk_paths = [audio_path]

            # Transcribe every chunk
            full_text = ""
            for j, cp in enumerate(chunk_paths):
                if len(chunk_paths) > 1:
                    console.print(f"     [info]📝 Transcribing shard {j + 1}/{len(chunk_paths)}...[/]")

                # verbose=False stops Whisper from spamming the console
                result = model.transcribe(cp, language=LANGUAGE, verbose=False) 
                full_text += result["text"].strip() + "\n\n"

            # Save merged transcription
            txt_name = os.path.splitext(file_name)[0] + ".txt"
            txt_path = os.path.join(pertemuan_path, txt_name)
            with open(txt_path, "w", encoding="utf-8") as fh:
                fh.write(full_text.strip())

            # Cleanup shards
            if len(chunk_paths) > 1:
                console.print(f"     [dim]🧹 Cleaning up {len(chunk_paths)} temporary shards...[/]")
                for cp in chunk_paths:
                    try: os.remove(cp)
                    except: pass
                console.print(f"     [success]🗑️  Shards removed. Archive is clean.[/]")

            console.print(f"     [success]✅ Saved:[/] [bold]{txt_name}[/]")
            
            # Advance the progress bar
            progress.update(task, advance=1)

    # --- Final Footer ---
    console.print()
    console.print(Panel.fit(
        "[bold green]🎉 ALL SUBJECTS TRANSCRIBED & CLEANED UP SUCCESSFULLY![/]",
        border_style="green",
        padding=(1, 4)
    ))

if __name__ == "__main__":
    main()
    input("\nPress Enter to exit...")