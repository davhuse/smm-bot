"""Standalone, guarded Telegram advertiser for the SMM account."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

from flask import Flask, jsonify
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smm-reklam")
STATE_FILE = Path("smm_delivery_state.json")
MIN_INTERVAL_SECONDS = 60 * 60
app = Flask(__name__)
status = {"state": "starting", "last_cycle": None, "last_error": None, "sent": 0}


def groups_from_env():
    raw = os.environ.get("SMM_TARGET_GROUPS", "")
    return list(dict.fromkeys(item.strip().lstrip("@") for item in raw.split(",") if item.strip()))


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(value):
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


async def run_publisher():
    session = os.environ.get("SMM_STRING_SESSION", "").strip()
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    message = os.environ.get("SMM_MESSAGE", "").strip()
    groups = groups_from_env()
    if not session or not api_id or not api_hash:
        status.update(state="configuration_error", last_error="Telegram oturum bilgileri eksik")
        return
    if not message or not groups:
        status.update(state="waiting_for_message_and_groups")
        log.info("SMM_MESSAGE ve SMM_TARGET_GROUPS tanimlanana kadar gonderim yok.")
        return
    interval = max(MIN_INTERVAL_SECONDS, int(os.environ.get("SMM_INTERVAL_MINUTES", "60")) * 60)
    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        status.update(state="configuration_error", last_error="Telegram oturumu yetkili degil")
        await client.disconnect()
        return
    status.update(state="running", last_error=None)
    while True:
        delivery_state, now = load_state(), time.time()
        for group in groups:
            if now - float(delivery_state.get(group, 0)) < interval:
                continue
            try:
                await client.send_message(await client.get_entity(group), message, link_preview=False)
                delivery_state[group] = time.time()
                save_state(delivery_state)
                status["sent"] += 1
                await asyncio.sleep(12)
            except FloodWaitError as exc:
                status["last_error"] = f"Telegram bekleme suresi: {exc.seconds}s"
                await asyncio.sleep(exc.seconds + 5)
            except RPCError as exc:
                status["last_error"] = f"@{group}: {type(exc).__name__}"
                log.warning("@%s gonderilemedi: %s", group, type(exc).__name__)
        status["last_cycle"] = datetime.now(timezone.utc).isoformat()
        await asyncio.sleep(60)


def background_runner():
    try:
        asyncio.run(run_publisher())
    except Exception as exc:
        status.update(state="crashed", last_error=f"{type(exc).__name__}: {exc}")
        log.exception("SMM yayincisi durdu")


@app.get("/")
@app.get("/health")
def health():
    return jsonify(status)


if __name__ == "__main__":
    Thread(target=background_runner, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
