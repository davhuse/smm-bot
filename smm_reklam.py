"""Render'da calisan, SMM hesabi (@SosyalPazarSMM) icin bagimsiz Telegram reklam yayincisi."""

import asyncio
import collections
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

from flask import Flask, jsonify, render_template, request
from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    RPCError,
    UserBannedInChannelError,
    UserNotParticipantError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smm-reklam")

STATE_FILE = Path("smm_delivery_state.json")
LOG_FILE = Path("smm_bot_log.txt")
MIN_INTERVAL_SECONDS = 60 * 15

base_dir = os.path.abspath(os.path.dirname(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(base_dir, "templates"),
    static_folder=os.path.join(base_dir, "static"),
)

# ── Shared state ────────────────────────────────────────────────────────────
_delivery_state_cache: dict = {}
_log_buffer = collections.deque(maxlen=500)
_bot_running = True
_client_instance = None

status = {
    "state": "starting",
    "last_cycle": None,
    "last_error": None,
    "sent": 0,
    "total_groups": 0,
    "progress": 0,
    "current_group": None,
}

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_API_ID = "31076280"
DEFAULT_API_HASH = "7ba4072dcf0a05a7ccf80e570866b6d8"
DEFAULT_SESSION = (
    "1AZWarzcBu3UZfk5_JqM6uQ79PaU4GCOX90-IFu7Ne4ssOo2dikO2cgWjKM-j_V95Yh"
    "G12dkQJUcqyfUxLiEp7FoUTWUAg7zBkxyDl51EzqGmdCO36M2c-1TyuOoKi5XHV_NeSK"
    "pj-0xbl9CKOL2L9auuU7z0vGjQlvt5leKxu4fFkeLlSj3mwBE_z4eJ5hUq61qwy3gsUQ"
    "K0DEgmqnVcpxVA5VQ-PfdFay8pqt3oH4xBY4mjysblTJU5jW3BiNBTzmyiM2McQV2wvd"
    "_PjhiVUij5IBqx5SAK-4urYCS3gHWCyl-dNmfOlccpm_-UUjPGHbReGIR5BIPtGM5PTKh"
    "LzDuyk0F9vEQ="
)
DEFAULT_MESSAGE = """SOSYALPAZAR SMM
Instagram, TikTok, YouTube, Telegram ve Spotify hizmetleri

INSTAGRAM TAKİPÇİ
Genel takipçi 30 gün telafi — 1K: 27,41 TL
Hızlı takipçi 30 gün telafi — 1K: 33,88 TL
Yüksek hızlı takipçi — 1K: 39,66 TL
Türk takipçi 30 gün telafi — 1K: 241,38 TL
Türk kaliteli takipçi — 1K: 274,46 TL
Türk kadın takipçi 30 gün telafi — 1K: 223,50 TL

INSTAGRAM BEĞENİ
Genel beğeni 30 gün telafi — 1K: 3,19 TL
Türk beğeni 30 gün telafi — 1K: 95,18 TL
Türk kadın/erkek beğeni seçenekleri mevcut

TIKTOK
TikTok takipçi 30 gün telafi — 1K: 140,60 TL
Hızlı TikTok takipçi — 1K: 146,33 TL
TikTok beğeni 30 gün telafi — 1K: 13,25 TL
TikTok izlenme 30 gün telafi — 1K: 2,38 TL
TikTok paylaşım — 1K: 8,99 TL

YOUTUBE
YouTube abone 30 gün telafi — 1K: 1.021,01 TL
YouTube beğeni 30 gün telafi — 1K: 64,97 TL
Türk YouTube izlenme — 1K: 107,45 TL
YouTube izlenme ve etkileşim paketleri mevcut

TELEGRAM
Telegram kanal/grup üyesi — 1K: 29,94 TL
Telegram abone 30 gün telafi — 1K: 55,39 TL
Telegram gönderi görüntülenme — 1K: 81,08 TL

SPOTIFY
Spotify takipçi 30 gün telafi — 1K: 13,69 TL
Spotify kaydetme — 1K: 19,68 TL

Seçili servislerde hızlı başlangıç
Uygun fiyatlı paket seçenekleri
Sipariş için şifre gerekmez
Güncel servis, miktar ve sipariş bilgisi için DM:
@SosyalPazarSMM"""

DEFAULT_GROUPS = (
    "kuponindirimsatis,satcek,kuponsat,ceksat,ticaretcanavari,alsatticarettz,"
    "letgoilanlari,kuponhesapsatis,kuponsatisgrup,kuponcekkodsatis,"
    "indirimkodusatis,alimsatimmerkezii,ticaretforumofficial,kuponsatislari0,"
    "yucekuponsatis,kupongrupta,kuponkodindirimilanlar,Kuponcekm,"
    "kodceksatismerkezi,ticaretyapn,kuponkodhesapilan,kodkuponmarketi,"
    "xalimsatiim,satiskodtakasi,kuponkodalimsatimm,ceksatkupon,"
    "kuponindirimpazari,zeroticaret,indirim363,ticaretgruptr"
)


# ── Logging helper ──────────────────────────────────────────────────────────
def add_log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    _log_buffer.append(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if level == "ERROR":
        log.error(msg)
    elif level == "WARNING":
        log.warning(msg)
    else:
        log.info(msg)


# ── Helpers ─────────────────────────────────────────────────────────────────
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
        add_log(f"State kaydetme hatasi: {exc}", "WARNING")


async def join_group_safe(client, group):
    try:
        add_log(f"[SosyalPazarSMM] ➕ Grupa katilma deneniyor: @{group}")
        await client(JoinChannelRequest(group))
        add_log(f"[SosyalPazarSMM] ✅ Grupa katilindi: @{group}")
        return True
    except FloodWaitError as exc:
        add_log(f"[SosyalPazarSMM] ⚠️ Katilma bekleme suresi: {exc.seconds}s @{group}", "WARNING")
        await asyncio.sleep(exc.seconds + 2)
        return False
    except Exception as exc:
        add_log(f"[SosyalPazarSMM] ❌ Grupa katilma basarisiz @{group}: {type(exc).__name__}", "WARNING")
        return False


# ── Publisher loop ──────────────────────────────────────────────────────────
async def run_publisher():
    global _bot_running, _client_instance

    api_id = os.environ.get("TELEGRAM_API_ID", DEFAULT_API_ID).strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", DEFAULT_API_HASH).strip()
    session = os.environ.get("SMM_STRING_SESSION", DEFAULT_SESSION).strip()
    message = os.environ.get("SMM_MESSAGE", DEFAULT_MESSAGE).strip()
    groups = groups_from_env()
    interval = max(MIN_INTERVAL_SECONDS, int(os.environ.get("SMM_INTERVAL_MINUTES", "60")) * 60)

    status["total_groups"] = len(groups)
    add_log(f"[SosyalPazarSMM] Bot baslatiliyor... {len(groups)} hedef grup, {interval}sn aralik")

    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    _client_instance = client
    await client.connect()

    if not await client.is_user_authorized():
        status.update(state="configuration_error", last_error="StringSession gecersiz")
        add_log("HATA: StringSession gecersiz! Hesaba baglanilamadi.", "ERROR")
        await client.disconnect()
        return

    me = await client.get_me()
    add_log(f"[SosyalPazarSMM] Hesap baglandi: {me.first_name} (@{me.username}) ID:{me.id}")
    status.update(state="running", last_error=None)

    while True:
        if not _bot_running:
            status["state"] = "stopped"
            await asyncio.sleep(5)
            continue

        status["state"] = "running"
        delivery_state = load_state()
        _delivery_state_cache.update(delivery_state)
        now = time.time()

        for idx, group in enumerate(groups):
            if not _bot_running:
                break

            last_time = float(delivery_state.get(group, 0))
            elapsed = now - last_time
            if elapsed < interval:
                remaining = interval - elapsed
                mins = int(remaining // 60)
                secs = int(remaining % 60)
                add_log(f"[SosyalPazarSMM] ⏳ Kalan: {mins}dk {secs}sn — @{group}")
                continue

            status["current_group"] = group
            status["progress"] = int(((idx + 1) / len(groups)) * 100)

            try:
                entity = await client.get_entity(group)
                await client.send_message(entity, message, link_preview=False)
                delivery_state[group] = time.time()
                _delivery_state_cache.update(delivery_state)
                save_state(delivery_state)
                status["sent"] += 1
                add_log(f"[SosyalPazarSMM] ✅ Mesaj Gonderildi -> @{group}")
                await asyncio.sleep(15)
            except (ChatWriteForbiddenError, UserNotParticipantError, ChannelPrivateError):
                add_log(f"[SosyalPazarSMM] ⚠️ Gruba katilinmamis, katiliniyor: @{group}", "WARNING")
                joined = await join_group_safe(client, group)
                if joined:
                    await asyncio.sleep(5)
                    try:
                        entity = await client.get_entity(group)
                        await client.send_message(entity, message, link_preview=False)
                        delivery_state[group] = time.time()
                        _delivery_state_cache.update(delivery_state)
                        save_state(delivery_state)
                        status["sent"] += 1
                        add_log(f"[SosyalPazarSMM] ✅ Katilim sonrasi gonderildi -> @{group}")
                        await asyncio.sleep(15)
                    except Exception as e:
                        add_log(f"[SosyalPazarSMM] ❌ @{group} gonderilemedi: {type(e).__name__}", "WARNING")
            except FloodWaitError as exc:
                status["last_error"] = f"FloodWait {exc.seconds}s"
                add_log(f"[SosyalPazarSMM] ⚠️ Flood wait @{group}: {exc.seconds}sn", "WARNING")
                await asyncio.sleep(exc.seconds + 5)
            except RPCError as exc:
                err_name = type(exc).__name__
                status["last_error"] = f"@{group}: {err_name}"
                add_log(f"[SosyalPazarSMM] ❌ @{group} gonderilemedi: {err_name}", "WARNING")
            except Exception as exc:
                err_name = type(exc).__name__
                status["last_error"] = f"@{group}: {err_name}"
                add_log(f"[SosyalPazarSMM] ❌ Hata @{group}: {err_name}", "ERROR")

            now = time.time()

        status["last_cycle"] = datetime.now(timezone.utc).isoformat()
        status["progress"] = 100
        status["current_group"] = None
        add_log(f"[SosyalPazarSMM] Dongu tamamlandi. Toplam gonderim: {status['sent']}")
        await asyncio.sleep(60)


def background_runner():
    try:
        asyncio.run(run_publisher())
    except Exception as exc:
        status.update(state="crashed", last_error=f"{type(exc).__name__}: {exc}")
        add_log(f"SMM runner durdu: {exc}", "ERROR")


# ── Flask Routes ────────────────────────────────────────────────────────────

@app.get("/")
def dashboard():
    try:
        return render_template("index.html")
    except Exception as e:
        return jsonify({"error": str(e), "status": status})


@app.get("/health")
def health():
    return jsonify(status)


@app.get("/api/status")
def api_status():
    return jsonify(status)


@app.get("/api/logs")
def api_logs():
    try:
        if LOG_FILE.exists():
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            return jsonify({"logs": lines[-200:]})
        return jsonify({"logs": list(_log_buffer)[-200:]})
    except Exception as e:
        return jsonify({"logs": [f"Log okuma hatasi: {e}"]})


@app.get("/api/delivery_state")
def api_delivery_state():
    state = load_state()
    _delivery_state_cache.update(state)
    return jsonify(_delivery_state_cache)


@app.route("/api/bot/start", methods=["POST"])
def api_start():
    global _bot_running
    _bot_running = True
    add_log("[SosyalPazarSMM] Bot BASLATILDI (panel uzerinden)")
    return jsonify({"ok": True, "state": "running"})


@app.route("/api/bot/stop", methods=["POST"])
def api_stop():
    global _bot_running
    _bot_running = False
    status["state"] = "stopped"
    add_log("[SosyalPazarSMM] Bot DURDURULDU (panel uzerinden)")
    return jsonify({"ok": True, "state": "stopped"})


if __name__ == "__main__":
    Thread(target=background_runner, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
