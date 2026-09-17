# Telegram Auto-Sync for the College Archive Transcriber

Watches your private group, downloads any recording it hasn't seen before
into `Matematika Diskrit`, and runs your existing `archive_transcriber.py`
right in the same window so you watch it transcribe live. Checks every 30
minutes by default, and only ever downloads a file once.

## Why not a plain "bot"?

A Telegram *bot* can't join a private group through an invite link the way
you're describing, and generally can't read a group's history either. This
uses **your own account** through Telegram's official API (via the
`Telethon` library) instead — the same access level as opening Telegram
yourself, just automated. Nothing shady, but it does mean the login step
below is with your real account, once.

## Files

| File | What it does |
|---|---|
| `telegram_sync.py` | The watcher: checks the group, downloads new audio, triggers transcription |
| `config.json` | Your settings (API keys, folder, interval) |
| `Start Telegram Sync.bat` | Launches it minimized; put a shortcut to this in your Windows startup folder |
| `archive_transcriber.py` | Your existing transcriber (put your copy in the same folder) |

Put all four in the same folder — the `College Archive` root, next to
`archive_transcriber.py` and `Transcribe.bat`.

## One-time setup

1. **Get API credentials** (free, 2 minutes): go to
   <https://my.telegram.org>, log in with your phone number, click
   **API development tools**, create an app (name/platform don't matter),
   and copy the `api_id` and `api_hash` it gives you.

2. **Edit `config.json`**:
   ```json
   {
     "api_id": 12345678,
     "api_hash": "abcdef1234567890abcdef1234567890",
     "phone": "+628123456789",
     "invite_link": "https://t.me/+CixsHDFT9TRiMDM1",
     "archive_root": "C:\\Users\\Iven\\Documents\\College Archive",
     "subject_folder": "Matematika Diskrit",
     "check_interval_minutes": 30,
     "whisper_model": "medium"
   }
   ```
   - `archive_root` — double-check this is your real archive folder path.
   - `subject_folder` — every audio file from this group lands here.
   - `check_interval_minutes` — how often it checks Telegram (30 = light on
     CPU; it sleeps between checks, using ~0% CPU while waiting).

3. **Install the one new dependency**:
   ```
   pip install telethon
   ```
   (You already have `openai-whisper` and `rich` from the transcriber setup.)

4. **Log in once, manually**, from a normal terminal in this folder:
   ```
   python telegram_sync.py
   ```
   Telegram will send a login code to your Telegram app — type it in when
   asked (and your two-factor password too, if you have one set up). This
   creates `telegram_session` next to the script, so this step never
   happens again. Leave it running for a minute to confirm it says
   "Watching group for new recordings..." with no errors, then Ctrl+C to
   stop it.

5. **Make it start with Windows**:
   - Press `Win+R`, type `shell:startup`, hit Enter.
   - Put a shortcut to `Start Telegram Sync.bat` in that folder (or copy the
     `.bat` itself in there — either works).

That's it. From now on, every time you log into Windows, a minimized
window opens that:
- checks the group every 30 minutes,
- downloads anything new straight into `College Archive\Matematika Diskrit`,
- and automatically runs the transcriber on it — the window's text just
  switches from "Checking Telegram..." into your normal transcription
  dashboard when there's something to do, then switches back to watching.

## How it avoids re-downloading

Every file it downloads gets its Telegram message ID recorded in
`sync_state.json`, saved to disk immediately after each file (so an
interruption mid-download never loses track). On the next check it skips
anything already in that list — so re-running or rebooting never
re-downloads what you already have.

Don't delete `sync_state.json` unless you actually want everything
re-downloaded and re-transcribed as new "meetings."

## If something goes wrong

- Check `telegram_sync.log` in the same folder — every cycle, download,
  and error gets timestamped there.
- **"That invite link has expired"** — group invite links can expire or
  get revoked; get a fresh one from the group and update `invite_link` in
  `config.json`.
- **Nothing downloads** — make sure the account you logged in with
  (step 4) has actually joined/can access that group.

## A couple of things I assumed — flag me if wrong

- **Every audio file posted in that group belongs to "Matematika
  Diskrit."** If this group actually covers multiple subjects and you want
  them sorted automatically (e.g. by keywords in the caption or filename),
  tell me and I'll add that logic.
- Your archive lives at `C:\Users\Iven\Documents\College Archive` (from
  your transcriber's own README) — update `archive_root` in `config.json`
  if that's not right.
- I treat anything that's a voice note, audio file, or a document with an
  audio MIME type/extension as a recording to grab. If lecture recordings
  come through as plain video files or something unusual, let me know and
  I'll widen the filter.
