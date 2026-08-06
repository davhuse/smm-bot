"""Render'da calisan, SMM hesabi (@SosyalPazarSMM) icin bagimsiz Telegram reklam yayincisi."""

import asyncio
import collections
import json
import logging
import os
import random
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

from flask import Flask, jsonify, render_template, request
from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    InviteRequestSentError,
    PeerFloodError,
    RPCError,
    SlowModeWaitError,
    UserAlreadyParticipantError,
    UserBannedInChannelError,
    UserNotParticipantError,
    UserRestrictedError,
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
MAX_PERSISTED_COOLDOWN_SECONDS = 60 * 60
BLAST_INTERVAL_SECONDS = 60 * 60
INTER_GROUP_DELAY_MIN = 20
INTER_GROUP_DELAY_MAX = 45
ACCOUNT_LAST_BLAST_KEY = "__ACCOUNT_LAST_BLAST_TIME__"
PERMANENT_BLACKLIST_KEY = "__PERMANENT_BLACKLIST__"
JOIN_RESTRICTION_KEY = "__JOIN_RESTRICTION_UNTIL__"
DISABLED_RENDER_HOST_MARKERS = ("smm-bot-1-w7pv",)
KEEPALIVE_INTERVAL_SECONDS = 300

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
    "process_started_at": datetime.now(timezone.utc).isoformat(),
    "last_keepalive": None,
}


def duplicate_render_service_disabled():
    """Keep the old duplicate Render service from opening Telegram sessions."""
    render_values = (
        os.environ.get("RENDER_EXTERNAL_URL", ""),
        os.environ.get("RENDER_EXTERNAL_HOSTNAME", ""),
        os.environ.get("RENDER_SERVICE_NAME", ""),
    )
    joined = " ".join(render_values).casefold()
    return any(marker in joined for marker in DISABLED_RENDER_HOST_MARKERS)

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_API_ID = "31076280"
DEFAULT_API_HASH = "7ba4072dcf0a05a7ccf80e570866b6d8"
DEFAULT_SESSION = ""
# No embedded StringSession: Render supplies it through the environment.
DEFAULT_MESSAGE = """🚀 SOSYALPAZAR SMM HİZMETLERİ 🚀

🔥 INSTAGRAM
• Genel Takipçi (1K): 27.41 TL
• Türk Takipçi (1K): 241.38 TL
• Beğeni Servisleri (1K): 3.19 TL'den başlayan

🔥 TIKTOK
• Takipçi (1K): 140.60 TL
• Beğeni (1K): 13.25 TL | İzlenme (1K): 2.38 TL

🔥 YOUTUBE
• Abone (1K): 1.021 TL | Beğeni (1K): 64.97 TL
• Türk İzlenme (1K): 107.45 TL

🔥 TELEGRAM & SPOTIFY
• Telegram Üye (1K): 29.94 TL | Görüntülenme: 81 TL
• Spotify Takipçi (1K): 13.69 TL

⚡ 30 Gün Telafili & Şifresiz Hizmet
👉 Sipariş ve Detaylı Bilgi İçin DM: @SosyalPazarSMM"""

DEFAULT_GROUPS = (
    "kuponindirimsatis,satcek,kuponsat,ceksat,ticaretcanavari,alsatticarettz,"
    "letgoilanlari,kuponhesapsatis,kuponsatisgrup,kuponcekkodsatis,"
    "indirimkodusatis,alimsatimmerkezii,ticaretforumofficial,kuponsatislari0,"
    "yucekuponsatis,kupongrupta,kuponkodindirimilanlar,Kuponcekm,"
    "kodceksatismerkezi,ticaretyapn,kuponkodhesapilan,kodkuponmarketi,"
    "xalimsatiim,satiskodtakasi,kuponkodalimsatimm,ceksatkupon,"
    "kuponindirimpazari,zeroticaret,indirim363,ticaretgruptr"
)

# Owner-approved production copy and the same active group list used by the
# Froxy publisher. Environment variables can override these values later.
APPROVED_MESSAGE = (
    "🚀 SOSYALPAZAR SMM HİZMETLERİ 🚀\n\n"
    "🔥 INSTAGRAM\n"
    "• Genel Takipçi (1K): 27.41 TL\n"
    "• Türk Takipçi (1K): 241.38 TL\n"
    "• Beğeni Servisleri (1K): 3.19 TL'den başlayan\n\n"
    "🔥 TIKTOK\n"
    "• Takipçi (1K): 140.60 TL\n"
    "• Beğeni (1K): 13.25 TL | İzlenme (1K): 2.38 TL\n\n"
    "🔥 YOUTUBE\n"
    "• Abone (1K): 1.021 TL | Beğeni (1K): 64.97 TL\n"
    "• Türk İzlenme (1K): 107.45 TL\n\n"
    "🔥 TELEGRAM & SPOTIFY\n"
    "• Telegram Üye (1K): 29.94 TL | Görüntülenme: 81 TL\n"
    "• Spotify Takipçi (1K): 13.69 TL\n\n"
    "⚡ 30 Gün Telafili & Şifresiz Hizmet\n"
    "👉 Sipariş ve Detaylı Bilgi İçin DM: @SosyalPazarSMM"
)
APPROVED_GROUPS = (
    "TicaretGrubuuu,kuponindirimsatis,zeroticaret,tahaaslan11,"
    "alimsatimmerkezii,sosyalmedyaalimsatimticaret,kuponsatisgrup,"
    "kuponcekkodsatis,referanslinkpaylasimigrup,kuponsatislari0,"
    "YuceKuponSatis,letgoilanlari,-3608209943,kuponhesapsatis,"
    "kuponvekodsatisgrubu,indirimkodusatis,mukyemek,kupongrupta,"
    "kuponkodindirimilanlar,Kuponcekm,satcek,kuponsat,ceksat,"
    "ticaretcanavari,alsatticarettz,ticaretforumofficial,"
    "kodceksatismerkezi,ticaretyapn,kuponkodhesapilan,kodkuponmarketi,"
    "xalimsatiim,satiskodtakasi,kuponkodalimsatimm,ceksatkupon,"
    "wishx_2,kuponindirimpazari,indirim363,ticaretgruptr"
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
    # Always include the full Froxy-approved list; an environment value may
    # add extra groups but cannot silently shrink it back to the old 18.
    raw = ",".join(filter(None, (APPROVED_GROUPS, os.environ.get("SMM_TARGET_GROUPS", ""))))
    return list(dict.fromkeys(item.strip().lstrip("@") for item in raw.split(",") if item.strip()))


import urllib.request
import urllib.error

FS_API_KEY = "AIzaSyCZz54GBF4nCgP84DsTSwwMyPq70Lb_Mjo"
FS_PROJECT_ID = "bot-2-63772"
FS_BASE_URL = f"https://firestore.googleapis.com/v1/projects/{FS_PROJECT_ID}/databases/(default)/documents"

def fs_get_state_smm():
    try:
        url = f"{FS_BASE_URL}/reklam/smm_state?key={FS_API_KEY}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                fields = data.get("fields", {})
                return fields.get("delivery_state", {}).get("stringValue", "{}")
    except Exception as e:
        add_log(f"Firestore get error: {e}", "WARNING")
    return "{}"

def fs_set_state_smm(delivery_state_json_str):
    try:
        url = f"{FS_BASE_URL}/reklam/smm_state?updateMask.fieldPaths=delivery_state&key={FS_API_KEY}"
        payload = json.dumps({"fields": {"delivery_state": {"stringValue": delivery_state_json_str}}}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, method='PATCH')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
    except urllib.error.HTTPError as e:
        # If document doesn't exist yet, patch will fail with 404. We need to create it by posting to the collection instead.
        if e.code == 404:
            try:
                url_create = f"{FS_BASE_URL}/reklam?documentId=smm_state&key={FS_API_KEY}"
                req = urllib.request.Request(url_create, data=payload, method='POST')
                req.add_header('Content-Type', 'application/json')
                with urllib.request.urlopen(req, timeout=10) as response:
                    pass
            except Exception as create_e:
                add_log(f"Firestore create error: {create_e}", "WARNING")
        else:
            add_log(f"Firestore patch error: {e}", "WARNING")
    except Exception as e:
        add_log(f"Firestore set error: {e}", "WARNING")

def load_state():
    try:
        state_str = fs_get_state_smm()
        if state_str and state_str != "{}":
            state_data = json.loads(state_str)
            try:
                STATE_FILE.write_text(json.dumps(state_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except:
                pass
            return state_data
    except Exception:
        pass

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(value):
    try:
        json_str = json.dumps(value, ensure_ascii=False, indent=2)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json_str, encoding="utf-8")
        tmp.replace(STATE_FILE)
        
        # Fire-and-forget background thread for firestore so it doesn't block main loop
        Thread(target=fs_set_state_smm, args=(json_str,), daemon=True).start()
    except Exception as exc:
        add_log(f"State kaydetme hatasi: {exc}", "WARNING")


def permanent_blacklist(state):
    value = state.get(PERMANENT_BLACKLIST_KEY, [])
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return set()
    return {str(item).strip().lstrip("@").lower() for item in value if str(item).strip()}


def set_permanent_blacklist(state, groups):
    state[PERMANENT_BLACKLIST_KEY] = sorted(groups)


def last_blast_remaining(state, now=None):
    now = now or time.time()
    try:
        last = float(state.get(ACCOUNT_LAST_BLAST_KEY, 0) or 0)
    except (TypeError, ValueError):
        last = 0
    if not last:
        return 0
    return max(0, int(BLAST_INTERVAL_SECONDS - (now - last)))


async def wait_for_blast_window(state):
    remaining = last_blast_remaining(state)
    if not remaining:
        return state
    add_log(
        f"[SosyalPazarSMM] ⏳ Son blast kaydından sonra kalan süre: "
        f"{remaining // 60}dk {remaining % 60}sn"
    )
    while remaining > 0 and _bot_running:
        status.update(
            state="waiting",
            last_error=None,
            progress=0,
            current_group=None,
        )
        await asyncio.sleep(min(15, remaining))
        remaining = last_blast_remaining(state)
    return state

# ── Watchdogs ───────────────────────────────────────────────────────────────
async def presence_watchdog(client):
    from telethon.tl.types import UserStatusOnline, UserStatusRecently
    add_log("[SosyalPazarSMM] Presence Watchdog başlatıldı...")
    while True:
        try:
            admin_user = await client.get_entity('Haacet')
            is_online = False
            if admin_user and admin_user.status:
                is_online = isinstance(admin_user.status, (UserStatusOnline, UserStatusRecently))
            
            # Use same Firestore helper
            payload = json.dumps({"fields": {"is_online": {"booleanValue": is_online}}}).encode('utf-8')
            url = f"{FS_BASE_URL}/reklam/habil_presence?updateMask.fieldPaths=is_online&key={FS_API_KEY}"
            req = urllib.request.Request(url, data=payload, method='PATCH')
            req.add_header('Content-Type', 'application/json')
            try:
                with urllib.request.urlopen(req, timeout=10) as r: pass
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    url_create = f"{FS_BASE_URL}/reklam?documentId=habil_presence&key={FS_API_KEY}"
                    req = urllib.request.Request(url_create, data=payload, method='POST')
                    req.add_header('Content-Type', 'application/json')
                    with urllib.request.urlopen(req, timeout=10) as r: pass
        except Exception as e:
            add_log(f"[Presence Watchdog] Habil durum kontrol hatası: {e}", "WARNING")
        await asyncio.sleep(60)

async def connection_watchdog(client):
    while True:
        try:
            if not client.is_connected():
                add_log("[SosyalPazarSMM] Bağlantı koptu, yeniden bağlanılıyor...", "WARNING")
                await client.connect()
        except Exception as exc:
            add_log(f"[SosyalPazarSMM] Bağlantı watchdog hatası: {type(exc).__name__}", "WARNING")
        await asyncio.sleep(30)


def _ping_health(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        response.read(64)
        return response.status


async def render_keepalive_watchdog():
    """Keep a Render web service from idling while the publisher is active."""
    base_url = (
        os.environ.get("SMM_KEEPALIVE_URL", "").strip()
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    )
    if not base_url:
        add_log("[SosyalPazarSMM] Render keep-alive URL yok; servis uykuya geçebilir.", "WARNING")
        return

    health_url = base_url.rstrip("/") + "/health"
    add_log(f"[SosyalPazarSMM] Render keep-alive watchdog aktif: {health_url}")
    while _bot_running:
        try:
            status_code = await asyncio.to_thread(_ping_health, health_url)
            status["last_keepalive"] = datetime.now(timezone.utc).isoformat()
            if status_code >= 400:
                add_log(f"[SosyalPazarSMM] Keep-alive HTTP {status_code}", "WARNING")
        except Exception as exc:
            add_log(f"[SosyalPazarSMM] Keep-alive hatası: {type(exc).__name__}", "WARNING")
        await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)



async def run_publisher():
    global _bot_running, _client_instance

    if duplicate_render_service_disabled():
        status.update(
            state="disabled_duplicate",
            last_error="Eski duplicate Render servisi devre dışı",
            total_groups=0,
            progress=0,
            current_group=None,
        )
        add_log(
            "[SosyalPazarSMM] Eski duplicate Render servisi devre dışı; "
            "Telegram bağlantısı açılmadı.",
            "WARNING",
        )
        return

    api_id = os.environ.get("TELEGRAM_API_ID", DEFAULT_API_ID).strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", DEFAULT_API_HASH).strip()
    session = os.environ.get("SMM_STRING_SESSION", "").strip()
    message = os.environ.get("SMM_MESSAGE", APPROVED_MESSAGE).strip()
    groups = groups_from_env()
    # Match the Froxy publisher: one fixed account-level blast window. The
    # per-group cooldown is also kept at this same one-hour interval.
    interval = BLAST_INTERVAL_SECONDS

    if not session or not message or not groups:
        missing = []
        if not session:
            missing.append("SMM_STRING_SESSION")
        if not message:
            missing.append("SMM_MESSAGE")
        if not groups:
            missing.append("SMM_TARGET_GROUPS")
        status.update(
            state="waiting_configuration",
            last_error=f"Eksik yapılandırma: {', '.join(missing)}",
            total_groups=0,
            progress=0,
            current_group=None,
        )
        add_log(
            "[SosyalPazarSMM] Yapılandırma bekleniyor; gönderim ve Telegram bağlantısı yok. "
            + ", ".join(missing),
            "WARNING",
        )
        return

    status["total_groups"] = len(groups)
    add_log(f"[SosyalPazarSMM] Bot baslatiliyor... {len(groups)} hedef grup, {interval}sn aralik")

    client = TelegramClient(StringSession(session), int(api_id), api_hash, flood_sleep_threshold=60)
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
    
    # Start watchdogs
    asyncio.create_task(presence_watchdog(client))
    asyncio.create_task(connection_watchdog(client))
    asyncio.create_task(render_keepalive_watchdog())

    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest

    while True:
        if not _bot_running:
            status["state"] = "stopped"
            await asyncio.sleep(5)
            continue

        status["state"] = "running"
        delivery_state = load_state()
        _delivery_state_cache.update(delivery_state)
        now = time.time()

        # A previous duplicate service wrote timestamps several hours into the
        # future. Clamp those stale records to one normal cooldown window so a
        # group cannot remain blocked for 400+ minutes.
        normalized = False
        for saved_group, saved_time in list(delivery_state.items()):
            if saved_group in {PERMANENT_BLACKLIST_KEY, JOIN_RESTRICTION_KEY}:
                continue
            try:
                if float(saved_time) > now + MAX_PERSISTED_COOLDOWN_SECONDS:
                    delivery_state[saved_group] = now
                    normalized = True
            except (TypeError, ValueError):
                delivery_state.pop(saved_group, None)
                normalized = True
        if normalized:
            _delivery_state_cache.clear()
            _delivery_state_cache.update(delivery_state)
            save_state(delivery_state)
            add_log("[SosyalPazarSMM] Eski cooldown kayıtları 1 saatlik pencereye normalize edildi.")

        delivery_blacklist = permanent_blacklist(delivery_state)
        try:
            join_restricted_until = float(delivery_state.get(JOIN_RESTRICTION_KEY, 0) or 0)
        except (TypeError, ValueError):
            join_restricted_until = 0
        join_allowed = time.time() >= join_restricted_until

        # Join missing targets before the blast cooldown.  This keeps the
        # membership phase from being hidden behind a 60-minute account wait
        # after a restart, while the five-group cap matches Froxy's
        # anti-spam behaviour.  Groups with a pending admin approval are
        # reported and will not be retried in this pass.
        preflight_joined = set()
        try:
            async for dialog in client.iter_dialogs():
                if dialog.is_channel or dialog.is_group:
                    username = getattr(dialog.entity, "username", None)
                    if username:
                        preflight_joined.add(username.lower())
                    preflight_joined.add(str(dialog.id))
        except Exception as exc:
            add_log(f"[SosyalPazarSMM] Katılım öncesi diyalog okuma hatası: {type(exc).__name__}", "WARNING")

        preflight_missing = [
            group for group in groups
            if group.strip().lstrip("@").lower() not in delivery_blacklist
            and group.strip().lstrip("@").lower() not in preflight_joined
        ]
        if preflight_missing and _bot_running and not join_allowed:
            remaining = max(0, int(join_restricted_until - time.time()))
            add_log(
                f"[SosyalPazarSMM] Join flood koruması aktif; katılım {remaining}sn sonra tekrar denenecek."
            )
        if preflight_missing and _bot_running and join_allowed:
            add_log(
                f"[SosyalPazarSMM] 🔍 {len(preflight_missing)} gruba henüz üye değiliz. "
                "Katılma ön aşaması başlıyor..."
            )
            successful_preflight_joins = 0
            for group in preflight_missing:
                if successful_preflight_joins >= 5:
                    break
                if not _bot_running:
                    break
                try:
                    add_log(f"[SosyalPazarSMM] ➕ Katılma deneniyor: @{group}")
                    raw_group = group.strip()
                    is_invite_hash = (
                        raw_group.startswith("+")
                        or raw_group.startswith("joinchat/")
                        or "/+" in raw_group
                    )
                    if is_invite_hash:
                        await client(ImportChatInviteRequest(group))
                    else:
                        await client(JoinChannelRequest(group))
                    add_log(f"[SosyalPazarSMM] ✅ Katılım başarılı: @{group}")
                    successful_preflight_joins += 1
                    await asyncio.sleep(random.randint(45, 75))
                except InviteRequestSentError:
                    add_log(f"[SosyalPazarSMM] ⏳ @{group} -> Katılım isteği gönderildi (onay bekleniyor).")
                except UserAlreadyParticipantError:
                    add_log(f"[SosyalPazarSMM] ℹ️ Zaten grupta var: @{group}")
                except FloodWaitError as exc:
                    delivery_state[JOIN_RESTRICTION_KEY] = time.time() + exc.seconds + 30
                    _delivery_state_cache.update(delivery_state)
                    save_state(delivery_state)
                    join_allowed = False
                    add_log(f"[SosyalPazarSMM] ⚠️ Join flood {exc.seconds}sn; katılım geçici durduruldu.")
                    break
                except Exception as exc:
                    err_type = type(exc).__name__
                    if err_type in {"ValueError", "UsernameInvalidError", "UsernameNotOccupiedError", "ChannelPrivateError"}:
                        delivery_blacklist.add(group.strip().lstrip("@").lower())
                        set_permanent_blacklist(delivery_state, delivery_blacklist)
                        _delivery_state_cache.update(delivery_state)
                        save_state(delivery_state)
                        add_log(f"[SosyalPazarSMM] ⛔ @{group} erişilemez/geçersiz; kalıcı olarak atlandı.", "WARNING")
                    else:
                        add_log(f"[SosyalPazarSMM] ❌ @{group} katılım hatası: {err_type}", "WARNING")

        await wait_for_blast_window(delivery_state)
        if not _bot_running:
            continue
        
        # 0. GET JOINED GROUPS
        joined_usernames = set()
        try:
            async for dialog in client.iter_dialogs():
                if dialog.is_channel or dialog.is_group:
                    if getattr(dialog.entity, 'username', None):
                        joined_usernames.add(dialog.entity.username.lower())
                    joined_usernames.add(str(dialog.id))
        except Exception as e:
            add_log(f"[SosyalPazarSMM] Dialogs okuma hatasi: {e}", "WARNING")
        
        # 1. SEND PHASE
        not_joined_groups = []

        for idx, group in enumerate(groups):
            if not _bot_running:
                break

            group_key = group.strip().lstrip("@").lower()
            if group_key in delivery_blacklist:
                add_log(f"[SosyalPazarSMM] ⛔ @{group} kalıcı kara listede, atlanıyor.")
                continue

            # Match Froxy's order: membership is checked before cooldown. A
            # stale delivery timestamp must never postpone joining a group.
            in_group = group.lower() in joined_usernames
            if not in_group:
                add_log(f"[SosyalPazarSMM] ⚠️ @{group} henüz üye değiliz, katılım listesine eklendi.")
                not_joined_groups.append(group)
                continue

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

            # Check if we are actually in the group before trying to send.
            in_group = group.lower() in joined_usernames
            if not in_group:
                # Fallback: Sometimes iter_dialogs misses newly joined groups.
                # Try getting entity, if it throws UserNotParticipant, then we are really not in it.
                try:
                    ent = await client.get_entity(group)
                    # If we got here and didn't crash, we might be able to send. Let's try.
                    in_group = True
                except:
                    pass
            
            if not in_group:
                add_log(f"[SosyalPazarSMM] ⚠️ @{group} henüz üye değiliz, katılım listesine eklendi.")
                not_joined_groups.append(group)
                continue

            try:
                entity = await client.get_entity(group)
                await client.send_message(entity, message, link_preview=False)
                accepted_at = time.time()
                delivery_state[group] = accepted_at
                delivery_state[ACCOUNT_LAST_BLAST_KEY] = accepted_at
                _delivery_state_cache.update(delivery_state)
                save_state(delivery_state)
                status["sent"] += 1
                add_log(f"[SosyalPazarSMM] ✅ Mesaj Gonderildi -> @{group}")
                
                group_delay = random.randint(INTER_GROUP_DELAY_MIN, INTER_GROUP_DELAY_MAX)
                add_log(f"[SosyalPazarSMM] 🛡️ Gruplar arasi bekleme: {group_delay}sn")
                await asyncio.sleep(group_delay)

            except FloodWaitError as exc:
                wait_sec = exc.seconds
                status["last_error"] = f"FloodWait {wait_sec}s"
                add_log(f"[SosyalPazarSMM] ⏳ FloodWait {wait_sec}sn; hesap duraklatıldı.")
                await asyncio.sleep(wait_sec + 2)

            except (PeerFloodError, UserRestrictedError) as e:
                wait_sec = getattr(e, "seconds", 48 * 3600) or 48 * 3600
                add_log(f"[SosyalPazarSMM] 🚫 Hesap kısıtlaması algılandı ({type(e).__name__}); {wait_sec}sn duraklatıldı.")
                await asyncio.sleep(wait_sec + 2)

            except UserBannedInChannelError:
                add_log(f"[SosyalPazarSMM] ❌ @{group} -> Banlandık! (UserBannedInChannel)")
                delivery_blacklist.add(group_key)
                set_permanent_blacklist(delivery_state, delivery_blacklist)
                _delivery_state_cache.update(delivery_state)
                save_state(delivery_state)

            except ChatWriteForbiddenError:
                add_log(f"[SosyalPazarSMM] 🔒 @{group} -> Yazma izni yok! (ChatWriteForbidden)")
                delivery_blacklist.add(group_key)
                set_permanent_blacklist(delivery_state, delivery_blacklist)
                _delivery_state_cache.update(delivery_state)
                save_state(delivery_state)

            except SlowModeWaitError as sme:
                wait_sec = getattr(sme, "seconds", 60) or 60
                delivery_state[group] = time.time() + wait_sec + 30
                _delivery_state_cache.update(delivery_state)
                save_state(delivery_state)
                add_log(f"[SosyalPazarSMM] 🐌 @{group} -> SlowMode aktif ({wait_sec}sn bekleme).")
                await asyncio.sleep(wait_sec + 2)

            except (UserNotParticipantError, ChannelPrivateError):
                add_log(f"[SosyalPazarSMM] ⚠️ @{group} henüz üye değiliz, katılım listesine eklendi.")
                not_joined_groups.append(group)

            except RPCError as exc:
                err_name = type(exc).__name__
                status["last_error"] = f"@{group}: {err_name}"
                add_log(f"[SosyalPazarSMM] ❌ @{group} gonderilemedi: {err_name}", "WARNING")

            except Exception as exc:
                err_name = type(exc).__name__
                status["last_error"] = f"@{group}: {err_name}"
                add_log(f"[SosyalPazarSMM] ❌ Hata @{group}: {err_name}", "ERROR")

            now = time.time()

        # 2. JOIN PHASE
        if not_joined_groups and _bot_running and join_allowed:
            add_log(f"\n[SosyalPazarSMM] 🔍 {len(not_joined_groups)} gruba henüz üye değiliz. Katılma başlıyor...")
            # Froxy ile aynı güvenli katılım limiti: tur başına en fazla 5
            # başarılı katılım. Geçersiz veya onay bekleyen bir grup bu
            # kotayı tüketmemeli.
            successful_joins = 0
            for group in not_joined_groups:
                if successful_joins >= 5:
                    break
                if not _bot_running:
                    break
                try:
                    add_log(f"[SosyalPazarSMM] ➕ Katılma deneniyor: @{group}")
                    raw_group = group.strip()
                    is_invite_hash = (
                        raw_group.startswith("+")
                        or raw_group.startswith("joinchat/")
                        or "/+" in raw_group
                    )
                    if is_invite_hash:
                        await client(ImportChatInviteRequest(group))
                        add_log(f"[SosyalPazarSMM] ✅ Özel gruba katıldı: @{group}")
                    else:
                        await client(JoinChannelRequest(group))
                        add_log(f"[SosyalPazarSMM] ✅ Gruba katıldı: @{group}")
                    successful_joins += 1
                    
                    wait_after_join = random.randint(45, 75)
                    add_log(f"[SosyalPazarSMM] 🛡️ Katılım sonrası anti-spam beklemesi: {wait_after_join}sn")
                    await asyncio.sleep(wait_after_join)
                    
                except InviteRequestSentError:
                    add_log(f"[SosyalPazarSMM] ⏳ @{group} -> Katılım isteği gönderildi (onay bekleniyor).")
                except UserAlreadyParticipantError:
                    add_log(f"[SosyalPazarSMM] ℹ️ Zaten grupta var: @{group}")
                except FloodWaitError as exc:
                    delivery_state[JOIN_RESTRICTION_KEY] = time.time() + exc.seconds + 30
                    _delivery_state_cache.update(delivery_state)
                    save_state(delivery_state)
                    join_allowed = False
                    add_log(f"[SosyalPazarSMM] ⚠️ Join flood {exc.seconds}sn; katılım geçici durduruldu.")
                    break
                except Exception as exc:
                    err_msg = str(exc)
                    err_type = type(exc).__name__
                    if 'banned' in err_msg.lower() or 'UserBannedInChannel' in err_type:
                        add_log(f"[SosyalPazarSMM] ⛔ @{group} -> Bu hesap bu gruptan BANLANMIŞ. 24 saat denenmeyecek.")
                        delivery_state[group] = time.time() + 86400
                        _delivery_state_cache.update(delivery_state)
                        save_state(delivery_state)
                    else:
                        add_log(f"[SosyalPazarSMM] ❌ @{group} katılım hatası: {err_type}", "WARNING")

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
