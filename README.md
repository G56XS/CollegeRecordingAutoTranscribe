# 🏋️ Habit Tracker Discord Bot

A clean workout streak tracker for Discord. SQLite storage, slash commands,
DM reminders with a one-tap "done" button, a live status that shows your
streak to anyone who looks at the bot — and every response (except `/help`)
is rendered as a polished Pillow image card instead of a plain embed.

## Fixing "CommandSignatureMismatch"

If a slash command throws `CommandSignatureMismatch`, Discord's cached
definition of that command no longer matches your code (usually from
changing a command's options across a few restarts before the sync fully
propagated). Two things fix it:

1. **While developing:** set `DEV_GUILD_ID` in `.env` to your server's ID.
   Guild-scoped syncs apply instantly instead of the up-to-an-hour delay
   global syncs can take, so your Discord client stops seeing stale
   signatures.
2. **If it's already stuck:** run `python reset_commands.py` once. It wipes
   every cached command definition and re-registers everything from
   scratch. Then restart your Discord client (`Ctrl+R`) to drop its own
   local cache before running `bot.py` normally again.

## ⚠️ Before anything else: rotate your token

The token you shared earlier is public now — reset it at
**Discord Developer Portal → Your App → Bot → Reset Token** before deploying
this bot. Never paste a real token into a chat, file, or public repo.

## Using it anywhere (DMs with friends, servers it's not in)

By default a bot's commands only work in servers it's been invited to (and in
a DM with the bot itself, if you share a server). To use `/log`, `/streak`,
etc. in a DM with a friend, a group chat, or any server — even ones the bot
was never added to — it needs to be installed as a **user app** instead of
just a guild bot. Every command below now sets `allowed_installs` /
`allowed_contexts` so this works, but you still need to flip two switches:

1. **Developer Portal → your app → Installation** — under "Installation
   Contexts," check **User Install** (keep Guild Install checked too), then
   Save Changes.
2. Copy the **Install Link** from that same page, open it, and choose
   **Add to My Apps** (not "Add to Server"). This installs it to your
   account rather than a specific server.
3. Restart the bot (`python bot.py`) so it re-syncs commands with the new
   context/install flags.

After that, typing `/` in any DM, group chat, or server should show this
bot's commands, run through your account.

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env`:
   - `DISCORD_TOKEN` — your new bot token
   - `PRESENCE_USER_ID` — your Discord user ID (Settings → Advanced → turn on
     Developer Mode, then right-click your name → Copy User ID). This is
     whose streak shows in the bot's "Watching ___'s streak" status.
   - `TIMEZONE_OFFSET` — hours from UTC (7 = Jakarta/Bangkok, already set)
   - `REMINDER_START_HOUR` / `REMINDER_END_HOUR` — the bot now DMs you **every hour** in this local-time window (default `8`–`22`) until you log that day's workout, then it stops for the rest of the day. Set the old `REMINDER_HOURS` (comma-separated 24h hours) instead if you want fixed times back.
   - `PRESENCE_PRONOUN` — used only when your streak breaks, e.g. "Watching LVENS not doing his habits" (default `his`, change to whatever fits)

3. **Enable the Members intent**
   In the Developer Portal → Bot → Privileged Gateway Intents, turn on
   **Server Members Intent**. The bot needs this to DM reminders.

4. **Invite the bot**
   Developer Portal → OAuth2 → URL Generator → check `bot` and
   `applications.commands`, permission `Send Messages`. Open the generated
   URL and add it to your server.

5. **Run it**
   ```bash
   python bot.py
   ```
   Slash commands sync automatically on startup (can take up to an hour to
   appear globally the very first time; usually it's instant).

## Commands

| Command | What it does |
|---|---|
| `/log [note]` | Logs today's workout, updates your streak |
| `/streak` | Shows current streak, longest streak, total workouts, today's workout, and a 14-day calendar |
| `/routine` | Shows the full workout cycle with today's day highlighted |
| `/leaderboard` | Ranks everyone in the server by current streak |
| `/reminders` | Toggles your hourly DM reminders on/off (stop once you log) |
| `/pause <days>` | Vacation mode — freezes your streak and reminders for 1-90 days starting today |
| `/resume` | Ends vacation mode early |
| `/journal add <entry>` | Saves a "cool thing you did today" with today's date |
| `/journal view` | Browse all your saved entries, newest first, with Prev/Next buttons |
| `/journal delete <id>` | Deletes an entry (its `#id` is shown next to it in `/journal view`) |
| `/trade add [note] [image] [symbol] [direction] [pnl]` | Logs a trade — screenshot, notes, symbol, long/short, and P&L are all optional and independent |
| `/trade view` | Browse your trading journal, one trade per page with the chart shown full-size |
| `/trade delete <id>` | Deletes a trade entry |
| `/goals` | Shows today's goals card on demand (it's also sent automatically — see below) |
| `/help` | Shows this command list, in-Discord |

### Daily goals & personal reminders

A separate, fixed-schedule set of DMs (`cogs/daily_goals.py`), on top of the
hourly workout reminders above. Sent to `PRESENCE_USER_ID` only — this part
of the bot is single-user by design:

| When | What it sends |
|---|---|
| The moment the bot starts (or the web control panel is opened for the first time that day, if the bot was already running) | 🌅 Shower + brush your teeth, plus today's goals |
| The next top of the hour after that | Same card again, once, then done — e.g. start the bot at 7:38 and it repeats at 8:00 |
| `GOAL_WIND_DOWN_TIME` (default **19:00**) | 🖥️ Stop using the PC |
| `GOAL_NIGHT_CHECKIN_TIMES` (default **20:00, 21:00, 21:30, 21:45**) | 🪥 Brush your teeth again, 📵 limit digital usage, 😴 sleep countdown — fires once per time in the list, so the nagging repeats and tightens up as bedtime nears |
| `GOAL_SLEEP_TIME` (default **22:00**) | 😴 Lights out |

The goal line (`config.GOALS` — "Sampe kapan mo hidup kayak gini?") is
appended to every one of these cards. Times accept `HH:MM` (or a bare hour
like `19`); `GOAL_NIGHT_CHECKIN_TIMES` takes a comma list, e.g.
`GOAL_NIGHT_CHECKIN_TIMES=20:00,21:00,21:30,21:45` in `.env`. Change the
goal or card text directly in `cogs/daily_goals.py`.

### Trading journal notes
- `/trade add` works with just a note, just an image, or both — nothing is required except at least filling in *something*.
- Attach a chart screenshot directly in the slash command's `image` field; Discord uploads it and the bot stores the resulting CDN link, so as long as you don't delete the original message the image stays viewable forever in `/trade view`.
- `direction` gives you a quick Long 📈 / Short 📉 tag with matching embed color; `pnl` is free text so `+2.3%`, `-$40`, or `breakeven` all work.

### Vacation mode

`/pause <days>` freezes your streak and turns off reminders for however many
days you give it (1-90), starting today. Life happens — this is for actual
trips, not an excuse button:

- While paused, missing days doesn't break your streak and you won't get
  hourly DM nudges.
- You can still log a workout during the pause if you want — it just keeps
  counting like normal, and the rest of the pause window stays protected in
  case you miss more days after that.
- There's a **one-day grace period** right after the pause ends: log that
  next day and your streak keeps climbing as if nothing happened. Wait
  longer than that and it resets to 1, same as missing any other day.
- `/resume` ends it early and goes straight back to normal tracking.
- `/streak` shows a banner while a pause is active, and the "Watching ___"
  presence status shows "on vacation" too, so nothing looks broken while
  you're away.

## The workout cycle

Tied directly to your streak day, so it always lines up with your presence
status. The default is:

| Streak day | Workout |
|---|---|
| 1 | 💪 Push Day — Push-ups, 2 sets to failure |
| 2 | 🚶 Rest Day — Walk/run |
| 3 | 🧗 Pull Day — Pull-ups, 2 sets to failure |
| 4 | 🦵 Leg Day — Squats, 2 sets to failure |
| 5 | 🚶 Rest Day — Walk/run |

...then it repeats (day 6 = Push again, and so on). Reminder DMs, `/log`
confirmations, and `/streak` all show the correct day automatically —
nothing to configure.

**Editing it:** the cycle lives in `routine.json` (auto-created next to
`routine.py` from the defaults above the first time the bot runs), and the
easiest way to change it is the **Routine** tab in the admin control panel
(`habit_admin`) — rename days, change the emoji/exercise, add or remove
days, drag to reorder, then Save. Changes apply immediately, no bot restart
needed. You can also hand-edit `routine.json` directly if you prefer.

## About the Rich Presence image cards

Quick honest note: the fancy "Playing for 2h / Join" cards with a big cover
image (like your screenshot) are Discord's **Game SDK Rich Presence**, which
only works when someone's own game client is running locally and connects to
Discord over IPC — it's not something a bot account can display for itself.
What this bot *can* do — and does — is set its own status line, which now
reads "Watching `<you>` on day N" and climbs by one every day you log a
workout, dropping to "Watching `<you>` not doing `<pronoun>` habits" the
moment a day is missed. It refreshes every 5 minutes so it stays accurate
without needing a restart.

The two image assets you uploaded (`ca0772fbe...`, `7c04db17...`) are ready
to go the moment you build an actual game/app that connects via the Game SDK
— just drop their asset keys into your IPC presence payload as `large_image`
/ `small_image`. They can't be wired into this bot, since bots don't have
that channel.

## Project structure

```
habit-bot/
├── bot.py               # entrypoint, presence + reminder loops
├── config.py             # reads .env, holds shared constants
├── database.py            # SQLite (aiosqlite) — users, streaks, workout log
├── routine.py              # the 5-day push/rest/pull/leg/rest cycle
├── reset_commands.py        # one-time fix for a stuck command-sync mismatch
├── cogs/
│   ├── card_kit.py           # shared Pillow renderer every card is built from
│   ├── habits.py              # /log /streak /leaderboard /reminders /help
│   ├── journal.py              # /journal add /journal view /journal delete
│   ├── trading.py               # /trade add /trade view /trade delete
│   └── daily_goals.py            # /goals + the goals/shower/teeth/PC/sleep card content
├── requirements.txt
├── .env.example
└── habits.db              # created automatically on first run
```

## About the image cards

Every response except `/help` (which stays a normal Discord embed) is
rendered as a PNG through `cogs/card_kit.py` — a dark rounded card with a
colored accent, stat rows, progress bars, a 14-day calendar strip, and a
footer, all drawn with Pillow using the Poppins font and real color emoji.
Trading journal entries embed the chart screenshot directly inside the card
instead of as a separate Discord image. Pagination (`/journal view`,
`/trade view`) re-renders the card and swaps the message's attachment on
each button press.

## Data

Everything — streaks, workout history, journal entries, and trading journal
entries — lives in `habits.db`, a single SQLite file created automatically
next to `bot.py` the first time you run the bot. It's purely local: nothing
is sent anywhere else. Back it up by copying that one file; delete it to
start completely fresh. Trade screenshots themselves are stored on Discord's
CDN (like any uploaded image); the database just keeps the link.
