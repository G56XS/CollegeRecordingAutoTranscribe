# Auto-Transcriber (Whisper, Indonesian) — whole College Archive

One program, watches your entire `College Archive` folder, every subject at
once (Matematika Diskrit, and whatever else you have alongside it).

**A note on OpenAI's "25 MB" limit:** that's a restriction of OpenAI's paid
*cloud API*. This script runs Whisper locally on your own GPU
(`--device cuda`), so that limit doesn't actually apply to you — there's
no hard cap on file size. That said, the script still splits long
recordings into pieces (see below), because that gives you two real
benefits regardless of any size limit: it uses far less memory on very
long files, and if the program gets interrupted partway through a
2-3 hour recording, you don't lose all the progress on it — re-running
just picks up the unfinished pieces.

## One-time setup

1. Install Python 3.9+ if you don't have it.
2. Install Whisper, a CUDA build of PyTorch, and Rich (for the interface):
   ```
   pip install -U openai-whisper rich
   ```
   Confirm CUDA is working: `python -c "import torch; print(torch.cuda.is_available())"` → should print `True`.
3. Install [ffmpeg](https://ffmpeg.org/download.html) and make sure `ffmpeg`
   and `ffprobe` are on your PATH.
4. Put `auto_transcribe.py` and `Transcribe_CollegeArchive.bat` anywhere
   convenient (they can even live right inside `College Archive` itself).

## Usage

Just double-click `Transcribe_CollegeArchive.bat` whenever you've added new
recordings anywhere in the archive. No need to tell it which subject —
it scans the whole tree itself.

## What it looks like while running

The console now shows a proper interface instead of a wall of text:
a bordered banner, a table of newly-found recordings, a spinner while
it analyzes long files for quiet spots to split at, a live progress bar
with a real elapsed/remaining-time estimate (weighted by how much audio
is actually left, not just file count), green checkmarks as each piece
finishes, and a final table showing exactly which `Pertemuan_N` folder
each recording landed in.

## What it does every run

1. Walks the entire `College Archive` folder, through every subject
   subfolder, looking for loose audio files (`.m4a`, `.mp3`, `.wav`,
   `.aac`, `.flac`, `.ogg`, `.m4b`, `.wma`) that haven't been picked up
   yet.
2. Nothing new anywhere? It prints that and exits — nothing else happens.
3. For each new file found, **it moves the file into its own same-named
   folder first** (e.g. `Rekaman_baru.m4a` → `Rekaman_baru/Rekaman_baru.m4a`).
   This folder is where all the work for that recording happens.
4. If a recording is short (roughly under ~18 minutes), it's transcribed
   as-is. If it's longer than that, the script finds quiet stretches in
   the audio (using ffmpeg's silence detection) and cuts it into ~15
   minute pieces **at those quiet points** — never mid-sentence — saved
   into a hidden `_chunks` subfolder inside the recording's folder.
5. It loads Whisper `medium` on CUDA (Indonesian language) and
   transcribes every queued piece, across every subject, one at a time,
   printing for each:
   - which subject and recording it's on, and which part (`part 2/5`) if
     the recording was split
   - that piece's length (if it can read it)
   - how many pieces are left overall after this one
   - an estimated time remaining for the rest of the whole queue, based
     on how fast it's processed pieces so far (it says "unknown" until at
     least one piece has finished — there's nothing to estimate from
     before that)
   - the live transcript text streaming as Whisper decodes it
6. Once all of a recording's pieces are transcribed, their texts are
   stitched back together (in order) into one final `.txt` matching the
   original filename, and the temporary `_chunks` folder is deleted —
   you're left with just the original audio + one clean transcript.
7. Finally, it goes back through the tree and, for **each subject folder
   separately**, takes that subject's finished recording folders, sorts
   them oldest-first (by the audio file's original modified date), and
   renames each one into `Pertemuan_N` — continuing the numbering from
   whatever `Pertemuan_N` folders already exist in that specific subject
   (so Matematika Diskrit and any other subject each have their own
   independent 1, 2, 3... count, and old folders never get renumbered).

### If the program gets interrupted mid-transcription

Just run it again. Pieces that already have a transcript are skipped,
and it picks up exactly where it left off — nothing gets re-transcribed,
and nothing gets lost.

Example partway through a run, with `kuliah2.m4a` being a long 2-hour
recording that's mid-split:

```
College Archive\
    Matematika Diskrit\
        Pertemuan_1\
            Pertemuan_1.m4a
            Pertemuan_1.txt
        Pertemuan_2\
            Pertemuan_2.m4a
            Pertemuan_2.txt
    Kalkulus\
        Pertemuan_1\
            kuliah1.m4a
            kuliah1.txt
        kuliah2\                  <- being worked on right now
            kuliah2.m4a
            _chunks\
                chunk_0001.wav
                chunk_0001.txt    <- done
                chunk_0002.wav    <- in progress / next up
                chunk_0003.wav
```

Once `kuliah2`'s pieces are all transcribed, they get merged into
`kuliah2\kuliah2.txt`, `_chunks` is deleted, and the whole `kuliah2`
folder gets renamed to `Pertemuan_2`.

## Notes

- A recording counts as "new" if it's a loose audio file with no working
  folder or transcript yet. Once it's inside its own folder being
  processed, re-running the script won't create a duplicate for it.
- To force a full redo of an already-finished recording, delete its
  final `.txt` from inside its `Pertemuan_N` folder (or its own folder,
  if not yet organized) and re-run.
- It skips descending into any folder already named `Pertemuan_N`, so it
  won't re-scan or touch stuff you've already organized.
- Pertemuan numbering is based on the audio file's **modified date**, not
  filename, so it's fine even with random recorder-generated filenames.
- No `ffprobe`/`ffmpeg`? Splitting and length/ETA info won't work —
  make sure both are on your PATH (Whisper needs `ffmpeg` anyway).
- The console window stays open at the end (press Enter to close).
- You can tune how aggressively it splits by editing the constants near
  the top of `auto_transcribe.py` (`TARGET_CHUNK_SECONDS`, etc.) if
  15-minute pieces aren't what you want.
