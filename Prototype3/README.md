# College Archive Transcriber

One script for the whole archive. Drop recordings into any subject folder,
double-click the launcher, and every new one gets transcribed and filed into
`Pertemuan_N`.

## Setup (once)

```
pip install -U openai-whisper rich
```

Optional but worth it:

```
pip install plyer               # desktop notification when a run finishes
```

You also need **ffmpeg** and **ffprobe** on your PATH (ffmpeg.org). For GPU
speed, install a CUDA build of PyTorch from pytorch.org and check:

```
python -c "import torch; print(torch.cuda.is_available())"   # should say True
```

Put `archive_transcriber.py` and `Transcribe.bat` inside your
`College Archive` folder, then double-click the .bat.

## What a run does

1. Walks the whole tree. Loose audio anywhere is "new"; a `Pertemuan_N`
   folder without a `transcript.txt` is "unfinished" and gets resumed.
2. Numbers new recordings per subject by recorded date, continuing from the
   highest `Pertemuan_N` already in that subject. Nothing existing is renumbered.
3. Moves the recording into its `Pertemuan_N` folder **first**, so an
   interruption can never strand a half-processed file.
4. Anything over ~18 minutes gets cut into ~15-minute pieces using ffmpeg's
   silence detection — cuts land inside a pause, never mid-sentence.
5. Cuts, transcribes, saves, and deletes **one piece at a time**. Memory stays
   flat whether the lecture is 10 minutes or 4 hours.
6. Stitches the pieces back together with corrected timestamps and writes:
   - `transcript.txt` — clean paragraphed text with a small metadata header
   - `transcript_timestamped.txt` — `[00:12:30 → 00:12:35] …` per line
   - `transcript.srt` — subtitles, for playing the audio alongside the text
7. Rebuilds `_Archive_Index.md` at the root: every subject, every meeting,
   date, length, and whether it's done.

A finished folder holds the original audio plus those three files. Nothing else.

## If it gets interrupted

Just run it again. Each piece's result is written to disk the moment it
finishes, along with the cut plan, so a resumed run reuses everything already
done and continues from the exact piece it died on. Ctrl+C is safe.

## Options

```
python archive_transcriber.py "C:\path\to\College Archive" --model large-v3
```

| Flag | Does |
|---|---|
| `--model` | `tiny` … `medium` (default), `large-v3`, `turbo` |
| `--language` | default `id` |
| `--device` | `auto` (default), `cuda`, `cpu` |
| `--chunk-minutes` | target piece length, default 15 |
| `--split-over-minutes` | leave anything shorter than this whole, default 18 |
| `--silence-db`, `--silence-min-dur` | what counts as a pause |
| `--keep-chunks` | keep the temporary pieces |
| `--no-srt`, `--no-header`, `--no-index` | trim the outputs |
| `--dry-run` | show the queue and stop |

## Notes

- Runs on plain `openai-whisper` only. An earlier version also tried
  `faster-whisper` automatically, but that engine fetches its own
  separately-hosted copy of the model from Hugging Face Hub — a different
  file from openai-whisper's, even for the same model name — which is what
  caused the extra download and the `hf_xet`/symlink warnings some runs saw.
  Plain openai-whisper doesn't have that problem.
- The 25 MB limit you may have read about belongs to OpenAI's paid cloud API.
  This runs Whisper locally, so it doesn't apply. Splitting here is for memory
  and crash-resilience, not a size cap.
- To redo a meeting, delete its `transcript.txt` and run again.
- `condition_on_previous_text` is off, which is what stops Whisper from getting
  stuck repeating the same sentence forever on quiet lecture audio.
- The console window stays open at the end; press Enter to close.

## Where this came from

Merged from five separate attempts, keeping the best of each:

| From | Kept |
|---|---|
| Claude | ffmpeg silence-aware split points, per-subject numbering that continues from existing folders, chunk-level resume |
| ChatGPT | JSON state on disk, the live dashboard layout, safe file moves, working without Rich installed |
| Grok | timestamped segment output (fixed so offsets are global, not per-chunk) |
| Qwen | Rich tables and panels, temporary-shard cleanup |
| Gemini | scan summary table, per-file ETA, desktop notification |

Added on top: model loaded once instead of per chunk, intra-chunk progress,
correct global timestamps, `.srt` output, the archive index, lazy
one-piece-at-a-time cutting, and a real progress bar for first-time model
downloads.
