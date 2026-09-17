# Telegram Archive Watcher — Setup

This adds automatic Telegram downloading on top of the existing
`archive_transcriber.py`. Put all these files in the **same folder**
(your "College Archive" folder, next to `archive_transcriber.py`):

```
College Archive/
├── archive_transcriber.py      (already had this)
├── telegram_watcher.py         (new)
├── config.json                 (new — you create this, see below)
├── Start_Watcher.bat           (new)
├── watcher.log                 (created automatically)
├── downloaded_state.json       (created automatically)
├── archive_session.session     (created automatically, after first login)
└── Matematika Diskrit/         (created automatically)
    └── ...recordings + transcripts...
```

## 1. Get a Telegram API ID and hash (one-time, ~2 minutes)

Telegram requires this for any program that logs in as you (this is how
Telethon works — bots can't read messages in a group they weren't
explicitly given admin rights in, so we log in as your own account
instead, exactly like Telegram Desktop does).

1. Go to **https://my.telegram.org** and log in with your phone number.
2. Click **API development tools**.
3. Fill in any app name/short name (e.g. "Archive Watcher") and submit.
4. You'll get an **api_id** (a number) and **api_hash** (a long string).
   Keep these private — they identify your account, like a password.

## 2. Install dependencies

```
pip install -U telethon
```

(You should already have `openai-whisper`, `rich`, and `ffmpeg` from the
transcriber's own setup.)

## 3. Configure

Copy `config.example.json` to `config.json` in the same folder, then edit it:

```json
{
  "api_id": 12345678,
  "api_hash": "abcdef1234567890abcdef1234567890",
  "phone": "+62812xxxxxxx",
  "groups": [
    {
      "invite_link": "https://t.me/+CixsHDFT9TRiMDM1",
      "subject_folder": "Matematika Diskrit"
    }
  ]
}
```

- `phone` — your Telegram account's phone number, international format.
- `groups` — one entry per Telegram group you want watched. Add more
  entries later for other subjects; each just needs its own invite link
  and folder name.
- Leave `root_folder` as `"."` to keep everything in this same folder
  (matches how `Transcribe.bat` already works). Change it only if you
  want recordings to land somewhere else.

## 4. First run (does a one-time interactive login)

Double-click `Start_Watcher.bat`, or run:

```
python telegram_watcher.py
```

The first time only, Telegram will text/send you a login code — type it
in when asked (and your 2FA password, if you have one set). This creates
`archive_session.session`, so **every run after this is fully silent** —
no more prompts, ever, even after restarting your PC.

It will then join the group (if you're not already in it), scan its
history, download every recording it hasn't seen before straight into
`Matematika Diskrit\`, wait ~20 seconds, and kick off the transcriber on
them automatically.

Leave the window open (or minimized) and it keeps watching live from then
on — new recordings get downloaded and queued for transcription within
seconds of being posted, no polling delay.

## 5. Make it start automatically when you log into Windows

**Easiest option — Startup folder:**
1. Press `Win + R`, type `shell:startup`, press Enter.
2. Right-click inside that folder → **New → Shortcut**.
3. Browse to `Start_Watcher.bat`, finish.

That's it — it now launches (with a visible console window) every time
you log in.

**Better option — Task Scheduler (can run hidden, more reliable):**
1. Press `Win + R`, type `taskschd.msc`, press Enter.
2. **Create Task…** (not "Basic Task", so you get the extra options).
3. **General** tab: name it "Telegram Archive Watcher". Tick **Run only
   when user is logged on**. Tick **Run with highest privileges** (not
   required, but avoids odd permission issues).
4. **Triggers** tab → **New…** → Begin the task: **At log on** → OK.
5. **Actions** tab → **New…**:
   - Program/script: `C:\path\to\College Archive\Start_Watcher.bat`
   - Start in: `C:\path\to\College Archive`
6. **Conditions** tab: untick "Start the task only if the computer is on
   AC power" if this is a laptop, so it still runs on battery.
7. OK, enter your Windows password if prompted.

To run it invisibly (no console window popping up at login), change the
Action's program to `pythonw.exe` instead of the `.bat` file, pointing
directly at `telegram_watcher.py`:
   - Program/script: `C:\Path\To\Python\pythonw.exe`
   - Arguments: `telegram_watcher.py`
   - Start in: `C:\path\to\College Archive`
Check `watcher.log` in that folder to see what it's doing, since there's
no window to watch anymore.

## How it behaves

- **Detecting duplicates:** every downloaded file's unique Telegram ID is
  recorded in `downloaded_state.json`. Anything already in there is
  skipped — restarting the watcher, or Telegram re-sending old history,
  never re-downloads a file. If a file somehow already exists as a plain
  file on disk (not yet in a `Pertemuan_N` folder) the transcriber's own
  behavior applies too: it just treats it as one more file to transcribe.
- **Catching up after being offline:** every time the watcher starts
  (i.e. every time you boot your PC) it immediately scans the group's
  recent history before switching to live watching, so anything sent
  while your PC was off still gets picked up.
- **Real-time + a safety net:** new messages are pushed to the watcher
  the instant they're sent (no polling loop burning CPU). A full re-scan
  additionally runs every 30 minutes (configurable via
  `poll_interval_minutes`) purely as a backstop in case a push update
  was ever missed — each such scan only checks the newest ~300 messages
  and finishes in under a second, so it's not something you'll notice.
- **CPU use while idle:** effectively zero — the process is asleep,
  waiting on its Telegram connection, the vast majority of the time.
- **Queuing multiple new files:** the watcher waits ~20 seconds after a
  download (so a burst of several files posted together triggers one
  transcription run, not several), then runs `archive_transcriber.py`.
  That script already discovers *every* untranscribed file in the whole
  archive tree and works through them one at a time — so if a new file
  lands while a previous batch is still transcribing, the watcher just
  runs the transcriber again right after it finishes, and it picks up
  what's new. You'll see this in the same console/log the transcriber
  already uses (its Rich dashboard, if installed).

## If something goes wrong

- Check `watcher.log` in the archive folder first — everything gets
  timestamped there, in addition to the console if one's visible.
- "Invite link is invalid or expired" — group invite links can be
  regenerated by an admin; grab the current one and update `config.json`.
- If you ever want to fully reset what's considered "downloaded", delete
  `downloaded_state.json` (this does **not** delete any files — it'll
  just re-check history, and files already present in a `Pertemuan_N`
  folder or already downloaded to the loose folder won't disappear or
  duplicate, since the transcriber matches by filename too).
