# College Archive Transcriber

**One script that watches your whole lecture archive, finds every recording without a transcript, splits it intelligently, transcribes it with Whisper, and files everything into neat `Pertemuan_N` folders.**

Drop new recordings into any subject folder and run it again. It resumes exactly where it left off.

---

## Features

| | |
|---|---|
| **Resumable** | Kill it anytime. Re-run and it continues from the last finished chunk. |
| **Memory-friendly** | Cuts with ffmpeg, transcribes one piece at a time. A 3-hour lecture never loads fully into RAM. |
| **Smart cuts** | Finds silent stretches so sentences aren't chopped mid-word. |
| **Model loaded once** | Weights stay in memory for the whole run — no reloading per file. |
| **Live dashboard** | Rich progress UI: subject, meeting, live text preview, ETA, activity log. |
| **Clean outputs** | Plain transcript, timestamped transcript, and optional `.srt` subtitles. |
| **Archive index** | Regenerates a one-page Markdown map of every subject and meeting. |
| **Graceful fallback** | Works without Rich (plain text mode) and without desktop notifications. |

---

## Requirements

- **Python 3.9+**
- **ffmpeg** and **ffprobe** on your `PATH`
- **openai-whisper**
- **rich** (optional but strongly recommended for the live UI)
- **plyer** (optional — desktop notification when a run finishes)

### Install once

```bash
pip install -U openai-whisper rich
# optional
pip install plyer
```

For GPU speed, install a CUDA build of PyTorch from [pytorch.org](https://pytorch.org) before or after Whisper.

ffmpeg:

- Windows: [ffmpeg.org/download.html](https://ffmpeg.org/download.html) → add the `bin` folder to PATH
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg` (or equivalent)

---

## Quick start

1. Put `archive_transcriber.py` (and optionally `Transcribe.bat`) in your archive root, **or** point the script at the archive folder.
2. Drop lecture recordings into subject folders (any supported audio/video format).
3. Run:

```bash
python archive_transcriber.py "C:\Users\You\Documents\College Archive"
```

Or double-click **`Transcribe.bat`** on Windows (it uses the folder it lives in by default).

That’s it. The script will:

1. Scan the tree
2. Find recordings that don’t have `transcript.txt` yet
3. Move each into a `Pertemuan_N` folder
4. Split long files at silence, transcribe, and write outputs
5. Rebuild `_Archive_Index.md`

---

## What it produces

For each meeting folder (e.g. `Mata Kuliah X/Pertemuan_3/`):

| File | Description |
|------|-------------|
| `transcript.txt` | Clean prose transcript with optional header (subject, date, duration, engine) |
| `transcript_timestamped.txt` | Same text with `[HH:MM:SS → HH:MM:SS]` timestamps |
| `transcript.srt` | Subtitle file (skip with `--no-srt`) |
| The original recording | Moved into the meeting folder |

At the archive root:

| File | Description |
|------|-------------|
| `_Archive_Index.md` | One-page table of every subject → meetings → date, length, status |

Temporary work lives in `_work/` inside each meeting folder and is deleted when the meeting finishes (unless you pass `--keep-chunks`).

---

## How discovery works

- Already organised `Pertemuan_N` folders that lack `transcript.txt` are treated as **resume** jobs.
- Loose audio/video files sitting in a subject folder are **new** recordings. They are assigned the next free meeting number (oldest first by modification time).
- Folders named `_work`, `__pycache__`, `.git`, etc. are ignored.

Supported extensions:

`.m4a` `.mp3` `.wav` `.flac` `.aac` `.ogg` `.opus` `.wma` `.m4b` `.mp4` `.mkv` `.webm` `.amr` `.3gp`

---

## Command-line options

```text
python archive_transcriber.py [root] [options]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `root` | script’s own folder | Path to the archive |
| `--model` | `medium` | Whisper model: `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo`, … |
| `--language` | `id` | Language code passed to Whisper |
| `--device` | `auto` | `auto` / `cuda` / `cpu` |
| `--chunk-minutes` | `15` | Target length of each piece |
| `--split-over-minutes` | `18` | Leave shorter recordings unsplit |
| `--silence-window-minutes` | `4` | How far to search for a quiet cut point |
| `--min-chunk-minutes` | `2` | Never leave a tiny leftover piece |
| `--silence-db` | `-35` | Silence threshold in dB |
| `--silence-min-dur` | `0.45` | Minimum silence duration (seconds) to count as a cut |
| `--keep-chunks` | off | Keep temporary `.wav` pieces |
| `--no-srt` | off | Skip writing `transcript.srt` |
| `--no-header` | off | Write bare transcript without metadata header |
| `--no-index` | off | Don’t rebuild `_Archive_Index.md` |
| `--dry-run` | off | Show the queue and exit |
| `--no-pause` | off | Don’t wait for Enter at the end (useful in scripts) |

Example:

```bash
python archive_transcriber.py "/path/to/archive" --model large-v3 --device cuda --language id
```

---

## How splitting works

Long recordings are cut into ~15-minute pieces (configurable). Cuts prefer the middle of a silent stretch near the target length so speech is not interrupted. If no usable silence is found nearby, a hard cut is used. Each piece is transcribed independently, then timestamps are shifted back to the original timeline so the final transcript and `.srt` are continuous.

Progress is saved after every piece (`_work/piece_NNN.json` + `plan.json`). Re-running the same meeting reuses finished pieces and only processes the rest.

---

## Live UI

When Rich is installed you get a full dashboard:

- Current subject / meeting / filename / phase
- Overall progress bar and ETA based on audio time processed
- Live text preview of the latest segment
- Rolling activity log

Without Rich the script falls back to plain, readable console output.

---

## Tips

- **First run** downloads the Whisper model (once). Progress is shown in the terminal.
- Prefer **GPU** (`--device cuda`) for long archives — the speed difference is large.
- Indonesian lectures: the default language is `id` and an Indonesian initial prompt is used to improve punctuation and capitalisation.
- Interrupted runs are safe. Finished pieces stay on disk; the next run continues from the first missing piece.
- Use `--dry-run` to inspect what would be transcribed without moving or processing anything.

---

## Project layout (example)

```text
College Archive/
├── archive_transcriber.py
├── Transcribe.bat                 # optional Windows launcher
├── _Archive_Index.md              # generated
├── Kalkulus/
│   ├── Pertemuan_1/
│   │   ├── lecture.m4a
│   │   ├── transcript.txt
│   │   ├── transcript_timestamped.txt
│   │   └── transcript.srt
│   └── Pertemuan_2/
│       └── ...
└── Algoritma/
    └── ...
```

---

## License / notes

This is a personal utility script. Use it freely for your own lecture archives. Whisper models are subject to OpenAI’s model licenses; ffmpeg is GPL/LGPL depending on build.

---

**Made for students who record everything and never want to type a transcript again.**
