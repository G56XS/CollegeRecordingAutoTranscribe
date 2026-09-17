#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Archive Watcher
========================

Watches one or more Telegram groups for new recordings, downloads anything
it hasn't seen before straight into the right subject folder of your
College Archive, then kicks off archive_transcriber.py so it gets turned
into text automatically. Meant to run quietly in the background, started
once when Windows logs in.

How it stays cheap on CPU:
  * New messages are pushed to us the instant they arrive (Telegram's own
    MTProto connection), not polled. The process just sleeps until
    something happens.
  * A full re-scan of each group only happens once at startup (to catch
    anything sent while your PC was off) and then again every
    `poll_interval_minutes` (default 30) purely as a safety net, in case a
    push update was ever missed. Each scan only looks at the newest ~200
    messages per group, which is instant.
  * The transcriber is only launched after a short quiet period following a
    download (so five files arriving at once triggers one run, not five),
    and never more than one copy of it runs at a time. If new files land
    while it's already working, it simply runs again right after.

Setup:
    pip install -U telethon
    Fill in config.json next to this file (see SETUP.md), then run once:
        python telegram_watcher.py
    to log in interactively (you'll get a Telegram login code). After that
    it remembers you via the .session file and needs no input at all, so it
    can be launched silently at every Windows login.

Usage:
    python telegram_watcher.py
    python telegram_watcher.py --config "C:\\path\\to\\config.json"
    python telegram_watcher.py --once     (single scan, no live watching — good for testing)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from telethon import TelegramClient, events
    from telethon.errors import (
        UserAlreadyParticipantError,
        InviteHashExpiredError,
        InviteHashInvalidError,
        FloodWaitError,
    )
    from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeFilename
except ImportError:
    print("Telethon isn't installed. Run:  pip install -U telethon")
    sys.exit(1)

# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent

AUDIO_EXTS = {
    ".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".wma",
    ".m4b", ".mp4", ".mkv", ".webm", ".amr", ".3gp",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(HERE / "watcher.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("watcher")
logging.getLogger("telethon").setLevel(logging.WARNING)  # keep the noisy library quiet


def load_config(path: Path) -> dict:
    if not path.exists():
        log.error(f"Config file not found: {path}")
        log.error("Copy config.example.json to config.json and fill it in first.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    required = ["api_id", "api_hash", "phone", "groups"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        log.error(f"config.json is missing: {', '.join(missing)}")
        sys.exit(1)
    cfg.setdefault("session_name", "archive_session")
    cfg.setdefault("root_folder", str(HERE))
    cfg.setdefault("poll_interval_minutes", 30)
    cfg.setdefault("transcriber_script", str(HERE / "archive_transcriber.py"))
    cfg.setdefault("transcriber_args", ["--no-pause"])
    cfg.setdefault("quiet_period_seconds", 20)
    cfg.setdefault("history_scan_limit", 300)
    return cfg


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("downloaded_state.json was unreadable, starting fresh.")
    return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def is_audio_message(message) -> bool:
    if message.voice or message.audio:
        return True
    if message.document:
        mime = (message.document.mime_type or "")
        if mime.startswith("audio/"):
            return True
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                if Path(attr.file_name).suffix.lower() in AUDIO_EXTS:
                    return True
            if isinstance(attr, DocumentAttributeAudio):
                return True
    return False


def guess_filename(message, chat_title: str) -> str:
    """Best available filename, falling back to something readable + unique."""
    if message.document:
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename) and attr.file_name:
                return attr.file_name
    ext = ".ogg" if message.voice else ".mp3"
    if message.document and message.document.mime_type:
        mime = message.document.mime_type
        ext_map = {
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
            "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
            "audio/flac": ".flac", "audio/aac": ".aac",
        }
        ext = ext_map.get(mime, ext)
    stamp = message.date.strftime("%Y-%m-%d_%H%M")
    safe_title = re.sub(r"[^\w\-]+", "_", chat_title).strip("_") or "telegram"
    return f"{safe_title}_{stamp}_{message.id}{ext}"


def unique_safe_path(folder: Path, filename: str) -> Path:
    dest = folder / filename
    if not dest.exists():
        return dest
    stem, suffix, i = dest.stem, dest.suffix, 2
    while dest.exists():
        dest = folder / f"{stem}_{i}{suffix}"
        i += 1
    return dest


def parse_invite_hash(link: str) -> str | None:
    m = re.search(r"(?:joinchat/|\+)([\w-]+)", link)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Transcriber launcher — one at a time, reruns itself if new work arrived
# --------------------------------------------------------------------------
class TranscriberRunner:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.running = False
        self.pending = False
        self.lock = asyncio.Lock()

    async def trigger(self):
        """Ask for a transcription pass. Coalesces bursts, never overlaps runs."""
        if self.running:
            self.pending = True
            return
        asyncio.create_task(self._run_loop())

    async def _run_loop(self):
        async with self.lock:
            self.running = True
            try:
                while True:
                    self.pending = False
                    await self._run_once()
                    if not self.pending:
                        break
                    log.info("New file arrived mid-run — running the transcriber again.")
            finally:
                self.running = False

    async def _run_once(self):
        script = self.cfg["transcriber_script"]
        root = self.cfg["root_folder"]
        args = [sys.executable, script, root, *self.cfg["transcriber_args"]]
        log.info(f"Starting transcriber: {' '.join(args)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning(f"Transcriber exited with code {proc.returncode}: "
                            f"{stderr.decode(errors='ignore')[-500:]}")
            else:
                log.info("Transcriber run finished.")
        except Exception as exc:
            log.error(f"Couldn't launch transcriber: {exc}")


# --------------------------------------------------------------------------
# Main watcher
# --------------------------------------------------------------------------
class ArchiveWatcher:
    def __init__(self, cfg: dict, config_dir: Path):
        self.cfg = cfg
        self.state_path = config_dir / "downloaded_state.json"
        self.state = load_state(self.state_path)
        self.root = Path(cfg["root_folder"]).expanduser().resolve()
        self.client = TelegramClient(
            str(config_dir / cfg["session_name"]), cfg["api_id"], cfg["api_hash"]
        )
        self.transcriber = TranscriberRunner(cfg)
        self.chat_folders: dict[int, Path] = {}   # chat_id -> destination folder
        self.chat_keys: dict[int, str] = {}       # chat_id -> state dict key

    # -- joining / resolving -------------------------------------------------
    async def resolve_groups(self):
        for group in self.cfg["groups"]:
            link = group["invite_link"]
            folder_name = group["subject_folder"]
            dest = self.root / folder_name
            dest.mkdir(parents=True, exist_ok=True)

            invite_hash = parse_invite_hash(link)
            entity = None
            if invite_hash:
                try:
                    await self.client(ImportChatInviteRequest(invite_hash))
                    log.info(f"Joined group for '{folder_name}'.")
                except UserAlreadyParticipantError:
                    pass
                except (InviteHashExpiredError, InviteHashInvalidError):
                    log.error(f"Invite link for '{folder_name}' is invalid or expired: {link}")
                    continue
                except FloodWaitError as e:
                    log.warning(f"Rate-limited joining '{folder_name}', waiting {e.seconds}s.")
                    await asyncio.sleep(e.seconds)
                except Exception as exc:
                    log.warning(f"Could not import invite for '{folder_name}': {exc}")
                try:
                    check = await self.client(CheckChatInviteRequest(invite_hash))
                    entity = getattr(check, "chat", None)
                except Exception:
                    entity = None
            if entity is None:
                try:
                    entity = await self.client.get_entity(link)
                except Exception as exc:
                    log.error(f"Can't resolve chat for '{folder_name}' ({link}): {exc}")
                    continue

            chat_id = entity.id
            self.chat_folders[chat_id] = dest
            self.chat_keys[chat_id] = str(chat_id)
            self.state.setdefault(self.chat_keys[chat_id], [])
            log.info(f"Watching '{getattr(entity, 'title', folder_name)}' -> {dest}")

    # -- downloading -----------------------------------------------------
    async def maybe_download(self, message, chat_id: int) -> bool:
        if not is_audio_message(message):
            return False
        key = self.chat_keys.get(chat_id)
        if key is None:
            return False
        file_uid = str(message.file.id) if message.file and message.file.id else f"msg{message.id}"
        if file_uid in self.state[key]:
            return False  # already downloaded

        dest_folder = self.chat_folders[chat_id]
        filename = guess_filename(message, dest_folder.name)
        dest_path = unique_safe_path(dest_folder, filename)

        log.info(f"Downloading '{filename}' -> {dest_folder.name}/")
        try:
            await self.client.download_media(message, file=str(dest_path))
        except Exception as exc:
            log.error(f"Download failed for message {message.id}: {exc}")
            return False

        # Stamp the file with when it was actually recorded/sent, not "now",
        # so the transcriber sorts and dates it correctly.
        try:
            ts = message.date.replace(tzinfo=timezone.utc).timestamp()
            import os
            os.utime(dest_path, (ts, ts))
        except Exception:
            pass

        self.state[key].append(file_uid)
        save_state(self.state_path, self.state)
        log.info(f"Saved: {dest_path.name}")
        return True

    async def scan_chat(self, chat_id: int) -> int:
        found = 0
        async for message in self.client.iter_messages(
            chat_id, limit=self.cfg["history_scan_limit"]
        ):
            if await self.maybe_download(message, chat_id):
                found += 1
        return found

    async def full_scan(self):
        log.info("Scanning all watched groups for anything new...")
        total = 0
        for chat_id in self.chat_folders:
            total += await self.scan_chat(chat_id)
        if total:
            log.info(f"Scan complete — {total} new file(s) downloaded.")
            await self.schedule_transcription()
        else:
            log.info("Scan complete — nothing new.")

    async def schedule_transcription(self):
        await asyncio.sleep(self.cfg["quiet_period_seconds"])
        await self.transcriber.trigger()

    # -- event-driven real-time watching ----------------------------------
    def register_live_handler(self):
        chat_ids = list(self.chat_folders.keys())

        @self.client.on(events.NewMessage(chats=chat_ids))
        async def handler(event):
            downloaded = await self.maybe_download(event.message, event.chat_id)
            if downloaded:
                await self.schedule_transcription()

    async def periodic_rescan(self):
        interval = self.cfg["poll_interval_minutes"] * 60
        while True:
            await asyncio.sleep(interval)
            await self.full_scan()

    async def run(self, once: bool = False):
        await self.client.start(phone=self.cfg["phone"])
        log.info("Logged in to Telegram.")
        await self.resolve_groups()
        if not self.chat_folders:
            log.error("No groups resolved — check config.json. Exiting.")
            return

        await self.full_scan()  # catch up on anything missed while offline
        if once:
            log.info("Single-scan mode (--once) — exiting.")
            return

        self.register_live_handler()
        asyncio.create_task(self.periodic_rescan())
        log.info(f"Now watching live. Safety re-scan every "
                 f"{self.cfg['poll_interval_minutes']} minutes. Idle otherwise (low CPU).")
        await self.client.run_until_disconnected()


def main():
    parser = argparse.ArgumentParser(description="Telegram Archive Watcher")
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--once", action="store_true",
                         help="scan once and exit, instead of watching live")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    cfg = load_config(config_path)
    watcher = ArchiveWatcher(cfg, config_path.parent)

    try:
        asyncio.run(watcher.run(once=args.once))
    except KeyboardInterrupt:
        log.info("Stopped.")


if __name__ == "__main__":
    main()
