#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# VIEDIET FIREBASE DETECTOR BOT
# ============================================================================
# Simplified bot (no referrals / no points / no Ujala API):
#   * Force channel join (@viedietlooters) before any interaction
#   * Bulk add Firebase Realtime Database URLs (one per line)
#   * Each URL is scanned for ONLINE devices using the exact discovery logic
#     from refer.py (All_Users/simDetails + All_Users/Data/DeviceInfo)
#   * Results are shown in a single EDITED message: URL, ✅ Active /
#     ❌ Inactive / ⚠️ Error, online device count, phone number count
#   * SQLite storage (users + user_firebases), global-unique URLs
#   * Admin panel via /admin: stats, users, per-user details, delete any URL
#   * All heavy scanning runs through a bounded worker queue -> the bot can
#     never be crashed by many users adding URLs at the same time
#
# NOTE ON "COLORED" BUTTONS:
# The Telegram Bot API does NOT support a `style` parameter on inline
# keyboard buttons ("can't parse InlineKeyboardButton: invalid button style
# specified"). We therefore send plain buttons - guaranteed to work on every
# Bot API server. The btn() helper keeps the same clean call sites so the UI
# stays uniform.
#
# SETUP
# -----
#     export BOT_TOKEN="YOUR_BOT_TOKEN"
#     export ADMIN_ID="YOUR_TELEGRAM_ID"
#     export CHANNEL_USERNAME="viedietlooters"
#     export DATA_DIR="./data"
#     export JOB_WORKERS="3"      # optional: max parallel scans
#     python3 firebase_detector_bot.py
#
# REQUIREMENTS
# ------------
#     pip install pyTelegramBotAPI>=4.24.0 requests
# ============================================================================

# ════════════════════════════════════════════════════════════════════════════
# 1. IMPORTS
# ════════════════════════════════════════════════════════════════════════════
import os
import re
import sys
import json
import time
import queue
import logging
import sqlite3
import threading
from datetime import datetime
from html import escape

import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ════════════════════════════════════════════════════════════════════════════
# 2. CONFIGURATION (environment variables)
# ════════════════════════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8139558808").strip() or "1")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "viedietlooters").strip().lstrip("@")
DATA_DIR = os.environ.get("DATA_DIR", "./data").strip()
JOB_WORKERS = int(os.environ.get("JOB_WORKERS", "3"))   # max parallel scans

GROUP_LINK = "https://t.me/viedietlooterschat"          # support group
PAGE_SIZE = 10                                          # admin pagination
SCAN_TIMEOUT = 8                                        # sec per Firebase GET

FOOTER = "\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 Made by viediet"

if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN environment variable is not set.")
    print("       export BOT_TOKEN=\"YOUR_BOT_TOKEN\"")
    sys.exit(1)
if ADMIN_ID <= 0:
    print("ERROR: ADMIN_ID environment variable is not set.")
    print("       export ADMIN_ID=\"YOUR_TELEGRAM_ID\"")
    sys.exit(1)

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "firebase_detector.db")

# ════════════════════════════════════════════════════════════════════════════
# 3. LOGGING (console + file)
# ════════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(DATA_DIR, "bot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("firebase_detector_bot")

# ════════════════════════════════════════════════════════════════════════════
# 4. BOT INITIALIZATION
# ════════════════════════════════════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ════════════════════════════════════════════════════════════════════════════
# 5. DATABASE (SQLite)
# ════════════════════════════════════════════════════════════════════════════
_db_lock = threading.RLock()


def get_conn():
    """Thread-safe connection."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock:
        conn = get_conn()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS users (
                    user_id        INTEGER PRIMARY KEY,
                    username       TEXT,
                    first_name     TEXT,
                    registered_at  TEXT,
                    channel_joined INTEGER DEFAULT 0
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS user_firebases (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER,
                    firebase_url  TEXT UNIQUE,
                    added_at      TEXT,
                    status        TEXT DEFAULT 'pending',
                    device_count  INTEGER DEFAULT 0,
                    number_count  INTEGER DEFAULT 0,
                    last_checked  TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(user_id)
                )"""
            )
            conn.commit()
        finally:
            conn.close()
    logger.info("Database initialized: %s", DB_PATH)


def get_user(user_id):
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?",
                               (user_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def create_user(user_id, username, first_name):
    """INSERT OR IGNORE; returns True if newly created."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO users
                   (user_id, username, first_name, registered_at, channel_joined)
                   VALUES (?, ?, ?, ?, 0)""",
                (user_id, username, first_name, now),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def update_user(user_id, **fields):
    if not fields:
        return
    with _db_lock:
        conn = get_conn()
        try:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?",
                         (*fields.values(), user_id))
            conn.commit()
        finally:
            conn.close()


def add_firebase(user_id, firebase_url):
    """Insert one URL (global UNIQUE). Returns the new id or None on duplicate."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO user_firebases
                   (user_id, firebase_url, added_at, status, device_count,
                    number_count, last_checked)
                   VALUES (?, ?, ?, 'pending', 0, 0, NULL)""",
                (user_id, firebase_url, now),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
            return cur.lastrowid
        finally:
            conn.close()


def get_firebase_by_id(fb_id):
    with _db_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM user_firebases WHERE id = ?",
                               (fb_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_user_firebases(user_id):
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM user_firebases WHERE user_id = ? ORDER BY id DESC",
                (user_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_all_firebases():
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM user_firebases ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def get_all_users():
    with _db_lock:
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT u.*, (SELECT COUNT(*) FROM user_firebases f
                               WHERE f.user_id = u.user_id) AS firebase_count
                   FROM users u ORDER BY u.user_id""").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def update_firebase(fb_id, **fields):
    if not fields:
        return
    with _db_lock:
        conn = get_conn()
        try:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE user_firebases SET {cols} WHERE id = ?",
                         (*fields.values(), fb_id))
            conn.commit()
        finally:
            conn.close()


def delete_firebase(fb_id):
    with _db_lock:
        conn = get_conn()
        try:
            cur = conn.execute("DELETE FROM user_firebases WHERE id = ?", (fb_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_stats():
    with _db_lock:
        conn = get_conn()
        try:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM user_firebases").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM user_firebases WHERE status = 'active'").fetchone()[0]
            inactive = conn.execute(
                "SELECT COUNT(*) FROM user_firebases WHERE status = 'inactive'").fetchone()[0]
            error = conn.execute(
                "SELECT COUNT(*) FROM user_firebases WHERE status = 'error'").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM user_firebases WHERE status = 'pending'").fetchone()[0]
            return {"users": users, "total": total, "active": active,
                    "inactive": inactive, "error": error, "pending": pending}
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════════════
# 6. FIREBASE PANEL DISCOVERY  (exact logic from refer.py, synchronous port)
# ════════════════════════════════════════════════════════════════════════════

def extract_all_nums(*dicts):
    """
    Extract phone numbers from simDetails / DeviceInfo dicts.
    Handles several known keys and normalizes to the last 10 digits.
    (copied from refer.py)
    """
    nums = []
    keys_to_check = ["sim1Number", "sim2Number", "numberSim1", "numberSim2",
                     "mobNo", "phoneNumber", "phone", "mobile"]
    for d in dicts:
        if not isinstance(d, dict):
            continue
        for k in keys_to_check:
            val = str(d.get(k, ""))
            if val and len(re.sub(r"\D", "", val)) >= 10:
                clean = re.sub(r"\D", "", val)
                nums.append(clean[-10:])
    return list(set(nums))


def fb_get_sync(base_url, path, timeout=SCAN_TIMEOUT):
    """
    Synchronous Firebase GET (mirrors refer.py aio_fb_get).
    Returns a dict or None. Raises on network errors/timeouts so the caller
    can mark the URL as 'error' (refer.py swallows these; we surface them).
    """
    r = requests.get(f"{base_url}/{path}.json", timeout=timeout)
    if r.status_code != 200:
        return None
    data = r.json()
    return data if isinstance(data, dict) else None


def _build_panel_report(url, sim_all, device_info_all):
    """
    Shared logic (from refer.py check_panel_active): build the report of
    online devices with their numbers, or None if the panel is unusable.
    """
    if not isinstance(sim_all, dict) or not sim_all:
        return None
    info_all = device_info_all if isinstance(device_info_all, dict) else {}
    online_devices = []
    for dev_id, sim in sim_all.items():
        info = info_all.get(dev_id) or {}
        status = str(info.get("Status", "")).lower()
        if status == "online":
            nums = extract_all_nums(sim, info)
            if nums:
                online_devices.append({"id": dev_id, "numbers": nums,
                                       "status": "online"})
    if not online_devices:
        return None
    total_nums = sum(len(d["numbers"]) for d in online_devices)
    return {
        "url": url,
        "online_devices": online_devices,
        "total_devices": len(online_devices),
        "total_numbers": total_nums,
    }


def check_panel_active(url):
    """
    Discover online devices for ONE Firebase panel URL.
    Synchronous port of refer.py check_panel_active():
        fetch All_Users/simDetails + All_Users/Data/DeviceInfo, count devices
        whose Status == 'online', extract their phone numbers.
    Returns a report dict, None (reachable but nothing online), or raises
    on network failure (timeout / unreachable).
    """
    sim_all = fb_get_sync(url, "All_Users/simDetails")
    device_info_all = fb_get_sync(url, "All_Users/Data/DeviceInfo")
    return _build_panel_report(url, sim_all, device_info_all)


def scan_panel(url):
    """
    Scan one URL -> (status, device_count, number_count).
    status is one of: 'active' | 'inactive' | 'error'.
    """
    try:
        report = check_panel_active(url)
        if report:
            return "active", report["total_devices"], report["total_numbers"]
        return "inactive", 0, 0
    except Exception as e:
        logger.warning("scan error for %s: %s", url, e)
        return "error", 0, 0


def is_valid_firebase_url(raw_url):
    """
    Validate a Firebase Realtime Database URL.
    Returns (ok, normalized_url_or_error_message).
    """
    url = (raw_url or "").strip().rstrip("/")
    if not url.startswith("https://"):
        return False, "URL must start with <b>https://</b>"
    if "firebaseio.com" not in url and "firebasedatabase.app" not in url:
        return (False,
                "Not a Firebase URL. It must contain <b>firebaseio.com</b> "
                "or <b>firebasedatabase.app</b>")
    return True, url


# ════════════════════════════════════════════════════════════════════════════
# 7. BOUNDED BACKGROUND JOB QUEUE (scans never crash the bot)
# ════════════════════════════════════════════════════════════════════════════
_job_queue = queue.Queue()
_scan_states = {}          # user_id -> True while their scan is running
_scan_states_lock = threading.RLock()


def _enqueue_job(kind, args):
    _job_queue.put((kind, args))


def _job_worker():
    while True:
        kind, args = _job_queue.get()
        try:
            if kind == "scan":
                scan_firebases_and_present(*args)
        except Exception:
            logger.exception("Queue job failed (kind=%s)", kind)
        finally:
            _job_queue.task_done()


def start_job_workers():
    for i in range(max(1, JOB_WORKERS)):
        threading.Thread(target=_job_worker, name=f"scan-worker-{i}",
                         daemon=True).start()
    logger.info("Started %d scan workers", max(1, JOB_WORKERS))


# ════════════════════════════════════════════════════════════════════════════
# 8. UI HELPERS (buttons, menus)
# ════════════════════════════════════════════════════════════════════════════

def btn(text, callback_data=None, url=None):
    """
    Inline keyboard button factory.
    NOTE: the Telegram Bot API does not support the colored-button `style`
    parameter, so we send plain buttons only (works everywhere).
    Every button MUST have callback_data OR url - Telegram rejects
    text-only inline buttons ("Text buttons are unallowed...").
    """
    if not callback_data and not url:
        raise ValueError(f"btn() without callback_data/url: {text!r}")
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    return InlineKeyboardButton(**kwargs)


def back_markup(callback_data):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(btn("🔙 BACK", callback_data=callback_data))
    return kb


def _short(url, limit=40):
    return url if len(url) <= limit else url[:limit] + "…"


def _status_badge(status):
    return {
        "active": "✅ Active",
        "inactive": "❌ Inactive",
        "error": "⚠️ Error",
        "pending": "⏳ Pending",
    }.get(status, "⏳ Pending")


def safe_send(chat_id, text, markup=None):
    try:
        return bot.send_message(chat_id, text, reply_markup=markup,
                                parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error("send failed for %s: %s", chat_id, e)
        return None


def safe_edit(chat_id, message_id, text, markup=None):
    """Edit a message; fall back to sending a new one on failure."""
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                              reply_markup=markup, parse_mode="HTML",
                              disable_web_page_preview=True)
        return message_id
    except Exception as e:
        err = str(e)
        if "message is not modified" in err:
            return message_id
        m = safe_send(chat_id, text, markup)
        return m.message_id if m else message_id


# ════════════════════════════════════════════════════════════════════════════
# 9. FORCE CHANNEL JOIN
# ════════════════════════════════════════════════════════════════════════════

def check_channel_membership(user_id):
    """Return True if the user is a member of the required channel."""
    try:
        member = bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        joined = member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.error("Channel check failed for %s: %s", user_id, e)
        joined = False
    if joined:
        update_user(user_id, channel_joined=1)
    return joined


def channel_join_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(btn(f"📢 JOIN @{CHANNEL_USERNAME}",
               url=f"https://t.me/{CHANNEL_USERNAME}"))
    kb.row(btn("✅ CHECK AGAIN", callback_data="check_channel"))
    return kb


def channel_join_text():
    return (
        f"🔒 <b>CHANNEL REQUIRED</b>\n\n"
        f"⚠️ To use this bot you must join our channel:\n\n"
        f"📢 <b>@{CHANNEL_USERNAME}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Join the channel, then press <b>CHECK AGAIN</b>.\n"
        f"{FOOTER}"
    )


def send_join_required(chat_id):
    bot.send_message(chat_id, channel_join_text(),
                     reply_markup=channel_join_keyboard(), parse_mode="HTML")


# ════════════════════════════════════════════════════════════════════════════
# 10. MAIN MENU
# ════════════════════════════════════════════════════════════════════════════

def main_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("📁 ADD FIREBASE", callback_data="add_firebase"))
    kb.row(btn("🆘 HELP", callback_data="help"), btn("💬 SUPPORT", url=GROUP_LINK))
    return kb


def main_menu_text(user):
    name = escape(user.get("first_name") or "User")
    joined = "✅" if user.get("channel_joined") else "❌"
    return (
        f"🔥 <b>VIEDIET FIREBASE DETECTOR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, <b>{name}</b>!\n"
        f"📢 Channel: {joined} @{CHANNEL_USERNAME}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 Add your Firebase URLs and the bot will scan them for\n"
        f"online devices & phone numbers.\n"
        f"💬 Need help? Tap 🆘 HELP or join our support group.\n"
        f"{FOOTER}"
    )


def show_main_menu(chat_id, message_id=None, edit=True):
    user = get_user(chat_id)
    if not user:
        return
    text = main_menu_text(user)
    markup = main_menu_keyboard()
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        safe_send(chat_id, text, markup)


# ════════════════════════════════════════════════════════════════════════════
# 11. HELP & SUPPORT
# ════════════════════════════════════════════════════════════════════════════

def send_help(chat_id, message_id=None, edit=True):
    text = (
        f"🆘 <b>HELP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>HOW TO ADD FIREBASE URLS</b>\n"
        f"1️⃣ Tap <b>ADD FIREBASE</b>\n"
        f"2️⃣ Paste one URL per line (bulk allowed), e.g.\n"
        f"   <code>https://panel-name-default-rtdb.firebaseio.com</code>\n\n"
        f"🔎 <b>WHERE TO FIND FIREBASE URLS</b>\n"
        f"• Firebase URLs are embedded inside Android apps (APKs).\n"
        f"• Extract the APK (e.g. with MT Manager / ApkTool), open its\n"
        f"  files and search for <code>firebaseio.com</code> or\n"
        f"  <code>firebasedatabase.app</code>.\n"
        f"• Paste the full <code>https://...</code> URLs here.\n\n"
        f"🔍 <b>WHAT THE BOT DOES</b>\n"
        f"• Validates every URL.\n"
        f"• Scans each panel for devices with status <b>ONLINE</b>.\n"
        f"• Shows ✅ Active / ❌ Inactive / ⚠️ Error and how many\n"
        f"  devices & phone numbers were found.\n"
        f"• Only <b>active</b> panels (online devices) show counts.\n\n"
        f"💬 Questions? Tap 💬 SUPPORT.\n"
        f"{FOOTER}"
    )
    markup = back_markup("main_menu")
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        safe_send(chat_id, text, markup)


# ════════════════════════════════════════════════════════════════════════════
# 12. ADD FIREBASE FLOW (bulk add + async scan, single edited message)
# ════════════════════════════════════════════════════════════════════════════
_firebase_states = {}        # user_id -> {"step": "awaiting_url"}
_state_lock = threading.RLock()


def start_add_firebase(chat_id, message_id=None):
    with _state_lock:
        if _scan_states.get(chat_id):
            safe_send(chat_id,
                      f"⏳ <b>Scan in progress!</b>\n"
                      f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                      f"A scan is already running for your account.\n"
                      f"Wait for it to finish, then add more URLs.\n"
                      f"{FOOTER}")
            return
    with _state_lock:
        _firebase_states[chat_id] = {"step": "awaiting_url"}
    text = (
        f"📁 <b>ADD FIREBASE (BULK)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Send <b>one or more Firebase URLs</b>, one per line:\n\n"
        f"<code>https://panel1-default-rtdb.firebaseio.com</code>\n"
        f"<code>https://panel2-default-rtdb.firebaseio.com</code>\n\n"
        f"✅ Each URL will be validated & scanned automatically.\n"
        f"🔁 Duplicate URLs are skipped.\n"
        f"📁 You can add <b>unlimited</b> URLs.\n"
        f"{FOOTER}"
    )
    if message_id:
        safe_edit(chat_id, message_id, text, None)
    else:
        safe_send(chat_id, text)


def handle_add_firebase_text(chat_id, text):
    raw_urls = [line.strip() for line in (text or "").splitlines() if line.strip()]
    valid, invalid = [], []
    for raw in raw_urls:
        ok, res = is_valid_firebase_url(raw)
        if ok:
            valid.append(res)
        else:
            invalid.append((raw, res))

    if not valid:
        safe_send(
            chat_id,
            f"❌ <b>No valid Firebase URLs found.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + ("\n".join(f"• <code>{escape(a[:50])}</code>: {b}" for a, b in invalid[:5])
               if invalid else
               "Send URLs starting with <b>https://</b> containing "
               "<b>firebaseio.com</b> or <b>firebasedatabase.app</b>") +
            f"\n\n👉 Try again, or send /cancel to abort.\n"
            f"{FOOTER}",
        )
        return

    # Insert (global UNIQUE -> duplicates come back as None)
    added, dupes = [], []
    for url in valid:
        fb_id = add_firebase(chat_id, url)
        if fb_id:
            added.append({"id": fb_id, "url": url})
        else:
            dupes.append(url)

    with _state_lock:
        _firebase_states.pop(chat_id, None)

    if not added:
        safe_send(
            chat_id,
            f"❌ <b>Nothing was added.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔁 All URLs are duplicates (already added before).\n"
            f"{FOOTER}",
        )
        return

    with _scan_states_lock:
        _scan_states[chat_id] = True

    # One scan message; progress is shown by editing THIS message only
    msg = safe_send(
        chat_id,
        f"🔍 <b>SCANNING FIREBASE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 Added <b>{len(added)}</b> URL(s)"
        + (f" · 🔁 {len(dupes)} duplicate(s) skipped" if dupes else "") +
        f"\n⏳ Starting scan...\n"
        f"{FOOTER}",
    )
    mid = msg.message_id if msg else 0

    # Scan in the bounded queue -> never blocks the bot
    _enqueue_job("scan", (chat_id, mid, [a["id"] for a in added]))


def scan_firebases_and_present(chat_id, message_id, fb_ids):
    """Scan the newly added URLs and edit ONE message with the results."""
    results = []
    total = len(fb_ids)
    try:
        for i, fb_id in enumerate(fb_ids, 1):
            fb = get_firebase_by_id(fb_id)
            if not fb:
                continue
            status, devs, nums = scan_panel(fb["firebase_url"])
            update_firebase(
                fb_id,
                status=status,
                device_count=devs,
                number_count=nums,
                last_checked=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            results.append((fb["firebase_url"], status, devs, nums))

            body = "\n".join(
                f"{i2}. {_status_badge(st2)} <code>{escape(_short(u2))}</code>"
                + (f"\n   🖥️ {d2} devices | 📱 {n2} numbers" if st2 == "active"
                   else f"\n   {'❌ No online devices' if st2 == 'inactive' else '⚠️ Unreachable / timeout'}")
                for i2, (u2, st2, d2, n2) in enumerate(results, 1)
            )
            text = (
                f"🔍 <b>SCANNING FIREBASE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Progress: <b>{i}/{total}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{body}\n"
                f"{FOOTER}"
            )
            message_id = safe_edit(chat_id, message_id, text, None)
            time.sleep(0.3)

        active = sum(1 for _, s, _, _ in results if s == "active")
        inactive = sum(1 for _, s, _, _ in results if s == "inactive")
        err = sum(1 for _, s, _, _ in results if s == "error")

        lines = []
        for i, (u, s, d, n) in enumerate(results, 1):
            if s == "active":
                extra = f"   🖥️ <b>{d}</b> devices | 📱 <b>{n}</b> numbers"
            elif s == "inactive":
                extra = "   ❌ No online devices found"
            else:
                extra = "   ⚠️ Error: unreachable / timeout"
            lines.append(f"{i}. {_status_badge(s)} <code>{escape(_short(u))}</code>\n{extra}")

        text = (
            f"📁 <b>SCAN COMPLETE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Active: <b>{active}</b> | ❌ Inactive: <b>{inactive}</b> | "
            f"⚠️ Error: <b>{err}</b>\n"
            f"💬 Add more: press 📁 ADD FIREBASE\n"
            f"{FOOTER}"
        )
        safe_edit(chat_id, message_id, text,
                  InlineKeyboardMarkup(row_width=2).row(
                      btn("📁 ADD MORE", callback_data="add_firebase"),
                      btn("🏠 MENU", callback_data="main_menu")))
    finally:
        with _scan_states_lock:
            _scan_states.pop(chat_id, None)


# ════════════════════════════════════════════════════════════════════════════
# 13. ADMIN PANEL
# ════════════════════════════════════════════════════════════════════════════

def is_admin(user):
    return user is not None and user.get("user_id") == ADMIN_ID


def admin_menu_text():
    return (
        f"👑 <b>ADMIN PANEL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Manage users, view stats and Firebase entries.\n"
        f"{FOOTER}"
    )


def admin_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("📊 STATS", callback_data="admin_stats"),
           btn("👥 USERS", callback_data="admin_users"))
    kb.row(btn("🔥 MANAGE FIREBASE", callback_data="admin_firebases"))
    kb.row(btn("🔙 USER MENU", callback_data="main_menu"))
    return kb


def send_admin_menu(chat_id, message_id=None, edit=True):
    if edit and message_id:
        safe_edit(chat_id, message_id, admin_menu_text(), admin_menu_keyboard())
    else:
        safe_send(chat_id, admin_menu_text(), admin_menu_keyboard())


def send_admin_stats(chat_id, message_id=None, edit=True):
    s = get_stats()
    text = (
        f"📊 <b>STATS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total users: <b>{s['users']}</b>\n"
        f"📁 Total Firebase entries: <b>{s['total']}</b>\n"
        f"✅ Active: <b>{s['active']}</b>\n"
        f"❌ Inactive: <b>{s['inactive']}</b>\n"
        f"⚠️ Error: <b>{s['error']}</b>\n"
        f"⏳ Pending: <b>{s['pending']}</b>\n"
        f"{FOOTER}"
    )
    markup = back_markup("admin_panel")
    if edit and message_id:
        safe_edit(chat_id, message_id, text, markup)
    else:
        safe_send(chat_id, text, markup)


def send_admin_users(chat_id, message_id=None, page=0, edit=True):
    users = get_all_users()
    total_pages = max(1, (len(users) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(users))
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users[start:end]:
        name = escape((u.get("first_name") or u.get("username") or f"User_{u['user_id']}")[:24])
        kb.row(btn(f"👤 {name} ({u['user_id']})",
                   callback_data=f"admin_view_user_{u['user_id']}"))
    nav = []
    if page > 0:
        nav.append(btn("⬅️ PREV", callback_data=f"admin_users_page_{page - 1}"))
    if end < len(users):
        nav.append(btn("NEXT ➡️", callback_data=f"admin_users_page_{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(btn("🔙 ADMIN MENU", callback_data="admin_panel"))
    text = (
        f"👥 <b>USERS</b> — page {page + 1}/{total_pages}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Tap a user for details.\n"
        f"{FOOTER}"
    )
    if edit and message_id:
        safe_edit(chat_id, message_id, text, kb)
    else:
        safe_send(chat_id, text, kb)


def send_admin_user_detail(chat_id, message_id, target_id):
    u = get_user(target_id)
    if not u:
        safe_edit(chat_id, message_id, "❌ User not found.", None)
        return
    fbs = get_user_firebases(target_id)
    joined = "✅" if u.get("channel_joined") else "❌"
    lines = []
    for r in fbs[:12]:
        lines.append(
            f"{_status_badge(r['status'])} <code>{escape(_short(r['firebase_url'], 32))}</code>"
            f" | 🖥️ {r['device_count']} | 📱 {r['number_count']}")
    fb_text = "\n".join(lines) if lines else "• No Firebase URLs"
    if len(fbs) > 12:
        fb_text += f"\n• ... and {len(fbs) - 12} more (see 🔥 MANAGE FIREBASE)"

    text = (
        f"👤 <b>USER DETAIL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"👤 Name: <b>{escape(u.get('first_name') or 'N/A')}</b>\n"
        f"📛 Username: @{escape(u.get('username') or 'N/A')}\n"
        f"📅 Joined: {u.get('registered_at') or 'N/A'}\n"
        f"📢 Channel: {joined}\n"
        f"📁 Firebase entries: <b>{len(fbs)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{fb_text}\n"
        f"{FOOTER}"
    )
    kb = InlineKeyboardMarkup(row_width=1)
    for r in fbs[:12]:
        kb.row(btn(f"🗑️ DELETE — {_short(r['firebase_url'], 30)}",
                   callback_data=f"admin_fb_del_{r['id']}"))
    kb.row(btn("🔙 USERS", callback_data="admin_users"),
           btn("👑 ADMIN MENU", callback_data="admin_panel"))
    safe_edit(chat_id, message_id, text, kb)


def send_admin_firebases(chat_id, message_id=None, page=0, edit=True):
    rows = get_all_firebases()
    total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(rows))
    kb = InlineKeyboardMarkup(row_width=2)
    for r in rows[start:end]:
        kb.row(btn(f"{_status_badge(r['status'])} {_short(r['firebase_url'], 26)}",
                   callback_data=f"admin_fb_view_{r['id']}"),
               btn("🗑️", callback_data=f"admin_fb_del_{r['id']}"))
    nav = []
    if page > 0:
        nav.append(btn("⬅️ PREV", callback_data=f"admin_fb_page_{page - 1}"))
    if end < len(rows):
        nav.append(btn("NEXT ➡️", callback_data=f"admin_fb_page_{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(btn("🔙 ADMIN MENU", callback_data="admin_panel"))
    text = (
        f"🔥 <b>MANAGE FIREBASE</b> — page {page + 1}/{total_pages}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Total entries: <b>{len(rows)}</b>\n"
        f"Tap an entry to view, 🗑️ to delete.\n"
        f"{FOOTER}"
    )
    if edit and message_id:
        safe_edit(chat_id, message_id, text, kb)
    else:
        safe_send(chat_id, text, kb)


def send_admin_firebase_detail(chat_id, message_id, fb_id):
    r = get_firebase_by_id(fb_id)
    if not r:
        safe_edit(chat_id, message_id, "❌ Entry not found.", None)
        return
    owner = get_user(r["user_id"])
    owner_name = "Unknown"
    if owner:
        owner_name = escape(owner.get("first_name") or f"User_{r['user_id']}")
    text = (
        f"🔥 <b>FIREBASE DETAIL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <code>{escape(r['firebase_url'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Entry ID: <code>{r['id']}</code>\n"
        f"👤 Owner: <b>{owner_name}</b> (<code>{r['user_id']}</code>)\n"
        f"📅 Added: {r['added_at']}\n"
        f"📊 Status: {_status_badge(r['status'])}\n"
        f"🖥️ Devices: <b>{r['device_count']}</b>\n"
        f"📱 Numbers: <b>{r['number_count']}</b>\n"
        f"🕒 Last checked: {r['last_checked'] or 'Never'}\n"
        f"{FOOTER}"
    )
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(btn("🗑️ DELETE ENTRY", callback_data=f"admin_fb_del_{r['id']}"))
    kb.row(btn("🔙 ALL FIREBASE", callback_data="admin_firebases"))
    safe_edit(chat_id, message_id, text, kb)


def confirm_admin_delete_firebase(chat_id, message_id, fb_id):
    r = get_firebase_by_id(fb_id)
    if not r:
        safe_edit(chat_id, message_id, "❌ Entry not found.", None)
        return
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(btn("🗑️ YES, DELETE", callback_data=f"admin_fb_del_yes_{fb_id}"))
    kb.row(btn("❌ CANCEL", callback_data="admin_firebases"))
    safe_edit(
        chat_id,
        message_id,
        f"🗑️ <b>Confirm deletion?</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<code>{escape(r['firebase_url'])}</code>\n\n"
        f"👤 Owner: <code>{r['user_id']}</code>\n"
        f"⚠️ This removes the URL from the database permanently.\n"
        f"{FOOTER}",
        kb,
    )


def admin_delete_firebase(chat_id, message_id, fb_id, call):
    r = get_firebase_by_id(fb_id)
    if not r:
        bot.answer_callback_query(call.id, "❌ Entry not found.", show_alert=True)
        return
    if delete_firebase(fb_id):
        bot.answer_callback_query(call.id, "✅ Deleted!", show_alert=True)
        send_admin_firebases(chat_id, message_id, page=0, edit=True)
    else:
        bot.answer_callback_query(call.id, "❌ Delete failed.", show_alert=True)


# ════════════════════════════════════════════════════════════════════════════
# 14. HANDLERS
# ════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def handle_start(message):
    try:
        uid = message.from_user.id
        create_user(uid, message.from_user.username, message.from_user.first_name)
        if not check_channel_membership(uid):
            send_join_required(uid)
            return
        show_main_menu(uid, message_id=message.message_id, edit=True)
    except Exception as e:
        logger.exception("start handler failed: %s", e)


@bot.message_handler(commands=["admin"])
def handle_admin_command(message):
    try:
        uid = message.from_user.id
        user = get_user(uid)
        if not user:
            create_user(uid, message.from_user.username, message.from_user.first_name)
            user = get_user(uid)
        if not is_admin(user):
            bot.reply_to(message, "⛔ Access denied.")
            return
        if not check_channel_membership(uid):
            send_join_required(uid)
            return
        send_admin_menu(uid, message_id=message.message_id, edit=True)
    except Exception as e:
        logger.exception("admin command failed: %s", e)


@bot.message_handler(commands=["cancel"])
def handle_cancel(message):
    try:
        uid = message.from_user.id
        with _state_lock:
            _firebase_states.pop(uid, None)
        bot.reply_to(message, "❌ Cancelled.\nPress /start to open the menu.")
    except Exception as e:
        logger.exception("cancel handler failed: %s", e)


@bot.message_handler(func=lambda m: True)
def handle_text(message):
    try:
        uid = message.from_user.id
        user = get_user(uid)
        if not user:
            bot.reply_to(message, "Press /start to begin.")
            return
        if not check_channel_membership(uid):
            send_join_required(uid)
            return
        text = (message.text or "").strip()
        if text.startswith("/"):
            return
        with _state_lock:
            state = _firebase_states.get(uid)
        if state and state.get("step") == "awaiting_url":
            handle_add_firebase_text(uid, text)
            return
        bot.reply_to(message,
                     f"❓ Please use the menu buttons below.\n"
                     f"Press /start to open the main menu.\n"
                     f"{FOOTER}")
    except Exception as e:
        logger.exception("text handler failed: %s", e)


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    try:
        uid = call.from_user.id
        data = call.data or ""
        chat_id = call.message.chat.id if call.message else uid
        message_id = call.message.message_id if call.message else None

        if data == "check_channel":
            if check_channel_membership(uid):
                bot.answer_callback_query(call.id, "✅ Channel joined! Welcome!")
                show_main_menu(chat_id, message_id, edit=True)
            else:
                bot.answer_callback_query(call.id,
                                          "❌ You have not joined the channel yet. "
                                          "Tap JOIN first!", show_alert=True)
            return

        user = get_user(uid)
        if not user:
            bot.answer_callback_query(call.id, "Please press /start first.")
            return

        # Channel gate (admins are exempt so the panel always works)
        if not is_admin(user) and not check_channel_membership(uid):
            send_join_required(uid)
            bot.answer_callback_query(call.id)
            return

        if data == "main_menu":
            show_main_menu(chat_id, message_id, edit=True)
            bot.answer_callback_query(call.id)
            return

        if data == "help":
            send_help(chat_id, message_id, edit=True)
            bot.answer_callback_query(call.id)
            return

        if data == "add_firebase":
            start_add_firebase(chat_id, message_id)
            bot.answer_callback_query(call.id)
            return

        # ─────────── ADMIN ROUTING ───────────
        if data.startswith("admin"):
            if not is_admin(user):
                bot.answer_callback_query(call.id, "❌ Unauthorized.", show_alert=True)
                return
            handle_admin_callback(call, chat_id, message_id, data)
            return

        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.exception("callback handler failed: %s", e)


def handle_admin_callback(call, chat_id, message_id, data):
    if data == "admin_panel":
        send_admin_menu(chat_id, message_id, edit=True)
        return

    if data == "admin_stats":
        send_admin_stats(chat_id, message_id, edit=True)
        return

    if data == "admin_users":
        send_admin_users(chat_id, message_id, page=0, edit=True)
        return

    if data.startswith("admin_users_page_"):
        try:
            page = int(data.split("_")[-1])
        except ValueError:
            page = 0
        send_admin_users(chat_id, message_id, page=page, edit=True)
        return

    if data.startswith("admin_view_user_"):
        try:
            target = int(data.split("_")[-1])
        except ValueError:
            return
        send_admin_user_detail(chat_id, message_id, target)
        return

    if data == "admin_firebases":
        send_admin_firebases(chat_id, message_id, page=0, edit=True)
        return

    if data.startswith("admin_fb_page_"):
        try:
            page = int(data.split("_")[-1])
        except ValueError:
            page = 0
        send_admin_firebases(chat_id, message_id, page=page, edit=True)
        return

    if data.startswith("admin_fb_view_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        send_admin_firebase_detail(chat_id, message_id, fb_id)
        return

    if data.startswith("admin_fb_del_yes_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        admin_delete_firebase(chat_id, message_id, fb_id, call)
        return

    if data.startswith("admin_fb_del_"):
        try:
            fb_id = int(data.split("_")[-1])
        except ValueError:
            return
        confirm_admin_delete_firebase(chat_id, message_id, fb_id)
        return

    bot.answer_callback_query(call.id)


# ════════════════════════════════════════════════════════════════════════════
# 15. MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(" 🔥 VIEDIET FIREBASE DETECTOR BOT")
    print("    Made by viediet")
    print("=" * 60)

    init_db()
    start_job_workers()

    logger.info("Bot started polling...")
    try:
        bot.infinity_polling(long_polling_timeout=20, skip_pending=True)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user. Shutting down gracefully...")
    except Exception as e:
        logger.error("Fatal polling error: %s", e)
    finally:
        try:
            bot.stop_polling()
        except Exception:
            pass
    print("👋 Bot stopped. Goodbye!")


if __name__ == "__main__":
    main()
