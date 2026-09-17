import os
import sys
import time
import shutil
import subprocess
from datetime import timedelta

# Imports with dependency checks
try:
    from pydub import AudioSegment
    from pydub.silence import detect_silence
except ImportError:
    print("Missing 'pydub'. Install it using: pip install pydub")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
    from rich.text import Text
except ImportError:
    print("Missing 'rich'. Install it using: pip install rich")
    sys.exit(1)

try:
    from plyer import notification
except ImportError:
    notification = None

# --- CONFIGURATION ---
MODEL = "medium"
LANGUAGE = "Indonesian"
DEVICE = "cuda"
MAX_SIZE_MB = 20                   # Split files larger than 20 MB
TARGET_CHUNK_MINUTES = 10          # Target chunk length in minutes
AUDIO_EXTENSIONS = (".m4a", ".mp3", ".wav", ".flac", ".aac", ".mp4", ".mkv")

console = Console()

def format_time(seconds):
    return str(timedelta(seconds=int(seconds)))

def split_audio_on_silence(audio_path, target_dir):
    """Splits an audio file at silent points if it exceeds MAX_SIZE_MB."""
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    if file_size_mb <= MAX_SIZE_MB:
        return [audio_path], False

    console.print(f"  [bold yellow]⚡ Large File Detected[/bold yellow] ({file_size_mb:.1f} MB > {MAX_SIZE_MB} MB). Scanning for silence gaps...")
    
    audio = AudioSegment.from_file(audio_path)
    total_len_ms = len(audio)
    target_len_ms = TARGET_CHUNK_MINUTES * 60 * 1000

    silences = detect_silence(audio, min_silence_len=500, silence_thresh=-35)

    cut_points = [0]
    next_target = target_len_ms

    for start, end in silences:
        mid_point = (start + end) // 2
        if mid_point >= next_target:
            cut_points.append(mid_point)
            next_target = mid_point + target_len_ms

    cut_points.append(total_len_ms)

    chunk_paths = []
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    
    for i in range(len(cut_points) - 1):
        chunk_audio = audio[cut_points[i]:cut_points[i + 1]]
        chunk_name = f"{base_name}_part{i+1:02d}.m4a"
        chunk_path = os.path.join(target_dir, chunk_name)
        
        chunk_audio.export(chunk_path, format="ipod")
        chunk_paths.append(chunk_path)

    console.print(f"  [bold green]✔ Split Complete:[/bold green] Created {len(chunk_paths)} seamless chunks at natural pauses.\n")
    return chunk_paths, True

def scan_subject(subject_dir):
    audio_files = []
    for root, _, files in os.walk(subject_dir):
        for f in files:
            if f.lower().endswith(AUDIO_EXTENSIONS) and "_part" not in f:
                audio_files.append(os.path.join(root, f))
    
    if not audio_files:
        return []

    # Sort audio files chronologically (oldest first)
    audio_files.sort(key=lambda p: os.path.getmtime(p))

    tasks = []
    subject_name = os.path.basename(subject_dir)

    for idx, audio_path in enumerate(audio_files, start=1):
        folder_name = f"Pertemuan_{idx}"
        target_folder = os.path.join(subject_dir, folder_name)
        base_name = os.path.splitext(os.path.basename(audio_path))[0]
        
        final_txt = os.path.join(target_folder, f"{base_name}.txt")
        is_transcribed = os.path.exists(final_txt)

        tasks.append({
            "subject": subject_name,
            "index": idx,
            "folder_name": folder_name,
            "target_folder": target_folder,
            "audio_path": audio_path,
            "base_name": base_name,
            "is_transcribed": is_transcribed
        })

    return tasks

def render_summary_table(all_tasks):
    table = Table(title="📊 College Archive Scan Summary", box=box.ROUNDED, header_style="bold magenta")
    table.add_column("Subject / Folder", style="bold white", width=28)
    table.add_column("Total Audios", justify="center", style="cyan")
    table.add_column("Completed", justify="center", style="bold green")
    table.add_column("Pending Queue", justify="center", style="bold yellow")

    subjects_summary = {}
    for task in all_tasks:
        s = task["subject"]
        if s not in subjects_summary:
            subjects_summary[s] = {"total": 0, "completed": 0, "pending": 0}
        subjects_summary[s]["total"] += 1
        if task["is_transcribed"]:
            subjects_summary[s]["completed"] += 1
        else:
            subjects_summary[s]["pending"] += 1

    for sub, stats in subjects_summary.items():
        table.add_row(
            sub, 
            str(stats["total"]), 
            str(stats["completed"]), 
            f"[bold yellow]{stats['pending']}[/bold yellow]" if stats["pending"] > 0 else "[green]0[/green]"
        )

    console.print(table)
    console.print()

def main():
    archive_dir = os.path.dirname(os.path.abspath(__file__))

    # Banner Header
    console.print(
        Panel(
            Text("🎓 COLLEGE ARCHIVE AUTOMATIC TRANSCRIBER", justify="center", style="bold white on blue"),
            subtitle="Auto-Sort • Silence Splitter • Whisper CUDA",
            subtitle_style="dim white",
            box=box.DOUBLE,
            border_style="cyan"
        )
    )

    entries = os.listdir(archive_dir)
    subject_dirs = [
        os.path.join(archive_dir, d) for d in entries 
        if os.path.isdir(os.path.join(archive_dir, d)) and not d.startswith('.')
    ]

    all_tasks = []
    for s_dir in subject_dirs:
        all_tasks.extend(scan_subject(s_dir))

    if not all_tasks:
        console.print("[bold red]x No audio files found in your College Archive subfolders.[/bold red]")
        return

    render_summary_table(all_tasks)

    pending_tasks = [t for t in all_tasks if not t["is_transcribed"]]
    total_pending = len(pending_tasks)

    if total_pending == 0:
        console.print(Panel("[bold green]✔ All audio files across all subjects are already organized and transcribed![/bold green]", box=box.ROUNDED))
        return

    completed_durations = []

    for i, task in enumerate(pending_tasks, start=1):
        remaining = total_pending - i + 1
        audio_filename = os.path.basename(task["audio_path"])
        target_folder = task["target_folder"]

        os.makedirs(target_folder, exist_ok=True)

        if completed_durations:
            avg_time = sum(completed_durations) / len(completed_durations)
            eta_str = f"~{format_time(avg_time * remaining)}"
        else:
            eta_str = "Calculating after 1st file..."

        # Task Card
        info_text = (
            f"[bold cyan]Subject     :[/bold cyan] {task['subject']}\n"
            f"[bold white]File        :[/bold white] {audio_filename}\n"
            f"[bold yellow]Destination :[/bold yellow] {task['subject']}/{task['folder_name']}/\n"
            f"[bold magenta]Queue       :[/bold magenta] File {i} of {total_pending} ({remaining} remaining)\n"
            f"[bold green]Archive ETA :[/bold green] {eta_str}"
        )
        
        console.print(
            Panel(
                info_text, 
                title=f"[bold white]⚙ Processing Queue [{i}/{total_pending}][/bold white]", 
                border_style="magenta",
                box=box.ROUNDED
            )
        )

        start_time = time.time()

        # Step 1: Move Audio File
        dest_audio = os.path.join(target_folder, audio_filename)
        if os.path.abspath(task["audio_path"]) != os.path.abspath(dest_audio):
            shutil.move(task["audio_path"], dest_audio)
            task["audio_path"] = dest_audio
            console.print(f"  [bold blue]📁 Moved[/bold blue] audio into [bold]{task['folder_name']}/[/bold]")

        # Step 2: Split Audio on Silence
        chunks, was_split = split_audio_on_silence(task["audio_path"], target_folder)

        # Step 3: Transcribe
        chunk_txt_files = []
        for c_idx, chunk_path in enumerate(chunks, start=1):
            if was_split:
                console.print(f"  [bold cyan]🎙 Transcribing Chunk [{c_idx}/{len(chunks)}]...[/bold cyan]")
            else:
                console.print(f"  [bold cyan]🎙 Transcribing Audio...[/bold cyan]")

            cmd = [
                "whisper",
                chunk_path,
                "--model", MODEL,
                "--language", LANGUAGE,
                "--device", DEVICE,
                "--output_dir", target_folder,
                "--output_format", "txt"
            ]

            try:
                subprocess.run(cmd, check=True)
                chunk_txt = os.path.splitext(chunk_path)[0] + ".txt"
                chunk_txt_files.append(chunk_txt)
            except subprocess.CalledProcessError as e:
                console.print(f"  [bold red]✖ Error transcribing chunk {chunk_path}: {e}[/bold red]")

        # Step 4: Merge Transcripts & Cleanup
        master_txt_path = os.path.join(target_folder, f"{task['base_name']}.txt")
        with open(master_txt_path, "w", encoding="utf-8") as master_file:
            for txt_file in chunk_txt_files:
                if os.path.exists(txt_file):
                    with open(txt_file, "r", encoding="utf-8") as tf:
                        master_file.write(tf.read() + "\n\n")

        if was_split:
            for c_path, c_txt in zip(chunks, chunk_txt_files):
                if os.path.exists(c_path):
                    os.remove(c_path)
                if os.path.exists(c_txt) and c_txt != master_txt_path:
                    os.remove(c_txt)
            console.print(f"  [bold dim]🧹 Cleaned up temporary split audio & text shards.[/bold dim]")

        elapsed = time.time() - start_time
        completed_durations.append(elapsed)

        console.print(f"  [bold green]✔ Complete![/bold green] Saved [bold]{task['base_name']}.txt[/bold] in [bold]{format_time(elapsed)}[/bold]\n")

    console.print(
        Panel(
            Text("🎉 ALL SUBJECT AUDIO FILES PROCESSED SUCCESSFULLY!", justify="center", style="bold white on green"),
            box=box.ROUNDED,
            border_style="green"
        )
    )

    # Windows Desktop Notification
    if notification:
        try:
            notification.notify(
                title="🎓 College Archive Transcriber",
                message=f"Done! Transcribed {total_pending} audio file(s) across your archive.",
                app_name="College Archive Transcriber",
                timeout=10
            )
        except Exception as e:
            console.print(f"[dim]Notification trigger skipped: {e}[/dim]")

if __name__ == "__main__":
    main()
