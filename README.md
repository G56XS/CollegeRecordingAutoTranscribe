# HTracker — Web-Integrated Habit Tracker

A personal accountability system combining a **Discord bot** and a local **web control panel**.

Track workout streaks, daily journals, and a trading journal — all stored in a single local SQLite database. Every bot response (except `/help`) is rendered as a polished image card. The web dashboard lets you manage users, view data, export journals, and control the bot process from one place.

```
┌─────────────────────┐         ┌──────────────────────┐
│   Discord Bot       │◄───────►│  Web Control Panel   │
│   (habit-bot/)      │  shared │  (habit_admin/)      │
│                     │  SQLite │  http://127.0.0.1:8420
│  /log  /streak      │         │                      │
│  /journal  /trade   │         │  Data · Console ·    │
│  Image cards        │         │  Start/Stop bot      │
└─────────────────────┘         └──────────────────────┘
```

---

## Features

### Discord Bot
- **Workout streak tracking** with vacation support (streaks continue across planned breaks)
- **5-day rotating routine** (Push → Rest → Pull → Leg → Rest) automatically tied to your streak day
- **Beautiful image cards** (Pillow) for `/log`, `/streak`, `/journal`, `/trade` — dark rounded cards with accent colors, progress bars, 14-day calendar strip, and real emoji
- **Journal** — save daily notes / affirmations, browse with pagination
- **Trading journal** — log XAUUSD (or any) trades with optional screenshot, symbol, direction (Long/Short), PnL, Live/Backtest type, and risk-reward notes
- **DM reminders** at configurable hours (default 7 AM & 3 PM local time)
- **Live presence status** — “Watching *you* on day N” that updates with your streak
- Works as a **user-installable app** (usable in DMs, group chats, and servers the bot isn’t in)

### Web Control Panel (“Habit Control Room”)
- Overview of all members, streaks, and activity
- Per-user detail: calendar editor, journal entries, trades, reminders toggle
- Reset or permanently delete user data
- Live **Console** tab showing both web and bot logs, with Start / Stop / Restart for `bot.py`
- Export journals to text files
- Serves permanent local copies of trade screenshots (Discord CDN links expire)
- Dark / light theme, runs purely on `127.0.0.1` — nothing leaves your machine

---

## Project Structure

```
Web Integrated Habit Tracker/
├── Start Habit Control Room.bat          # One-click launcher (recommended)
├── Start Habit Control Room (silent).vbs # Silent version for Windows Startup
├── Kill Switch.bat
├── Enable / Disable Run Command (habitbot).bat
│
├── habit-bot/                            # Discord bot
│   ├── bot.py                            # Entrypoint, presence + reminder loops
│   ├── config.py                         # Loads .env, timezone helpers
│   ├── database.py                       # SQLite (aiosqlite) schema & queries
│   ├── routine.py                        # 5-day Push/Rest/Pull/Leg/Rest cycle
│   ├── cogs/
│   │   ├── card_kit.py                   # Shared Pillow card renderer
│   │   ├── habits.py                     # /log /streak /routine /leaderboard /reminders /help
│   │   ├── journal.py                    # /journal add|view|delete
│   │   └── trading.py                    # /trade add|view|delete
│   ├── assets/
│   │   ├── fonts/                        # Poppins + Noto Color Emoji
│   │   └── trade_images/                 # Permanent local copies of trade screenshots
│   ├── requirements.txt
│   ├── .env                              # Your Discord token & settings (do not commit)
│   └── habits.db                         # SQLite database (auto-created)
│
└── habit_admin/                          # Flask web dashboard
    ├── app.py
    ├── templates/index.html
    ├── static/css/style.css
    ├── static/js/app.js
    ├── static/img/                       # Background themes
    └── requirements.txt
```

---

## Quick Start

### 1. Prerequisites
- Python 3.10+
- A Discord application with a bot token ([Discord Developer Portal](https://discord.com/developers/applications))

### 2. Install dependencies

```bash
# Bot
cd "Web Integrated Habit Tracker/habit-bot"
pip install -r requirements.txt

# Web panel
cd "../habit_admin"
pip install -r requirements.txt
```

### 3. Configure the bot

Edit `habit-bot/.env`:

```env
DISCORD_TOKEN=your_bot_token_here
PRESENCE_USER_ID=your_discord_user_id          # whose streak the bot status shows
TIMEZONE_OFFSET=7                              # hours from UTC (7 = WIB / Western Indonesia)
REMINDER_HOURS=7,15                            # local hours for DM reminders
PRESENCE_PRONOUN=their                         # used when streak breaks
# DEV_GUILD_ID=1234567890                      # optional: for instant command sync while developing
```

> **Important:** Never commit or share your real token. If it was ever exposed, reset it in the Developer Portal.

### 4. Discord app settings (recommended)

To use the bot in DMs / group chats / any server:

1. Developer Portal → your app → **Installation**
2. Enable **User Install** (keep Guild Install checked)
3. Open the Install Link → **Add to My Apps**

### 5. Run everything

**Easiest (Windows):** double-click  
`Start Habit Control Room.bat`

This starts the web panel, which automatically starts the bot. Open:

**http://127.0.0.1:8420**

You’ll see both web and bot logs in the **Console** tab, plus Start / Stop / Restart buttons.

**Alternative:** run the bot alone

```bash
cd habit-bot
python bot.py
# or double-click habit.bat
```

**Silent autostart (Windows):**  
Put a shortcut to `Start Habit Control Room (silent).vbs` in your Startup folder (`Win+R` → `shell:startup`).

---

## Bot Commands

| Command | Description |
|---------|-------------|
| `/log [note]` | Log today’s workout and update streak |
| `/streak` | Current / longest streak, total workouts, 14-day calendar |
| `/routine` | Full 5-day cycle with today highlighted |
| `/leaderboard` | Rank server members by current streak |
| `/reminders` | Toggle 7 AM / 3 PM DM reminders |
| `/journal add <entry>` | Save a daily note or affirmation |
| `/journal view` | Browse entries (paginated image cards) |
| `/journal delete <id>` | Delete an entry |
| `/trade add` | Log a trade (note, image, symbol, direction, PnL, type, RR all optional) |
| `/trade view` | Browse trading journal with chart screenshots |
| `/trade delete <id>` | Delete a trade |
| `/help` | Command list (plain embed) |

### Built-in 5-day routine

| Streak day | Workout |
|------------|---------|
| 1 | 💪 Push Day — Push-ups, 2 sets to failure |
| 2 | 🚶 Rest Day — Walk / easy run |
| 3 | 🧗 Pull Day — Pull-ups, 2 sets to failure |
| 4 | 🦵 Leg Day — Squats, 2 sets to failure |
| 5 | 🚶 Rest Day — Walk / easy run |

(Then repeats. Edit `routine.py` to change exercises or cycle length.)

---

## Database

Everything lives in a single file: `habit-bot/habits.db`.

| Table | Purpose |
|-------|---------|
| `users` | Streaks, totals, reminder prefs, vacation fields |
| `workout_log` | One row per logged workout day + optional note |
| `journal_entries` | Free-form daily journal |
| `trading_journal` | Trades with symbol, direction, PnL, type, RR, image path |
| `vacation_periods` | Date ranges that preserve the streak |
| `bot_state` | Daily flags used by the bot’s scheduled features |

Backup = copy `habits.db`. Delete it to start fresh.

---

## Web Panel Highlights

- **Data mode** — member list, search, per-user calendar editor, journal & trade management
- **Console mode** — live combined logs + bot process control
- **Export** — download a user’s journal as `.txt`
- **Maintenance** — cleanup empty trade rows
- Theme toggle (dark / light) and nature-themed backgrounds

The panel intentionally has **no authentication** — it only binds to localhost.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `CommandSignatureMismatch` | Set `DEV_GUILD_ID` in `.env` while developing, or run `python reset_commands.py` once, then restart Discord (`Ctrl+R`) and the bot |
| Streak / presence looks frozen | Confirm `TIMEZONE_OFFSET` matches your real timezone. The bot uses local midnight defined by this offset, not the server’s OS clock |
| Trade images disappear later | The bot downloads screenshots to `assets/trade_images/` at add-time so they survive Discord CDN link expiry |
| Bot doesn’t appear in DMs | Enable **User Install** in the Developer Portal and reinstall the app to your account |

Utility scripts in `habit-bot/`:

- `reset_commands.py` — wipe and re-register all slash commands
- `reset_user.py` / `list_users.py` — quick DB helpers
- `cleanup_empty_trades.py` — remove incomplete trade rows
- `export_journals.py` — bulk journal export

---

## Privacy & Local-first

- All data stays on your machine
- No external analytics or cloud sync
- Trade screenshots are stored locally after the initial Discord upload
- The web panel is reachable only at `127.0.0.1`

---

## License / Notes

Personal project. Feel free to adapt the code for your own use.

For deeper details on the bot alone or the web panel alone, see the READMEs inside `habit-bot/` and `habit_admin/`.
