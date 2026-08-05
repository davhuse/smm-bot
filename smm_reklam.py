"""Render'da calisan, SMM hesabi (@SosyalPazarSMM) icin bagimsiz Telegram reklam yayincisi."""

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
MIN_INTERVAL_SECONDS = 60 * 60  # 1 saat minimum
app = Flask(__name__)
status = {"state": "starting", "last_cycle": None, "last_error": None, "sent": 0}

# ── Varsayılan ayarlar (Render ortam değişkenleri bunları ezer) ──────────────
DEFAULT_API_ID   = "31076280"
DEFAULT_API_HASH = "7ba4072dcf0a05a7ccf80e570866b6d8"
DEFAULT_SESSION  = (
    "1AZWarzcBu3UZfk5_JqM6uQ79PaU4GCOX90-IFu7Ne4ssOo2dikO2cgWjKM-j_V95Yh"
    "G12dkQJUcqyfUxLiEp7FoUTWUAg7zBkxyDl51EzqGmdCO36M2c-1TyuOoKi5XHV_NeSK"
    "pj-0xbl9CKOL2L9auuU7z0vGjQlvt5leKxu4fFkeLlSj3mwBE_z4eJ5hUq61qwy3gsUQ"
    "K0DEgmqnVcpxVA5VQ-PfdFay8pqt3oH4xBY4mjysblTJU5jW3BiNBTzmyiM2McQV2wvd"
    "_PjhiVUij5IBqx5SAK-4urYCS3gHWCyl-dNmfOlccpm_-UUjPGHbReGIR5BIPtGM5PTKh"
    "LzDuyk0F9vEQ="
)
DEFAULT_MESSAGE = (
    "🚀 *SMM PANEL — EN UCUZ SOSYAL MEDYA HİZMETLERİ* 🚀\n\n"
    "Instagram, TikTok, YouTube ve Telegram için Türkiye'nin en hızlı SMM hizmetleri!\n\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
    "🔥 *HİZMETLERİMİZ*\n"
    "⚡ Instagram Gerçek Takipçi & Beğeni\n"
    "⚡ TikTok Keşfet & Takipçi Paketleri\n"
    "⚡ YouTube İzlenme, Abone & 4000 Saat\n"
    "⚡ Telegram Kanal Üyesi & Anlık Görüntülenme\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
    "✅ 7/24 Otomatik Teslimat | Şifresiz | Bayi Fiyatları\n\n"
    "👉 Sipariş & bilgi için DM: @SosyalPazarSMM"
)
DEFAULT_GROUPS = (
    "kuponindirimsatis,satcek,kuponsat,ceksat,ticaretcanavari,alsatticarettz,"
    "letgoilanlari,kuponhesapsatis,kuponsatisgrup,kuponcekkodsatis,"
    "indirimkodusatis,alimsatimmerkezii,ticaretforumofficial,kuponsatislari0,"
    "yucekuponsatis,kupongrupta,kuponkodindirimilanlar,Kuponcekm,"
    "kodceksatismerkezi,ticaretyapn,kuponkodhesapilan,kodkuponmarketi,"
    "xalimsatiim,satiskodtakasi,kuponkodalimsatimm,ceksatkupon,"
    "kuponindirimpazari,zeroticaret,indirim363,ticaretgruptr"
)


def groups_from_env():
    raw = os.environ.get("SMM_TARGET_GROUPS", DEFAULT_GROUPS)
    return list(dict.fromkeys(item.strip().lstrip("@") for item in raw.split(",") if item.strip()))


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(value):
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:
        log.warning("State kaydetme hatasi: %s", exc)


async def run_publisher():
    api_id   = os.environ.get("TELEGRAM_API_ID",   DEFAULT_API_ID).strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH",  DEFAULT_API_HASH).strip()
    session  = os.environ.get("SMM_STRING_SESSION", DEFAULT_SESSION).strip()
    message  = os.environ.get("SMM_MESSAGE",        DEFAULT_MESSAGE).strip()
    groups   = groups_from_env()
    interval = max(MIN_INTERVAL_SECONDS,
                   int(os.environ.get("SMM_INTERVAL_MINUTES", "60")) * 60)

    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        status.update(state="configuration_error", last_error="StringSession gecersiz")
        await client.disconnect()
        return

    status.update(state="running", last_error=None)
    log.info("SMM yayincisi basladi — %d grup, %d sn aralik", len(groups), interval)

    while True:
        delivery_state = load_state()
        now = time.time()
        for group in groups:
            if now - float(delivery_state.get(group, 0)) < interval:
                continue
            try:
                await client.send_message(await client.get_entity(group), message, link_preview=False)
                delivery_state[group] = time.time()
                save_state(delivery_state)
                status["sent"] += 1
                log.info("Gonderildi -> @%s", group)
                await asyncio.sleep(15)
            except FloodWaitError as exc:
                status["last_error"] = f"FloodWait {exc.seconds}s"
                log.warning("Flood wait @%s: %ss", group, exc.seconds)
                await asyncio.sleep(exc.seconds + 5)
            except RPCError as exc:
                status["last_error"] = f"@{group}: {type(exc).__name__}"
                log.warning("@%s gonderilemedi: %s", group, type(exc).__name__)
            except Exception as exc:
                status["last_error"] = f"@{group}: {type(exc).__name__}"
                log.exception("Gonderim hatasi @%s", group)
        status["last_cycle"] = datetime.now(timezone.utc).isoformat()
        await asyncio.sleep(60)


def background_runner():
    try:
        asyncio.run(run_publisher())
    except Exception as exc:
        status.update(state="crashed", last_error=f"{type(exc).__name__}: {exc}")
        log.exception("SMM runner durdu")


@app.get("/")
@app.get("/health")
def health():
    return jsonify(status)


if __name__ == "__main__":
    Thread(target=background_runner, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
