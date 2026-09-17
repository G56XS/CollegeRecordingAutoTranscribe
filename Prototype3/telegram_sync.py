#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Auto-Sync -> College Archive Transcriber
==================================================

Watches a private Telegram group (via invite link) for new audio recordings,
downloads anything it hasn't downloaded before, drops it straight into the
right subject folder of your College Archive, then runs the transcriber
in the SAME console window so you watch it happen live.

First run needs you to type in the login code Telegram sends you (one time
only -- after that a session file on disk keeps you logged in).

Setup:
    1. Get api_id + api_hash from https://my.telegram.org (Api Development Tools)
    2. Fill them into config.json, next to this script, along with your phone
       number in international format (e.g. "+628123456789")
    3. pip install telethon
    4. Run once manually:  python telegram_sync.py
       -> enter the login code Telegram sends your app (and your 2FA
          password if you have one). This only happens once.
    5. Once it's running and syncing happily, set it to launch at login
       (see "Start Telegram Sync.bat" / README.md).

Everything after that is automatic: it checks the group every
`check_interval_minutes`, downloads anything new, files it into
`archive_root\\subject_folder`, and calls archive_transcriber.py on the
archive whenever there's something new to transcribe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError, FloodWaitError
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.types import DocumentAttributeAudio, ChatInviteAlready

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_PATH = SCRIPT_DIR / "sync_state.json"
LOG_PATH = SCRIPT_DIR / "telegram_sync.log"
SESSION_PATH = SCRIPT_DIR / "telegram_session"

AUDIO_EXTS = {
    ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac",
    ".wma", ".amr", ".3gp", ".mp4", ".mkv", ".webm",
}


# --------------------------------------------------------------------------
# config / state
# --------------------------------------------------------------------------
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[ERROR] {CONFIG_PATH} not found. Copy config.json next to this "
              f"script and fill it in first.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("api_id") or cfg.get("api_hash") == "PUT_YOUR_API_HASH_HERE":
        print("[ERROR] config.json still has placeholder api_id/api_hash. "
              "Get real ones from https://my.telegram.org and fill them in.")
        sys.exit(1)
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"downloaded_ids": [], "last_message_id": 0}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_PATH)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def is_audio_message(msg) -> bool:
    if msg.voice or msg.audio:
        return True
    if msg.document:
        doc = msg.document
        if doc.mime_type and doc.mime_type.startswith("audio/"):
            return True
        for attr in doc.attributes:
            if isinstance(attr, DocumentAttributeAudio):
                return True
            fname = getattr(attr, "file_name", None)
            if fname and Path(fname).suffix.lower() in AUDIO_EXTS:
                return True
    return False


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


async def ensure_joined(client: TelegramClient, invite_link: str):
    invite_hash = invite_link.rstrip("/").split("/")[-1].lstrip("+")
    try:
        result = await client(ImportChatInviteRequest(invite_hash))
        return result.chats[0]
    except UserAlreadyParticipantError:
        info = await client(CheckChatInviteRequest(invite_hash))
        if isinstance(info, ChatInviteAlready):
            return info.chat
        # Fallback: we're a member but couldn't resolve directly -- try dialogs.
        async for dialog in client.iter_dialogs():
            if dialog.title and info.title and dialog.title == info.title:
                return dialog.entity
        raise
    except InviteHashExpiredError:
        raise RuntimeError(
            "That invite link has expired or is invalid. Get a fresh invite "
            "link from the group and update invite_link in config.json."
        )


# --------------------------------------------------------------------------
# core sync
# --------------------------------------------------------------------------
async def sync_once(client: TelegramClient, config: dict, state: dict) -> list[Path]:
    chat = await ensure_joined(client, config["invite_link"])

    subject_dir = Path(config["archive_root"]) / config["subject_folder"]
    subject_dir.mkdir(parents=True, exist_ok=True)

    downloaded_ids = set(state.get("downloaded_ids", []))
    new_files: list[Path] = []

    min_id = state.get("last_message_id", 0)
    messages = [m async for m in client.iter_messages(chat, min_id=min_id, reverse=True)]

    for msg in messages:
        state["last_message_id"] = max(state.get("last_message_id", 0), msg.id)

        if msg.id in downloaded_ids or not is_audio_message(msg):
            continue

        filename = None
        if msg.file and msg.file.name:
            filename = msg.file.name
        if not filename:
            date_str = msg.date.strftime("%Y-%m-%d_%H%M")
            ext = ".ogg" if msg.voice else ".mp3"
            filename = f"{date_str}_{msg.id}{ext}"

        dest_path = unique_path(subject_dir / filename)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

        logging.info("Downloading %s (message %s)...", filename, msg.id)
        print(f"  -> downloading: {filename}")
        try:
            await client.download_media(msg, file=str(tmp_path))
        except FloodWaitError as e:
            logging.warning("Rate-limited, waiting %s seconds", e.seconds)
            await asyncio.sleep(e.seconds + 1)
            await client.download_media(msg, file=str(tmp_path))
        tmp_path.rename(dest_path)

        downloaded_ids.add(msg.id)
        state["downloaded_ids"] = list(downloaded_ids)
        save_state(state)  # persist after every single file -> crash-safe
        new_files.append(dest_path)

    save_state(state)
    return new_files


def run_transcriber(config: dict) -> None:
    """Runs archive_transcriber.py in-process so its live dashboard shows up
    right here in this same window."""
    archive_root = config["archive_root"]
    transcriber_path = Path(archive_root) / "archive_transcriber.py"
    if not transcriber_path.exists():
        # Fall back to looking next to this script.
        transcriber_path = SCRIPT_DIR / "archive_transcriber.py"
    if not transcriber_path.exists():
        print("[WARN] Could not find archive_transcriber.py -- new audio was "
              "downloaded but not transcribed. Put archive_transcriber.py "
              "in the archive root or next to telegram_sync.py.")
        return

    sys.path.insert(0, str(transcriber_path.parent))
    import importlib
    module_name = "archive_transcriber"
    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
        archive_transcriber = sys.modules[module_name]
    else:
        archive_transcriber = importlib.import_module(module_name)

    try:
        archive_transcriber.main([
            archive_root,
            "--model", config.get("whisper_model", "medium"),
            "--no-pause",
        ])
    except SystemExit:
        pass
    except Exception:
        logging.exception("Transcriber run failed")


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------
async def main_loop() -> None:
    config = load_config()
    state = load_state()
    interval = max(1, int(config.get("check_interval_minutes", 30))) * 60

    client = TelegramClient(str(SESSION_PATH), config["api_id"], config["api_hash"])

    async with client:
        if not await client.is_user_authorized():
            print("First-time login -- check your Telegram app for the code.")
            await client.start(phone=config["phone"])
            print("Logged in. This won't be needed again.\n")

        print(f"Watching group for new recordings (checking every "
              f"{config.get('check_interval_minutes', 30)} min). Ctrl+C to stop.\n")

        while True:
            timestamp = asyncio.get_event_loop().time()
            try:
                print("Checking Telegram for new files...")
                new_files = await sync_once(client, config, state)
                if new_files:
                    print(f"Downloaded {len(new_files)} new file(s). "
                          f"Handing off to the transcriber...\n")
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, run_transcriber, config)
                    print("\nBack to watching for new Telegram files...\n")
                else:
                    print("Nothing new.\n")
            except Exception:
                logging.exception("Sync cycle failed")
                print("[WARN] Sync cycle hit an error -- see telegram_sync.log. "
                      "Will retry next cycle.\n")

            await asyncio.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\nStopped.")
