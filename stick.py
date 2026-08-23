#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔═══════════════════════════════════════════════════════════════════╗
║    MACCARON REFERRAL BOT — STEP-BY-STEP SECURE EDITION             ║
║                                                                   ║
║   FLOW (har step locked, no shortcut):                            ║
║     1. /start  ─────────────────────────── deep-link referral      ║
║     2. Channel join  ──────────────────── FORCED, verify karke    ║
║     3. Math CAPTCHA ───────────────────── solve karo, tab access   ║
║     4. Maccaron refer code ────────────── apna code daalo          ║
║     5. Phone → OTP → Signup ───────────── 1 registration = 1 point ║
║                                                                   ║
║   POINTS SYSTEM:                                                  ║
║     • Naya user (captcha ke baad)  = +5 free points               ║
║     • Har friend (join+captcha)    = +10 points                   ║
║     • Har registration             = -1 point                     ║
║                                                                   ║
║   ANTI-COPY / ANTI-BYPASS:                                        ║
║     • Step-by-step gate (join → captcha → code)                   ║
║     • Self-referral block, duplicate referral block               ║
║     • Referral credit SIRF captcha ke baad                        ║
║     • Captcha 3 fail = 10 min lock                                ║
║     • Personal refer code change with unique check                ║
║                                                                   ║
║   RUN:  python mc.py                                              ║
║   TOKEN: token.txt (ya BOT_TOKEN env)                             ║
║   ADMIN: admins.txt (ya ADMIN_IDS env)                            ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import random
import re
import sqlite3
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_token():
    t = os.getenv("BOT_TOKEN").strip()
    if not t:
        try:
            t = open(os.path.join(BASE_DIR, "token.txt"), encoding="utf-8").read().strip()
        except Exception:
            t = ""
    if not t:
        t = ""
    return t


def _load_admins():
    ids = [int(x) for x in os.getenv("ADMIN_IDS", "1364476174").split(",") if x.strip().isdigit()]
    if not ids:
        try:
            for line in open(os.path.join(BASE_DIR, "admins.txt"), encoding="utf-8"):
                line = line.strip()
                if line.isdigit():
                    ids.append(int(line))
        except Exception:
            pass
    return ids


# ================== CONFIG ==================
BOT_TOKEN = _load_token()
BOT_USERNAME = os.getenv("BOT_USERNAME", "Viediet_MACCARON_bot")     # without @
ADMIN_IDS = _load_admins()

# default referral used when admin runs /auto without their own Maccaron code
DEFAULT_REFERRAL = "RAND4FE6AFDB"


def is_admin(user_id):
    return str(user_id) in [str(a) for a in ADMIN_IDS]


def points_display(user_id):
    if is_admin(user_id):
        return "∞ (unlimited)"
    u = get_user(user_id)
    return str(u["points"]) if u else "0"

# ── Forced channels (bot ko channel ka admin/member hona zaroori) ──
REQUIRED_CHANNELS = [
    {"chat_id": "@viedietlooters", "title": "VIEDIET LOOTERS"},
]

# ── Points system ──
INITIAL_POINTS = 5            # naye user ko captcha ke baad free points
REFERRAL_POINTS = 10          # har confirmed friend = +10 points (10 uses)
POINTS_PER_USE = 1            # har registration me points kharch

# ── Captcha ──
CAPTCHA_MAX_FAILS = 3
CAPTCHA_LOCK_SECONDS = 600    # 10 min lock after 3 wrong attempts

# ── Maccaron API ──
GRAPHQL_URL = "https://graphql.maccaron.in/graphql/"
HEADERS = {
    "accept": "application/graphql-response+json,application/json;q=0.9",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "content-type": "application/json",
    "origin": "https://maccaron.in",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://maccaron.in/",
    "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
}

IO_POOL = ThreadPoolExecutor(max_workers=20)
API_SEMAPHORE = threading.Semaphore(5)

DB_PATH = os.path.join(BASE_DIR, "maccaron.db")

# ── Conversation states ──
CHANNEL_CHECK, CAPTCHA, REFERRAL, PHONE, OTP, CHANGE_MC, CHANGE_PC = range(7)

# ── Colored button emoji IDs (Bot API 7.11+) ──
# (unused, kept for reference)
EMOJI_BLUE = "5373141891321699086"
EMOJI_RED = "5370810157871667232"
EMOJI_GREEN = "5471984997361523302"

_db_lock = threading.Lock()


# ════════════════════════════════════════════════════════════════════
#  COLORED BUTTON HELPER (style + icon via api_kwargs)
# ════════════════════════════════════════════════════════════════════
def col(text, cdata=None, url=None, style=None, emoji=None):
    """Colored InlineKeyboardButton — Telegram Bot API 7.11+ style.
    Valid styles: primary, success, danger (NOT secondary)."""
    api = {}
    if style in ("primary", "success", "danger"):
        api["style"] = style
    if emoji:
        api["icon_custom_emoji_id"] = emoji
    kwargs = {}
    if cdata is not None:
        kwargs["callback_data"] = cdata
    if url:
        kwargs["url"] = url
    return InlineKeyboardButton(text, **kwargs, api_kwargs=api or None)


def blue(text, cdata=None, url=None):
    return col(text, cdata=cdata, url=url, style="primary")


def red(text, cdata=None, url=None):
    return col(text, cdata=cdata, url=url, style="danger")


def green(text, cdata=None, url=None):
    return col(text, cdata=cdata, url=url, style="success")


def grey(text, cdata=None, url=None):
    return col(text, cdata=cdata, url=url)


# ════════════════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════════════════
def init_db():
    with _db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id TEXT PRIMARY KEY,
                username TEXT DEFAULT '',
                full_name TEXT DEFAULT '',
                maccaron_code TEXT DEFAULT '',
                personal_code TEXT UNIQUE,
                referred_by TEXT DEFAULT '',
                points INTEGER DEFAULT 0,
                referral_count INTEGER DEFAULT 0,
                captcha_ok INTEGER DEFAULT 0,
                captcha_fails INTEGER DEFAULT 0,
                captcha_locked_until REAL DEFAULT 0,
                channel_ok INTEGER DEFAULT 0,
                phone TEXT DEFAULT '',
                state TEXT DEFAULT 'new',
                last_active REAL DEFAULT 0,
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS referrals(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id TEXT,
                referred_id TEXT UNIQUE,
                credited INTEGER DEFAULT 0,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS results(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT, name TEXT, email TEXT, status TEXT,
                details TEXT, referral_code TEXT, user_id TEXT, ts REAL
            );

            CREATE TABLE IF NOT EXISTS user_phones(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT, phone TEXT, first_seen REAL, last_seen REAL,
                status TEXT, referral_code TEXT, count INTEGER DEFAULT 1,
                UNIQUE(user_id, phone)
            );
            """
        )
        conn.commit()
        conn.close()


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _b36(n):
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    n = int(n)
    s = ""
    while n:
        n, r = divmod(n, 36)
        s = chars[r] + s
    return s or "0"


def default_personal_code(user_id):
    return "VD" + _b36(user_id)


# ── user helpers ──
def get_user(user_id):
    with _db_lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (str(user_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_user_by_code(code):
    if not code:
        return None
    with _db_lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM users WHERE personal_code=?", (str(code).strip(),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def create_user(user_id, username, full_name, referred_by=""):
    with _db_lock:
        conn = _conn()
        try:
            now = time.time()
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id, username, full_name, personal_code, referred_by, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(user_id), username or "", full_name or "", default_personal_code(user_id),
                 str(referred_by or ""), now, now))
            conn.commit()
        finally:
            conn.close()
    return get_user(user_id)


def update_user(user_id, **fields):
    if not fields:
        return
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _db_lock:
        conn = _conn()
        try:
            conn.execute(f"UPDATE users SET {sets} WHERE user_id=?",
                         (*fields.values(), str(user_id)))
            conn.commit()
        finally:
            conn.close()


def _inc_field(user_id, field, n=1):
    with _db_lock:
        conn = _conn()
        try:
            conn.execute(f"UPDATE users SET {field} = {field} + ? , updated_at=? WHERE user_id=?",
                         (int(n), time.time(), str(user_id)))
            conn.commit()
        finally:
            conn.close()


def add_points(user_id, n):
    _inc_field(user_id, "points", int(n))


def set_points(user_id, n):
    update_user(user_id, points=int(n))


def can_use(user_id):
    # ADMIN: full access, unlimited usage
    if is_admin(user_id):
        return True, 999999
    u = get_user(user_id)
    if not u or u["points"] <= 0:
        return False, 0
    return True, u["points"]


def consume_point(user_id):
    # ADMIN: never deduct points (unlimited)
    if is_admin(user_id):
        return True
    u = get_user(user_id)
    if not u or u["points"] <= 0:
        return False
    with _db_lock:
        conn = _conn()
        try:
            cur = conn.execute("UPDATE users SET points = points - ? WHERE user_id=? AND points >= ?",
                               (POINTS_PER_USE, str(user_id), POINTS_PER_USE))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ── referral ──
def add_referral(referrer_id, referred_id):
    referrer_id = str(referrer_id)
    referred_id = str(referred_id)
    if referrer_id == referred_id or not referrer_id:
        return False
    with _db_lock:
        conn = _conn()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO referrals(referrer_id, referred_id, created_at) VALUES(?,?,?)",
                (referrer_id, referred_id, time.time()))
            conn.commit()
            return cur.lastrowid is not None and cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()


def get_pending_referral(referred_id):
    with _db_lock:
        conn = _conn()
        try:
            row = conn.execute(
                "SELECT * FROM referrals WHERE referred_id=? AND credited=0", (str(referred_id),)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def credit_referral(referred_id):
    """Referee ke captcha complete hote hi referrer ko +REFERRAL_POINTS."""
    pending = get_pending_referral(referred_id)
    if not pending:
        return None
    referrer_id = pending["referrer_id"]
    with _db_lock:
        conn = _conn()
        try:
            cur = conn.execute(
                "UPDATE referrals SET credited=1 WHERE referred_id=? AND credited=0",
                (str(referred_id),))
            conn.commit()
            if cur.rowcount <= 0:
                return None
        finally:
            conn.close()
    add_points(referrer_id, REFERRAL_POINTS)
    _inc_field(referrer_id, "referral_count", 1)
    return referrer_id


def get_ref_count(referrer_id):
    u = get_user(referrer_id)
    return u["referral_count"] if u else 0


def get_referrals(referrer_id, limit=20):
    with _db_lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT r.referred_id, u.username, u.full_name, r.created_at "
                "FROM referrals r LEFT JOIN users u ON u.user_id=r.referred_id "
                "WHERE r.referrer_id=? ORDER BY r.id DESC LIMIT ?",
                (str(referrer_id), int(limit))).fetchall()
            return [dict(x) for x in rows]
        finally:
            conn.close()


# ── captcha ──
def make_captcha():
    op = random.choice(["+", "-", "*"])
    if op == "+":
        a, b = random.randint(5, 15), random.randint(5, 15)
    elif op == "-":
        a = random.randint(10, 25)
        b = random.randint(2, a - 1)
    else:
        a, b = random.randint(3, 9), random.randint(3, 9)
    ans = {"+": a + b, "-": a - b, "*": a * b}[op]
    opts = {ans}
    guard = 0
    while len(opts) < 4 and guard < 60:
        guard += 1
        cand = ans + random.randint(-8, 8)
        if cand >= 0:
            opts.add(cand)
    pool = list(range(0, ans + 12))
    random.shuffle(pool)
    for x in pool:
        if len(opts) >= 4:
            break
        opts.add(x)
    opts = list(opts)[:4]
    random.shuffle(opts)
    return op, a, b, ans, opts


# ── result / phone tracking (kept from original) ──
def log_result(phone, name, email, status, details="", referral_code="", user_id=""):
    with _db_lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO results(phone,name,email,status,details,referral_code,user_id,ts) VALUES(?,?,?,?,?,?,?,?)",
                (phone, name, email, status, details, referral_code, user_id, time.time()))
            existing = conn.execute(
                "SELECT count, status FROM user_phones WHERE user_id=? AND phone=?",
                (str(user_id), phone)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE user_phones SET last_seen=?, status=?, referral_code=?, count=count+1 WHERE user_id=? AND phone=?",
                    (time.time(), status, referral_code, str(user_id), phone))
            else:
                try:
                    conn.execute(
                        "INSERT INTO user_phones(user_id,phone,first_seen,last_seen,status,referral_code,count) VALUES(?,?,?,?,?,?,?)",
                        (str(user_id), phone, time.time(), time.time(), status, referral_code, 1))
                except sqlite3.IntegrityError:
                    conn.execute(
                        "UPDATE user_phones SET last_seen=?, status=?, referral_code=?, count=count+1 WHERE user_id=? AND phone=?",
                        (time.time(), status, referral_code, str(user_id), phone))
            conn.commit()
        finally:
            conn.close()


def get_user_success_count(user_id):
    with _db_lock:
        conn = _conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM user_phones WHERE user_id=? AND status='success'",
                                (str(user_id),)).fetchone()[0]
        finally:
            conn.close()


def get_user_phone_status(user_id, phone):
    with _db_lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT status FROM user_phones WHERE user_id=? AND phone=?",
                               (str(user_id), phone)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()


def get_user_total_processed(user_id):
    with _db_lock:
        conn = _conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM user_phones WHERE user_id=?", (str(user_id),)).fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM user_phones WHERE user_id=? AND status!='success'",
                                  (str(user_id),)).fetchone()[0]
            return total, failed
        finally:
            conn.close()


def get_user_recent_phones(user_id, limit=10):
    with _db_lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT phone, status FROM user_phones WHERE user_id=? ORDER BY last_seen DESC LIMIT ?",
                (str(user_id), int(limit))).fetchall()
            return [(r[0], r[1]) for r in rows]
        finally:
            conn.close()


def get_global_stats():
    with _db_lock:
        conn = _conn()
        try:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_success = conn.execute("SELECT COUNT(*) FROM user_phones WHERE status='success'").fetchone()[0]
            total_phones = conn.execute("SELECT COUNT(*) FROM user_phones").fetchone()[0]
            return total_users, total_success, total_phones
        finally:
            conn.close()


def get_all_users(offset=0, limit=20):
    with _db_lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT user_id, personal_code, maccaron_code, points, referral_count, captcha_ok, created_at "
                "FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?", (int(limit), int(offset))).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def reset_user_session(user_id):
    with _db_lock:
        conn = _conn()
        try:
            conn.execute("DELETE FROM users WHERE user_id=?", (str(user_id),))
            conn.execute("DELETE FROM user_phones WHERE user_id=?", (str(user_id),))
            conn.execute("DELETE FROM results WHERE user_id=?", (str(user_id),))
            conn.commit()
        finally:
            conn.close()


# ════════════════════════════════════════════════════════════════════
#  CHANNEL CHECK
# ════════════════════════════════════════════════════════════════════
async def check_channel_joins(bot, user_id):
    not_joined = []
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=int(user_id))
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception as e:
            logger.error(f"Channel check {ch['chat_id']}: {e}")
            not_joined.append(ch)
    return not_joined


def channel_join_keyboard():
    rows = []
    for ch in REQUIRED_CHANNELS:
        rows.append([blue(f"📢 Join {ch['title']}",
                          url=f"https://t.me/{ch['chat_id'].lstrip('@')}")])
    rows.append([green("✅ I've Joined — Verify", cdata="action_check_joined")])
    rows.append([red("🔙 Back", cdata="action_back_to_start")])
    return InlineKeyboardMarkup(rows)


# ════════════════════════════════════════════════════════════════════
#  MACCARON API
# ════════════════════════════════════════════════════════════════════
def generate_random_user():
    first_names = [
        "Rajat", "Amit", "Suresh", "Priya", "Ananya", "Rahul",
        "Neha", "Vikram", "Kavya", "Arjun", "Deepa", "Ravi",
        "Meera", "Kiran", "Pooja", "Sanjay", "Lata", "Vivek",
        "Sunita", "Manish",
    ]
    last_names = [
        "Kumar", "Sharma", "Patel", "Singh", "Gupta", "Reddy",
        "Nair", "Mehta", "Joshi", "Verma", "Das", "Rao",
        "Pillai", "Chauhan", "Agarwal", "Bhat", "Iyer", "Malhotra",
    ]
    first = random.choice(first_names)
    last = random.choice(last_names)
    random_str = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"{first.lower()}{random_str}@gmail.com"
    password = "".join(random.choices(string.digits, k=11))
    return first, last, email, password


def send_create_otp(phone):
    payload = {
        "operationName": "createOtp",
        "variables": {"input": {"receiver": phone}},
        "query": "mutation createOtp($input: OtpInput!) {\n  createOtp(input: $input) {\n    otp {\n      receiver\n      status\n      __typename\n    }\n    errors {\n      field\n      message\n      __typename\n    }\n    __typename\n  }\n}",
    }
    try:
        resp = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload, timeout=15)
        try:
            data = resp.json()
            if data is None:
                return {"error": "API returned null response"}
            return data
        except ValueError:
            return {"error": f"Invalid JSON response: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def verify_otp(phone, otp):
    payload = {
        "operationName": "verifyOtp",
        "variables": {"input": {"receiver": phone, "value": otp}},
        "query": "mutation verifyOtp($input: VerifyOtpInput!) {\n  verifyOtp(input: $input) {\n    otp {\n      id\n      receiver\n      value\n      status\n      __typename\n    }\n    verified\n    errors {\n      field\n      message\n      __typename\n    }\n    __typename\n  }\n}",
    }
    try:
        resp = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload, timeout=15)
        try:
            data = resp.json()
            if data is None:
                return {"error": "API returned null response"}
            return data
        except ValueError:
            return {"error": f"Invalid JSON response: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def customer_signup(first_name, last_name, email, password, otp_id, otp_value, mobile, referral_code):
    payload = {
        "operationName": "customerSignUp",
        "variables": {
            "input": {
                "firstName": first_name, "lastName": last_name, "email": email,
                "password": password, "otpId": otp_id, "otpValue": otp_value,
                "mobileNumber": mobile, "referralCode": referral_code,
                "cartToken": None, "signupPlatform": "Web",
            }
        },
        "query": "mutation customerSignUp($input: CustomerSignUpInput!) {\n  customerSignUp(input: $input) {\n    user {\n      id\n      email\n      __typename\n    }\n    errors {\n      field\n      message\n      __typename\n    }\n    __typename\n  }\n}",
    }
    try:
        resp = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload, timeout=15)
        try:
            data = resp.json()
            if data is None:
                return {"error": "API returned null response"}
            return data
        except ValueError:
            return {"error": f"Invalid JSON response: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def do_send_otp(phone, referral_code, user_id):
    with API_SEMAPHORE:
        api_phone = phone[-10:] if len(phone) > 10 else phone
        first_name, last_name, email, password = generate_random_user()
        full_name = f"{first_name} {last_name}"
        otp_response = send_create_otp(api_phone)
        if not isinstance(otp_response, dict):
            otp_response = {"error": f"Unexpected response: {otp_response}"}
        if "error" in otp_response:
            msg = otp_response["error"]
            log_result(phone, full_name, email, "otp_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "otp_failed", "reason": msg}
        gql_errors = otp_response.get("errors") or []
        if gql_errors:
            msg = gql_errors[0].get("message", "Unknown GraphQL error")
            log_result(phone, full_name, email, "otp_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "otp_failed", "reason": msg}
        otp_data = (otp_response.get("data") or {}).get("createOtp") or {}
        otp_status = (otp_data.get("otp") or {}).get("status")
        if otp_status != "SENT":
            errors = otp_data.get("errors", [])
            msg = errors[0]["message"] if errors else f"OTP not sent (status: {otp_status})"
            log_result(phone, full_name, email, "otp_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "otp_failed", "reason": msg}
        return {"status": "otp_sent"}


def do_verify_and_signup(phone, referral_code, otp, user_id):
    with API_SEMAPHORE:
        api_phone = phone[-10:] if len(phone) > 10 else phone
        verify_response = verify_otp(api_phone, otp)
        if not isinstance(verify_response, dict):
            verify_response = {"error": f"Unexpected response: {verify_response}"}
        if "error" in verify_response:
            msg = verify_response["error"]
            log_result(phone, "", "", "verify_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "verify_failed", "reason": msg}
        gql_errors = verify_response.get("errors") or []
        if gql_errors:
            msg = gql_errors[0].get("message", "Unknown GraphQL error")
            log_result(phone, "", "", "verify_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "verify_failed", "reason": msg}
        verify_data = (verify_response.get("data") or {}).get("verifyOtp") or {}
        if not verify_data.get("verified"):
            errors = verify_data.get("errors", [])
            msg = errors[0]["message"] if errors else "OTP verification failed"
            log_result(phone, "", "", "verify_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "verify_failed", "reason": msg}
        otp_id = verify_data["otp"]["id"]
        otp_value = verify_data["otp"]["value"]
        first_name, last_name, email, password = generate_random_user()
        full_name = f"{first_name} {last_name}"
        signup_response = customer_signup(
            first_name, last_name, email, password, otp_id, otp_value, api_phone, referral_code)
        if not isinstance(signup_response, dict):
            signup_response = {"error": f"Unexpected response: {signup_response}"}
        if "error" in signup_response:
            msg = signup_response["error"]
            log_result(phone, full_name, email, "signup_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "signup_failed", "reason": msg}
        gql_errors = signup_response.get("errors") or []
        if gql_errors:
            msg = gql_errors[0].get("message", "Unknown GraphQL error")
            log_result(phone, full_name, email, "signup_failed", details=msg,
                       referral_code=referral_code, user_id=user_id)
            return {"status": "signup_failed", "reason": msg}
        signup = (signup_response.get("data") or {}).get("customerSignUp") or {}
        if signup.get("user"):
            log_result(phone, full_name, email, "success",
                       referral_code=referral_code, user_id=user_id)
            return {
                "status": "success", "name": full_name,
                "email": email, "user_maccaron_id": signup["user"]["id"],
            }
        errors = signup.get("errors", [])
        msg = errors[0]["message"] if errors else str(signup_response)
        log_result(phone, full_name, email, "signup_failed", details=msg,
                   referral_code=referral_code, user_id=user_id)
        return {"status": "signup_failed", "reason": msg}


# ════════════════════════════════════════════════════════════════════
#  FIREBASE AUTO-OTP AUTOMATION  (UJALA-pattern: All_Users/sms/{device_id})
# ════════════════════════════════════════════════════════════════════
import json as _json
import threading

# Maccaron OTP SMS: "695486 is your Maccaron verification OTP for mobile ..."
MACCARON_OTP_RE = re.compile(r"(\d{4,6})\s+is your Maccaron verification OTP", re.IGNORECASE)


def extract_maccaron_otp(body):
    """Robustly pull the 6-digit Maccaron OTP from an SMS body.

    Matches the exact Maccaron format: '<6 digits> is your Maccaron verification OTP...'
    Falls back to any standalone 6-digit code when 'maccaron' appears in the body.
    """
    if not body:
        return None
    body = str(body)
    low = body.lower()
    if "maccaron" in low or "macron" in low or "maccrn" in low:
        m = re.search(r"(?<![\d.])(\d{6})(?![\d.])", body)
        if m:
            return m.group(1)
    m = MACCARON_OTP_RE.search(body)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6})\b", body)
    return m.group(1) if m else None

# Global automation state
AUTO_RUNNING = {"v": False}
AUTO_STOP_REQUESTED = {"v": False}


FIREBASE_PANELS = [
    {"tag": '47.apk', "url": 'https://hdjdjdj-a73f2-default-rtdb.firebaseio.com', "keys": ['AIzaSyCPJL-eDjeLSamlXrLz44ONBevUgSTVxzU']},
    {"tag": '61.apk', "url": 'https://muajob-29c86-default-rtdb.firebaseio.com', "keys": ['AIzaSyCMNuhFzhoDzPMbc3m7kUvm-qYD2fxhuy0']},
    {"tag": '63.apk', "url": 'https://dark-274b4-default-rtdb.firebaseio.com', "keys": ['AIzaSyApSpNpxolCsK96UD2MZRVqoKR7qNu7hoE']},
    {"tag": '65.apk', "url": 'https://dyydd-c53c8-default-rtdb.firebaseio.com', "keys": ['AIzaSyBIHeqqdiLPEzZ5CxpkSVh8J0jrSDqq9Y4']},
    {"tag": '66.apk', "url": 'https://gjhghjj-3d251-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'AdminPanel.apk', "url": 'https://smsgrabbeer-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyC-yL_7j_FcnKNwpVT81oCsYTB4yt4mIRA']},
    {"tag": 'Android (2).apk', "url": 'https://rantaishita-f7614-default-rtdb.firebaseio.com', "keys": ['AIzaSyAXeDnVzCBt7e-l1x5hb-2GZJr7wifUPDQ']},
    {"tag": 'CARDTRY1-1.apk', "url": 'https://trying-90b4b-default-rtdb.firebaseio.com', "keys": ['AIzaSyAGUGYKDbUX1rFDhnk79dk3_XWIVxmXC-Y', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCyjAOZ3D45nzWaBn9pzEkdBUVlbxhCfMQ']},
    {"tag": 'CARDTRY1-1.apk', "url": 'https://newgodx-5b008-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyAGUGYKDbUX1rFDhnk79dk3_XWIVxmXC-Y', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCyjAOZ3D45nzWaBn9pzEkdBUVlbxhCfMQ']},
    {"tag": 'DamonPS2_Pro_-_PS2_Emulator_v5-0Pre2.apk', "url": 'https://damonps2-pro.firebaseio.com', "keys": ['AIzaSyC1MkFGHIJ2RmNBUWhll52tRnNkptMm5xo']},
    {"tag": 'Firebase 1', "url": 'https://rahulcscperosnl-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'Firebase 2', "url": 'https://pm-kisan-05jg-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'Firebase 3', "url": 'https://ajna-20fc4-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'Firebase 4', "url": 'https://lalannew5-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'Firebase 5', "url": 'https://myapp-8228a-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'GK 27Nov rto challan admin.apk', "url": 'https://rc-39-15-default-rtdb.firebaseio.com', "keys": ['AIzaSyBSbWYMdNYM-0tCYdY-kizOpvzonPW_-1s']},
    {"tag": 'GREEN PANEL ______ _ Ruff _ (2).apk', "url": 'https://ruff-panel-default-rtdb.firebaseio.com', "keys": ['AIzaSyBZHk0O8LYZSdbIZjbOihbgteb7QvV8LCA']},
    {"tag": 'Jamtara ____ (3).apk', "url": 'https://yourfirebase-default-rtdb.firebaseio.com', "keys": ['AIzaSnB1cdgCf8hSGRjx7sKuzfsmMQ_a2Uk2NlQ']},
    {"tag": 'Jamtara ____ (3).apk', "url": 'https://server-2-a095f-default-rtdb.firebaseio.com', "keys": ['AIzaSnB1cdgCf8hSGRjx7sKuzfsmMQ_a2Uk2NlQ']},
    {"tag": 'Jamtara ____ (3).apk', "url": 'https://server-1-c3501-default-rtdb.firebaseio.com', "keys": ['AIzaSnB1cdgCf8hSGRjx7sKuzfsmMQ_a2Uk2NlQ']},
    {"tag": 'Jamtara ____ (3).apk', "url": 'https://server-3-e44be-default-rtdb.firebaseio.com', "keys": ['AIzaSnB1cdgCf8hSGRjx7sKuzfsmMQ_a2Uk2NlQ']},
    {"tag": 'Medicien panel.apk', "url": 'https://e5turnament2-default-rtdb.firebaseio.com', "keys": ['AIzaSyDj9pR0AaoGKIlje-0E6QQ5hUZH-3gh-_Q']},
    {"tag": 'PM_ADMIN_L_V2.apk', "url": 'https://challan-758d1-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyBXJdXWfTCC4tSqD0nYXUbXaSISKhtjnrc']},
    {"tag": 'Panda ____ Admin 181_1.0.apk', "url": 'https://jamtara181-default-rtdb.firebaseio.com', "keys": ['AIzaSyCv4JJw_4ruIYnNjwuWqnvmk4FZz1n7F4M']},
    {"tag": 'Panel Wala V16.apk', "url": 'https://panel-wala-v16-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'Panel Wala V17.apk', "url": 'https://panel-wala-v11-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'Pm-Admin_v5_Final.apk', "url": 'https://navin512-54d6f-default-rtdb.firebaseio.com', "keys": ['AIzaSyBiCQmETLwj3ouuJkw7fCkhUtidDVh1x9I']},
    {"tag": 'RTO 63 ADMIN.apk', "url": 'https://rto-63-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyBHNK2QS-P75DLud5130Uo8bUm5j_biKzU']},
    {"tag": 'RTO ADMIN.apk', "url": 'https://activity-e16b3-default-rtdb.firebaseio.com', "keys": ['AIzaSyBg2FWKtNhoFd4Jd_dYIn3U2EUI3bsux4o']},
    {"tag": 'RTO Admin_1.0 (2).apk', "url": 'https://smas-8bff8-default-rtdb.firebaseio.com', "keys": ['AIzaSyC6tb3NaodXCW4Qh8KR8xTW5BteUTbwMc8']},
    {"tag": 'Rdx Admin 3.k.apk', "url": 'https://business-apps-ba1-f86b7-default-rtdb.firebaseio.com', "keys": ['AIzaSyACVxRuQ_vZEFceetyCQbJG6o_KFp2Ggf0']},
    {"tag": 'SPY MASTER.apk', "url": 'https://dyno-1b564-default-rtdb.firebaseio.com', "keys": ['AIzaSyCliMn51IHaR_mPG5MCSvWMK7toxntO7bQ']},
    {"tag": 'Sam Admin_1.3.apk', "url": 'https://rexxx-4c7a7-default-rtdb.firebaseio.com', "keys": ['AIzaSyDnVaMQ1RY6R1SyFy65TO2bOQXOC_b2VRA']},
    {"tag": 'Shoot Admin (2).apk', "url": 'https://kumarlive1-default-rtdb.firebaseio.com', "keys": ['AIzaSyAD6iCJUEDvl_XFCUZ0VeiGvH571FobXMM', 'AIzaSyCbT2cHC05tINJ3aOku1URGlAzWTG1IS1E']},
    {"tag": 'Shoot Admin 236 (2) (3).apk', "url": 'https://rahulcscperosnl-default-rtdb.firebaseio.com', "keys": ['AIzaSyA51lq8IG509h32yHtzaWWWzdyZNqemUkc', 'AIzaSyCbT2cHC05tINJ3aOku1URGlAzWTG1IS1E']},
    {"tag": 'Shoot Admin __.apk', "url": 'https://myapp-8228a-default-rtdb.firebaseio.com', "keys": ['AIzaSyCbT2cHC05tINJ3aOku1URGlAzWTG1IS1E']},
    {"tag": 'Shoot Admin-2.apk', "url": 'https://tryagainnew-58f1a-default-rtdb.firebaseio.com', "keys": ['AIzaSyATn6LDSqEYPCyY-yMKDhzVBO263WmYOqY', 'AIzaSyCbT2cHC05tINJ3aOku1URGlAzWTG1IS1E']},
    {"tag": 'Shoot Admin.apk', "url": 'https://lovefimus-default-rtdb.firebaseio.com', "keys": ['AIzaSyCbT2cHC05tINJ3aOku1URGlAzWTG1IS1E', 'AIzaSyD2Ry06YV58BdIjbhX5nvdY6MN1IQaRqGk']},
    {"tag": 'ZEN ADMIN_1.0.apk', "url": 'https://aaaa-b3749-default-rtdb.firebaseio.com', "keys": ['AIzaSyANHri0JIWEroUrloP97KGAcIOKMwT4UgU', 'AIzaSyD74TOQXfWjYvfwDQ06U38xhdRUIVJ4Afs']},
    {"tag": '______ ____M__N 3_____1.0.apk', "url": 'https://boi-3-8914d-default-rtdb.firebaseio.com', "keys": ['AIzaSyDc4HYWT6jdXAZbRB8wAa_I5HwVcffGfgY']},
    {"tag": '____________________ ____________________ - _____________ .a', "url": 'https://projectsb0810-default-rtdb.firebaseio.com', "keys": ['AIzaSyCAKj9lK1TggPOpafxeolFrhVz1hpepVlk']},
    {"tag": 'access20', "url": 'https://access20-3fc38-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'adsdasd.apk', "url": 'https://totla-panel-default-rtdb.firebaseio.com', "keys": ['AIzaSyD6Mlr26HvFPHYIv6h1EQhGBGg52xc5Z7Q']},
    {"tag": 'airto', "url": 'https://ai-rto-9-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'apkdriod', "url": 'https://apkdriod-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'apkdriod_f6fb9', "url": 'https://apkdriod-f6fb9-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'app2', "url": 'https://app-2-7ac78-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'asdtest', "url": 'https://asdtest-project-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'bankekyc', "url": 'https://bank-e-kyc-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'base (1) (10).apk', "url": 'https://rto9-d2b33-default-rtdb.firebaseio.com', "keys": ['AIzaSyC5pP7ZRx9h_Puc3nvbQ7-O8zzxVPXnl54']},
    {"tag": 'base (2) (11).apk', "url": 'https://pp30-fc7e5-default-rtdb.firebaseio.com', "keys": ['AIzaSyADzHYWclidHTO9vu1u2Wo51dAClnJaAqg']},
    {"tag": 'base (2) (4) (2).apk', "url": 'https://sssssmmmmsw-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyBTebmiVIh2_vFMgPJ0heGQDSSGJT6oZNA']},
    {"tag": 'base (2).apk', "url": 'https://rt51-6e1df-default-rtdb.firebaseio.com', "keys": ['AIzaSyByfSpvDUTzyEwIKgGfiupVSdUPMZe1vGs']},
    {"tag": 'base (27).apk', "url": 'https://rto-e-chall-4-default-rtdb.firebaseio.com', "keys": ['AIzaSyC-U9fBwuK610sUjZ4UAwppaWzSGHv0Cfc']},
    {"tag": 'base (28).apk', "url": 'https://yes2-ead3d-default-rtdb.firebaseio.com', "keys": ['AIzaSyCrhMuJqoSYIDc2O34nnCoqWXW9JVBYzlQ']},
    {"tag": 'base (29).apk', "url": 'https://sbi-yono-i31an-default-rtdb.firebaseio.com', "keys": ['AIzaSyDXGhxslZRGq3W3aoNzZgEwPysRHh1ycXw']},
    {"tag": 'base (3).apk', "url": 'https://rtx-c9-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyC0D_8y2VQ5rYqfPyr-anr67ewq1MskDZg']},
    {"tag": 'base (31).apk', "url": 'https://rameshwar-7okt-default-rtdb.firebaseio.com', "keys": ['AIzaSyBQbjvGvphUOGhjJlGBn7M5c8nSsJDP_XA']},
    {"tag": 'base (32).apk', "url": 'https://rto-44-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyArFzwZ1p3yOaTW-u6pEvjA44nIYIaCnzc']},
    {"tag": 'base (35).apk', "url": 'https://jamtara74-c231e-default-rtdb.firebaseio.com', "keys": ['AIzaSyA9ViACbG-4iDooKvf-QEBq0ICoM-uT-f0', 'AIzaSyAy5QbbJwK0gHrt3-LZmbU7PxuVU6ZDw50', 'AIzaSyCfzlvNg6vYgd69dcHB0dpf6H6Ipz_yZsY', 'AIzaSyCp7YJATv2jsGuH0QHZ7n2RwNQIWhNU5mM', 'AIzaSyDDUhxUpo2jXam6ez8Wz92y9Kebf9cVtIA', 'AIzaSyDiiky_NvdG7tsU1MRQePT0-NuKHvVoFAQ', 'AIzaSyDmvtf9EwVSsffqOJarrAgWaGAhYa-m1_E']},
    {"tag": 'base (35).apk', "url": 'https://raja252525raj-4ee9a-default-rtdb.firebaseio.com', "keys": ['AIzaSyA9ViACbG-4iDooKvf-QEBq0ICoM-uT-f0', 'AIzaSyAy5QbbJwK0gHrt3-LZmbU7PxuVU6ZDw50', 'AIzaSyCfzlvNg6vYgd69dcHB0dpf6H6Ipz_yZsY', 'AIzaSyCp7YJATv2jsGuH0QHZ7n2RwNQIWhNU5mM', 'AIzaSyDDUhxUpo2jXam6ez8Wz92y9Kebf9cVtIA', 'AIzaSyDiiky_NvdG7tsU1MRQePT0-NuKHvVoFAQ', 'AIzaSyDmvtf9EwVSsffqOJarrAgWaGAhYa-m1_E']},
    {"tag": 'base (35).apk', "url": 'https://raj254346kumar-84033-default-rtdb.firebaseio.com', "keys": ['AIzaSyA9ViACbG-4iDooKvf-QEBq0ICoM-uT-f0', 'AIzaSyAy5QbbJwK0gHrt3-LZmbU7PxuVU6ZDw50', 'AIzaSyCfzlvNg6vYgd69dcHB0dpf6H6Ipz_yZsY', 'AIzaSyCp7YJATv2jsGuH0QHZ7n2RwNQIWhNU5mM', 'AIzaSyDDUhxUpo2jXam6ez8Wz92y9Kebf9cVtIA', 'AIzaSyDiiky_NvdG7tsU1MRQePT0-NuKHvVoFAQ', 'AIzaSyDmvtf9EwVSsffqOJarrAgWaGAhYa-m1_E']},
    {"tag": 'base (35).apk', "url": 'https://salasali6990-1171d-default-rtdb.firebaseio.com', "keys": ['AIzaSyA9ViACbG-4iDooKvf-QEBq0ICoM-uT-f0', 'AIzaSyAy5QbbJwK0gHrt3-LZmbU7PxuVU6ZDw50', 'AIzaSyCfzlvNg6vYgd69dcHB0dpf6H6Ipz_yZsY', 'AIzaSyCp7YJATv2jsGuH0QHZ7n2RwNQIWhNU5mM', 'AIzaSyDDUhxUpo2jXam6ez8Wz92y9Kebf9cVtIA', 'AIzaSyDiiky_NvdG7tsU1MRQePT0-NuKHvVoFAQ', 'AIzaSyDmvtf9EwVSsffqOJarrAgWaGAhYa-m1_E']},
    {"tag": 'base (35).apk', "url": 'https://rahu80759-ac69b-default-rtdb.firebaseio.com', "keys": ['AIzaSyA9ViACbG-4iDooKvf-QEBq0ICoM-uT-f0', 'AIzaSyAy5QbbJwK0gHrt3-LZmbU7PxuVU6ZDw50', 'AIzaSyCfzlvNg6vYgd69dcHB0dpf6H6Ipz_yZsY', 'AIzaSyCp7YJATv2jsGuH0QHZ7n2RwNQIWhNU5mM', 'AIzaSyDDUhxUpo2jXam6ez8Wz92y9Kebf9cVtIA', 'AIzaSyDiiky_NvdG7tsU1MRQePT0-NuKHvVoFAQ', 'AIzaSyDmvtf9EwVSsffqOJarrAgWaGAhYa-m1_E']},
    {"tag": 'base (35).apk', "url": 'https://samar95476-54eb9-default-rtdb.firebaseio.com', "keys": ['AIzaSyA9ViACbG-4iDooKvf-QEBq0ICoM-uT-f0', 'AIzaSyAy5QbbJwK0gHrt3-LZmbU7PxuVU6ZDw50', 'AIzaSyCfzlvNg6vYgd69dcHB0dpf6H6Ipz_yZsY', 'AIzaSyCp7YJATv2jsGuH0QHZ7n2RwNQIWhNU5mM', 'AIzaSyDDUhxUpo2jXam6ez8Wz92y9Kebf9cVtIA', 'AIzaSyDiiky_NvdG7tsU1MRQePT0-NuKHvVoFAQ', 'AIzaSyDmvtf9EwVSsffqOJarrAgWaGAhYa-m1_E']},
    {"tag": 'base (35).apk', "url": 'https://samar84900-6f084-default-rtdb.firebaseio.com', "keys": ['AIzaSyA9ViACbG-4iDooKvf-QEBq0ICoM-uT-f0', 'AIzaSyAy5QbbJwK0gHrt3-LZmbU7PxuVU6ZDw50', 'AIzaSyCfzlvNg6vYgd69dcHB0dpf6H6Ipz_yZsY', 'AIzaSyCp7YJATv2jsGuH0QHZ7n2RwNQIWhNU5mM', 'AIzaSyDDUhxUpo2jXam6ez8Wz92y9Kebf9cVtIA', 'AIzaSyDiiky_NvdG7tsU1MRQePT0-NuKHvVoFAQ', 'AIzaSyDmvtf9EwVSsffqOJarrAgWaGAhYa-m1_E']},
    {"tag": 'base (4) (3).apk', "url": 'https://server14-c6551-default-rtdb.firebaseio.com', "keys": ['AIzaSyCt0gdzlqIxnuJH4TUzgEPJD3111w_qkBg']},
    {"tag": 'base (4).apk', "url": 'https://sb-rex-11-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyBa8wRzdVyXo-MSUMbnibj8qmoOor49uUY']},
    {"tag": 'base (40).apk', "url": 'https://panel-wala-v70-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'base (42).apk', "url": 'https://ruhr-4da8f-default-rtdb.firebaseio.com', "keys": ['AIzaSyCdKKxasC0wyxiW1f2qGOV3b24710-tNJ8']},
    {"tag": 'base (8) (1).apk', "url": 'https://jayma-9ce22-default-rtdb.firebaseio.com', "keys": ['AIzaSyANVndD8aVFoGpg-w5SbrHPGajrYv5wpzo', 'AIzaSyAVwTIwqW_oKyXZhaA6HFnNB7uD4k3U1tY', 'AIzaSyAW0ODNbYscLvfR8peqI7mEdf_16dArlws', 'AIzaSyBxSWK2jUvbczs57IgcRTSql_QQ6en_j7A', 'AIzaSyCDQ5Bu_0Ag4BRZXoIBQ1p2Sm9A1NTn2fY', 'AIzaSyCOcxmOCivdRSq4w-pYS44g2-P7HdYUyKE', 'AIzaSyCgEzi1tlT5gBTr8y9AsrDFtrlRbDJ2D4w']},
    {"tag": 'base (8) (1).apk', "url": 'https://annapunna-12b79-default-rtdb.firebaseio.com', "keys": ['AIzaSyANVndD8aVFoGpg-w5SbrHPGajrYv5wpzo', 'AIzaSyAVwTIwqW_oKyXZhaA6HFnNB7uD4k3U1tY', 'AIzaSyAW0ODNbYscLvfR8peqI7mEdf_16dArlws', 'AIzaSyBxSWK2jUvbczs57IgcRTSql_QQ6en_j7A', 'AIzaSyCDQ5Bu_0Ag4BRZXoIBQ1p2Sm9A1NTn2fY', 'AIzaSyCOcxmOCivdRSq4w-pYS44g2-P7HdYUyKE', 'AIzaSyCgEzi1tlT5gBTr8y9AsrDFtrlRbDJ2D4w']},
    {"tag": 'base (8) (1).apk', "url": 'https://newappi-7661a-default-rtdb.firebaseio.com', "keys": ['AIzaSyANVndD8aVFoGpg-w5SbrHPGajrYv5wpzo', 'AIzaSyAVwTIwqW_oKyXZhaA6HFnNB7uD4k3U1tY', 'AIzaSyAW0ODNbYscLvfR8peqI7mEdf_16dArlws', 'AIzaSyBxSWK2jUvbczs57IgcRTSql_QQ6en_j7A', 'AIzaSyCDQ5Bu_0Ag4BRZXoIBQ1p2Sm9A1NTn2fY', 'AIzaSyCOcxmOCivdRSq4w-pYS44g2-P7HdYUyKE', 'AIzaSyCgEzi1tlT5gBTr8y9AsrDFtrlRbDJ2D4w']},
    {"tag": 'base (8) (1).apk', "url": 'https://dwala-3d1ff-default-rtdb.firebaseio.com', "keys": ['AIzaSyANVndD8aVFoGpg-w5SbrHPGajrYv5wpzo', 'AIzaSyAVwTIwqW_oKyXZhaA6HFnNB7uD4k3U1tY', 'AIzaSyAW0ODNbYscLvfR8peqI7mEdf_16dArlws', 'AIzaSyBxSWK2jUvbczs57IgcRTSql_QQ6en_j7A', 'AIzaSyCDQ5Bu_0Ag4BRZXoIBQ1p2Sm9A1NTn2fY', 'AIzaSyCOcxmOCivdRSq4w-pYS44g2-P7HdYUyKE', 'AIzaSyCgEzi1tlT5gBTr8y9AsrDFtrlRbDJ2D4w']},
    {"tag": 'base (8) (1).apk', "url": 'https://pinkyrani-default-rtdb.firebaseio.com', "keys": ['AIzaSyANVndD8aVFoGpg-w5SbrHPGajrYv5wpzo', 'AIzaSyAVwTIwqW_oKyXZhaA6HFnNB7uD4k3U1tY', 'AIzaSyAW0ODNbYscLvfR8peqI7mEdf_16dArlws', 'AIzaSyBxSWK2jUvbczs57IgcRTSql_QQ6en_j7A', 'AIzaSyCDQ5Bu_0Ag4BRZXoIBQ1p2Sm9A1NTn2fY', 'AIzaSyCOcxmOCivdRSq4w-pYS44g2-P7HdYUyKE', 'AIzaSyCgEzi1tlT5gBTr8y9AsrDFtrlRbDJ2D4w']},
    {"tag": 'base (8) (1).apk', "url": 'https://komaljah-default-rtdb.firebaseio.com', "keys": ['AIzaSyANVndD8aVFoGpg-w5SbrHPGajrYv5wpzo', 'AIzaSyAVwTIwqW_oKyXZhaA6HFnNB7uD4k3U1tY', 'AIzaSyAW0ODNbYscLvfR8peqI7mEdf_16dArlws', 'AIzaSyBxSWK2jUvbczs57IgcRTSql_QQ6en_j7A', 'AIzaSyCDQ5Bu_0Ag4BRZXoIBQ1p2Sm9A1NTn2fY', 'AIzaSyCOcxmOCivdRSq4w-pYS44g2-P7HdYUyKE', 'AIzaSyCgEzi1tlT5gBTr8y9AsrDFtrlRbDJ2D4w']},
    {"tag": 'base (8) (1).apk', "url": 'https://binacallwalahe-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyANVndD8aVFoGpg-w5SbrHPGajrYv5wpzo', 'AIzaSyAVwTIwqW_oKyXZhaA6HFnNB7uD4k3U1tY', 'AIzaSyAW0ODNbYscLvfR8peqI7mEdf_16dArlws', 'AIzaSyBxSWK2jUvbczs57IgcRTSql_QQ6en_j7A', 'AIzaSyCDQ5Bu_0Ag4BRZXoIBQ1p2Sm9A1NTn2fY', 'AIzaSyCOcxmOCivdRSq4w-pYS44g2-P7HdYUyKE', 'AIzaSyCgEzi1tlT5gBTr8y9AsrDFtrlRbDJ2D4w']},
    {"tag": 'base-6-1 (3).apk', "url": 'https://panel-wala-v1-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": []},
    {"tag": 'base.apk', "url": 'https://chfjfj-c2857-default-rtdb.firebaseio.com', "keys": ['AIzaSyCAD1dGu5emRyr5YpnmLvwqffaK78hjAFI']},
    {"tag": 'business-apps-5aeb2', "url": 'https://business-apps-5aeb2-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'challan5', "url": 'https://challan5-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'chudaaai 9.apk', "url": 'https://dogla-de225-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://gren-ff2af-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://loda-5029e-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://mpari-6a6e5-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://comeback-5b876-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://strom-90e84-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://singhaana-6f199-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://flash-v7powerengine-v7-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://money-ace2c-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://vecna-82db2-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://rajputchuttad-default-rtdb.firebaseio.com', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://dadddy-ec5fa-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://nyawala-3e7c3-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://kashish-700f7-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAGUGYKDbUX1rFDhnk79dk3_XWIVxmXC-Y', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyCyjAOZ3D45nzWaBn9pzEkdBUVlbxhCfMQ', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://ridam-c7949-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://hack-boss-9de0f-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://anand-d7e61-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://farhan-565bc-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://jonny-9bb2a-default-rtdb.europe-west1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://proooh-672e6-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'chudaaai 9.apk', "url": 'https://apna26-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": ['AIzaSyA6iuCQxsY5W8tw-Hu0MF3ey0j3RSniOV8', 'AIzaSyAat3Ojk0hzugcjR9uG_O5ecJMcnXKmIz4', 'AIzaSyAbxb1hqTPl0qen2PGmnERgW7to9YNsqS0', 'AIzaSyAk5-ghEad9u6MDFGKdi8OdkbWyS86vwco', 'AIzaSyAu4Gv_EqcU0vjCO5DBoVeSu13-2RXSR0I', 'AIzaSyBE-mHLcDav5Bn4CTaPso3F7HX4tTy4wqo', 'AIzaSyBI1mOXLc6tgq7sh8aVJ7hsMqrwGo7gNlM', 'AIzaSyBVry2e7mc2VESO6HlJKMha8pMzyeweQTA', 'AIzaSyBcKb88YDDd9ffoDsH5EhiAJVw_ygY12pY', 'AIzaSyBgLptlqk-59Uk1RU3LXvMO4DUl_I7oVRs', 'AIzaSyC2J3ise8JYGXnaqbB6smr7dICCx0WHd9c', 'AIzaSyC4N_f3Md8cbt8rs-hdE89jOJ6Sn2t8RqM', 'AIzaSyC8d6kfctG23R2z77IcifG-dTo0rxFeO7Q', 'AIzaSyC9bjJf7jfHocW1cWTlPxgB2pbAuQ6hUuM', 'AIzaSyCFYKfIP4K2ge1PRHBu25mF1jIYDDZijKo', 'AIzaSyCGwbXI_jW-8tC9LJSlH4Pz8EMvFZQwueE', 'AIzaSyCJTE56lh43HKsWD8AJAyBS3lPes83mK9o', 'AIzaSyCT-cJzwhUszwCCRhHHwhULL7_JcV_1NFQ', 'AIzaSyCZm2iYTn4_l4ltIntVXz4WfT4zsvAsEN0', 'AIzaSyCxB48ZZla6mufEbYDXUH2p8c6w0Gdi_jk', 'AIzaSyD-1Gvt2cmr0mv1xoK4V9vtjVMXyJVLAvg', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'ckkumar', "url": 'https://ck-kumar3-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'colana', "url": 'https://colana-84ce2-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'csforme', "url": 'https://csforme-dc64a-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'dark18907', "url": 'https://dark-18907-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": []},
    {"tag": 'demon4', "url": 'https://demon-4-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'dhani', "url": 'https://dhani-aa151-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'fir-27c9e', "url": 'https://fir-27c9e-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'fir1', "url": 'https://fir-1fa16-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'fir408', "url": 'https://fir-408f9-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'fire8ad7', "url": 'https://fir-e8ad7-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'fires', "url": 'https://fires-847da-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'fixed AI RTO Admin 9 (1) (2).apk', "url": 'https://rto91-2b27f-default-rtdb.firebaseio.com', "keys": ['AIzaSyAgRUQgmgrRPIJohL5OTqc3tHg77bWtXcI']},
    {"tag": 'gaandkiaand', "url": 'https://gaandkiaand-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'gggggg', "url": 'https://gggggg-979bd-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'goone', "url": 'https://go-one-1b6b2-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'hdhdhdh', "url": 'https://hdhdhdh-38ae0-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'hehe', "url": 'https://hehe-679dd-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'hopkhfg', "url": 'https://hopkhfg-9981a-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'imdum', "url": 'https://imdum-6e873-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'indus', "url": 'https://indus-1-cec4f-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'jamtara118', "url": 'https://jamtara118-7cd20-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'jamtara150', "url": 'https://jamtara150-62b22-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'kisi', "url": 'https://kisi-d6da8-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'maik31440', "url": 'https://maik-31440-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'mano99', "url": 'https://mano99-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'manuwa', "url": 'https://manuwa-bb70a-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'mayor', "url": 'https://mayor-6f08c-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'merawala', "url": 'https://mera-wala-71a5e-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'mmmm', "url": 'https://mmmm-f7678-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'modi ji_1.0.apk', "url": 'https://duuu-dc41d-default-rtdb.firebaseio.com', "keys": ['AIzaSyANb5diLzfmtbkPbZk-UwG-ot4JZhjScsA', 'AIzaSyDQ_21hdN7h87WsRF4qUOVfqWYgI28r374']},
    {"tag": 'myabtar', "url": 'https://myabtar-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'newspreding', "url": 'https://newspreding-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'nyapanel.apk', "url": 'https://bossuun-default-rtdb.firebaseio.com', "keys": ['AIzaSyBfQobM5HmnK6khogyF4ytOX7E9N0e_lAQ', 'AIzaSyDbWz9viiCY6VnWHP0_-Wo6TWZwCwu7Meg']},
    {"tag": 'painislv', "url": 'https://painislv-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'panel123628', "url": 'https://panel123628-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'panel2', "url": 'https://lalannew5-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'panel3', "url": 'https://ajna-20fc4-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'panelwala64', "url": 'https://panel-wala-v64-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pawankumar', "url": 'https://pawankumar92342038-8f702-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pehla-green', "url": 'https://pehla-panel-green-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pintu', "url": 'https://pintu-8921f-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'piryankakumari', "url": 'https://piryankakumari1212c-9f29e-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pk114', "url": 'https://pk114-6e828-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'please', "url": 'https://please-2b091-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pm-kisan-20', "url": 'https://pm-kisan-20-vgg-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pm-kisan-28', "url": 'https://pm-kisan-28ugg-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pmfg', "url": 'https://pmfg-ccccc-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pmkisan', "url": 'https://pm-kisan-01hfg-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pmnr1newad', "url": 'https://pmnr1newad-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pmsjdj', "url": 'https://pmsjdj-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pohn', "url": 'https://pohn-cd7ea-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'prof', "url": 'https://prof-b6a64-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'project3', "url": 'https://project3-13fff-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'pvn7', "url": 'https://pvn7-a873a-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'r62710898', "url": 'https://r62710898-39a8e-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'rahul-54fe9', "url": 'https://rahul-54fe9-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'rahul-6bf55', "url": 'https://rahul-6bf55-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'rahulgandhi', "url": 'https://rahulgandhi-d09ca-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'rajkumar', "url": 'https://raj-kumar-63492-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'rajkumar8822556644', "url": 'https://rajkumar8822556644-407f5-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'raki143aa', "url": 'https://raki143aa-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'randi-rona', "url": 'https://randi-rona-81876-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'rbl7', "url": 'https://rbl-7-e796b-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'risho', "url": 'https://risho-d4c66-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'rnd12', "url": 'https://rnd12-17508-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'runjun', "url": 'https://runjun-master-panel-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 's85138920', "url": 'https://s85138920-87594-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'sbiclient0', "url": 'https://sbiclient0-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": []},
    {"tag": 'sep12', "url": 'https://sep12-aea6d-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'server2', "url": 'https://server-2-fb768-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'server97', "url": 'https://server-97e23-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'shhs', "url": 'https://shhs-8fe30-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'shootadminkitter', "url": 'https://shooot-admin-kitter-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'sirelech', "url": 'https://sirelech1-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'smsmms', "url": 'https://smsmms-3b08e-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'spy25', "url": 'https://spy-25-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'testing848', "url": 'https://testing-848ad-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'tillu2', "url": 'https://tillu-2-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'u40179853', "url": 'https://u40179853-987df-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'u67583339', "url": 'https://u67583339-bf0c1-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'ufff', "url": 'https://ufff-52c18-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'vdgdgd', "url": 'https://vdgdgd-80f1e-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'vibe', "url": 'https://vibe-d238e-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'virugoniya', "url": 'https://virugoniya-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'xc04', "url": 'https://xc04-52348-default-rtdb.firebaseio.com', "keys": []},
    {"tag": 'xx Admin 047 (3).apk', "url": 'https://rto-47-b39f4-default-rtdb.firebaseio.com', "keys": ['AIzaSyB9qxDIqS7FCqB-jSpqTAw9ipdf9OzIpho']},
    {"tag": 'yourfirebasio', "url": 'https://yourfirebasio-default-rtdb.asia-southeast1.firebasedatabase.app', "keys": []},
    {"tag": 'yqhwy', "url": 'https://yqhwy-2fb47-default-rtdb.firebaseio.com', "keys": []},
]


def fb_get(url, path, keys=None, timeout=8):
    """Firebase REST GET with optional auth-key fallback."""
    try:
        full = f"{url.rstrip('/')}/{path}.json"
        r = requests.get(full, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (401, 403) and keys:
            for k in keys:
                r2 = requests.get(f"{full}?auth={k}", timeout=timeout)
                if r2.status_code == 200:
                    return r2.json()
        return None
    except Exception:
        return None


def _extract_nums(sim):
    nums = []
    if isinstance(sim, dict):
        for key in ("sim1Number", "sim2Number", "numberSim1", "numberSim2",
                    "mobNo", "phoneNumber", "phone", "mobile"):
            v = re.sub(r"\D", "", str(sim.get(key, "")))
            if len(v) >= 10:
                nums.append(v[-10:])
    elif isinstance(sim, list):
        for item in sim:
            v = re.sub(r"\D", "", str(item))
            if len(v) >= 10:
                nums.append(v[-10:])
    return nums


def fb_discover_numbers(panel):
    """Return list of (device_id, number) pairs.

    Tries two structures:
      A) UJALA:  All_Users/simDetails  (keyed by device_id, holds SIM numbers)
      B) OTP panel: numbers/available  (keyed/list of numbers; device_id == number)
    """
    url = panel["url"]
    keys = panel.get("keys", [])
    out = []
    # Pattern A
    sim_all = fb_get(url, "All_Users/simDetails", keys)
    if isinstance(sim_all, dict):
        for dev_id, sim in sim_all.items():
            for n in set(_extract_nums(sim)):
                out.append((dev_id, n))
    if out:
        return out
    # Pattern B
    avail = fb_get(url, "numbers/available", keys)
    if isinstance(avail, dict):
        for k in avail.keys():
            n = re.sub(r"\D", "", str(k))
            if len(n) >= 10:
                out.append((n[-10:], n[-10:]))
    elif isinstance(avail, list):
        for k in avail:
            n = re.sub(r"\D", "", str(k))
            if len(n) >= 10:
                out.append((n[-10:], n[-10:]))
    return out


def fb_fetch_otp(panel, device_id, timeout=30):
    """Poll Firebase SMS for the Maccaron OTP message.

    Tries both All_Users/sms/{key} and sms/{key} (key = device_id or number)."""
    url = panel["url"]
    keys = panel.get("keys", [])
    candidates = [f"All_Users/sms/{device_id}", f"sms/{device_id}"]
    # prime 'existing' keys for each candidate
    existing = {c: set() for c in candidates}
    for c in candidates:
        try:
            init = fb_get(url, c, keys, timeout=5)
            if isinstance(init, dict):
                existing[c] = set(init.keys())
        except Exception:
            pass
    start = time.time()
    while time.time() - start < timeout:
        if AUTO_STOP_REQUESTED["v"]:
            return None
        for c in candidates:
            try:
                data = fb_get(url, c, keys, timeout=5)
                if not isinstance(data, dict):
                    continue
                for sms_key, sms_val in data.items():
                    if sms_key in existing[c]:
                        continue
                    if isinstance(sms_val, dict):
                        body = str(sms_val.get("body") or sms_val.get("message")
                                   or sms_val.get("text") or "")
                        otp = extract_maccaron_otp(body)
                        if otp:
                            return otp
                    existing[c].add(sms_key)
            except Exception:
                pass
        time.sleep(0.5)
    return None


def tg_send(chat_id, text):
    """Send a Telegram message from any thread (fire-and-forget)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception:
        pass


def auto_run(user_id, chat_id, referral_code, max_count=None):
    """Automation loop: for every device/number in Firebase panels,
    send Maccaron OTP -> fetch OTP from Firebase SMS -> verify+signup
    using the user's Maccaron referral code."""
    AUTO_RUNNING["v"] = True
    AUTO_STOP_REQUESTED["v"] = False
    total = ok = fail = 0
    tg_send(chat_id,
            f"🤖 <b>Automation Started</b>\nReferral code: <code>{referral_code}</code>\n"
            f"Panels loaded: {len(FIREBASE_PANELS)}\nSend /autostop to halt.")
    for panel in FIREBASE_PANELS:
        if AUTO_STOP_REQUESTED["v"]:
            break
        nums = fb_discover_numbers(panel)
        if not nums:
            continue
        tg_send(chat_id, f"📡 Panel <b>{panel['tag']}</b>: {len(nums)} number(s) found")
        for dev_id, number in nums:
            if AUTO_STOP_REQUESTED["v"]:
                break
            if max_count and total >= max_count:
                break
            total += 1
            res = do_send_otp(number, referral_code, user_id)
            if res.get("status") != "otp_sent":
                fail += 1
                continue
            otp = fb_fetch_otp(panel, dev_id, timeout=30)
            if not otp:
                fail += 1
                tg_send(chat_id, f"⏳ OTP not received for <code>{number}</code>")
                continue
            sign = do_verify_and_signup(number, referral_code, otp, user_id)
            if sign.get("status") == "success":
                ok += 1
                tg_send(chat_id, f"✅ <code>{number}</code> registered")
            else:
                fail += 1
                tg_send(chat_id, f"❌ <code>{number}</code>: {sign.get('reason', '?')}")
            time.sleep(1)
    AUTO_RUNNING["v"] = False
    AUTO_STOP_REQUESTED["v"] = False
    tg_send(chat_id,
            f"🏁 <b>Automation Finished</b>\nTotal: {total} | ✅ {ok} | ❌ {fail}")

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [green("📱 Register Number", cdata="action_register")],
        [blue("🎁 Referral Program", cdata="action_refer")],
        [grey("📊 My Stats", cdata="action_stats"), grey("📋 History", cdata="action_recent")],
        [grey("🔑 Change Maccaron Code", cdata="action_change_mc")],
        [grey("✏️ Change Link Code", cdata="action_change_pc")],
        [red("❓ Help & Guide", cdata="action_help")],
    ])


def back_button():
    return InlineKeyboardMarkup([[grey("🔙 Back to Menu", cdata="action_back_to_menu")]])


def admin_menu_keyboard():
    return InlineKeyboardMarkup([
        [blue("📊 Global Stats", cdata="admin_stats")],
        [green("📢 Broadcast", cdata="admin_broadcast")],
        [grey("👥 List Users", cdata="admin_listusers")],
        [red("🔄 Reset User", cdata="admin_resetuser")],
        [grey("🔙 Back to Main", cdata="admin_back")],
    ])


def back_or_skip(extra=None):
    rows = []
    if extra:
        rows.append(extra)
    rows.append([grey("🔙 Back to Menu", cdata="action_back_to_menu")])
    return InlineKeyboardMarkup(rows)


def captcha_keyboard(opts):
    row = []
    for i, opt in enumerate(opts):
        row.append(grey(str(opt), cdata=f"cap:{opt}"))
    kb = [row]
    kb.append([red("❌ Cancel", cdata="action_cancel_captcha")])
    return InlineKeyboardMarkup(kb)


# ════════════════════════════════════════════════════════════════════
#  REFERRAL LINK + ARG PARSING
# ════════════════════════════════════════════════════════════════════
def refer_link(user_id):
    code = get_user(user_id)["personal_code"]
    return f"https://t.me/{BOT_USERNAME}?start=ref_{code}"


def resolve_ref_arg(arg):
    """Deep-link arg se referrer user_id nikaalo. Backward-compat:
    ref_<digits> = user_id, warna personal code se lookup."""
    if not arg:
        return None
    a = str(arg).strip()
    for p in ("ref_", "REF_", "Ref_"):
        if a.startswith(p):
            a = a[len(p):]
            break
    if not a:
        return None
    if a.isdigit() and get_user(a):
        return str(a)
    u = get_user_by_code(a)
    return str(u["user_id"]) if u else None


# ════════════════════════════════════════════════════════════════════
#  HANDLERS
# ════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user is None:
        return ConversationHandler.END
    user = update.effective_user
    user_id = str(user.id)

    referrer_id = resolve_ref_arg(context.args[0] if context.args else None)

    existing = get_user(user_id)
    if existing is None:
        create_user(user_id, user.username or "", user.full_name or "", referred_by=referrer_id or "")
        if referrer_id:
            add_referral(referrer_id, user_id)

    update_user(user_id, username=user.username or "", full_name=user.full_name or "",
                last_active=time.time())

    # ADMIN: full access — skip channel join + captcha, go straight to menu
    if is_admin(user_id):
        update_user(user_id, channel_ok=1, captcha_ok=1)
        return await show_main_menu(update, context, user_id)

    # STEP 1 — forced channel join
    not_joined = await check_channel_joins(context.bot, user.id)
    if not_joined:
        names = "\n".join(f"• {ch['title']}" for ch in not_joined)
        await update.message.reply_text(
            "🔒 <b>STEP 1/4 — Join Required Channel</b>\n\n"
            f"You must join the following channel(s) to continue:\n{names}\n\n"
            "Join all of them, then tap <b>✅ I've Joined</b> so the bot can verify.",
            parse_mode="HTML",
            reply_markup=channel_join_keyboard(),
        )
        return CHANNEL_CHECK

    update_user(user_id, channel_ok=1)

    # STEP 2 — captcha gate (agar pehle se pass nahi)
    u = get_user(user_id)
    if not u["captcha_ok"]:
        return await show_captcha(update, context, user_id)

    # Already onboarded → main menu
    return await show_main_menu(update, context, user_id)


async def show_captcha(update, context, user_id, edit=False):
    u = get_user(user_id)
    if u and u["captcha_locked_until"] and time.time() < u["captcha_locked_until"]:
        mins = int((u["captcha_locked_until"] - time.time()) // 60) + 1
        text = f"🚫 <b>Too many wrong attempts!</b>\n\nTry again in <b>{mins} min</b>."
        if edit:
            await update.callback_query.edit_message_text(text, parse_mode="HTML")
        else:
            await update.message.reply_text(text, parse_mode="HTML")
        return ConversationHandler.END

    op, a, b, ans, opts = make_captcha()
    context.user_data["captcha"] = ans
    context.user_data["captcha_op"] = (op, a, b)
    text = (
        "🧮 <b>STEP 2/4 — Human Verification</b>\n\n"
        f"Please solve this simple math to prove you're human:\n\n"
        f"🔢 <b>{a} {op} {b} = ?</b>\n\n"
        "Tap the correct answer. (3 wrong attempts = 10 min lock)"
    )
    kb = captcha_keyboard(opts)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    return CAPTCHA


async def show_main_menu(update, context, user_id, edit=False):
    u = get_user(user_id)
    name = u["full_name"] or "there"
    text = (
        f"👋 <b>Welcome back, {name}!</b>\n\n"
        f"💳 <b>Points:</b> <code>{points_display(user_id)}</code>\n"
        f"🔑 <b>Maccaron Code:</b> <code>{u['maccaron_code'] or 'Not set'}</code>\n"
        f"👥 <b>Referrals:</b> {u['referral_count']}\n\n"
        "What would you like to do?"
    )
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="HTML",
                                                       reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="HTML",
                                        reply_markup=main_menu_keyboard())
    return PHONE


# ── channel check callback ──
async def handle_channel_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    not_joined = await check_channel_joins(context.bot, query.from_user.id)
    if not_joined:
        names = "\n".join(f"• {ch['title']}" for ch in not_joined)
        await query.edit_message_text(
            "🔒 <b>Still missing channels:</b>\n" + names + "\n\nJoin them and try again.",
            parse_mode="HTML",
            reply_markup=channel_join_keyboard(),
        )
        return CHANNEL_CHECK

    update_user(user_id, channel_ok=1)
    return await show_captcha(update, context, user_id, edit=True)


# ── captcha callback ──
async def handle_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    data = query.data

    if data == "action_cancel_captcha":
        await query.edit_message_text("❌ Captcha cancelled. Send /start to try again.")
        return ConversationHandler.END

    answer = context.user_data.get("captcha")
    if answer is None:
        await query.edit_message_text("⏳ Session expired. Send /start.", reply_markup=main_menu_keyboard())
        return PHONE

    try:
        chosen = int(data.split(":")[1])
    except Exception:
        await query.edit_message_text("❌ Invalid option. Send /start.")
        return ConversationHandler.END

    if chosen == answer:
        update_user(user_id, captcha_ok=1, captcha_fails=0)
        u = get_user(user_id)
        # referral credit (referee ke captcha complete = confirmed friend)
        referrer = credit_referral(user_id)
        if referrer:
            ref_u = get_user(referrer)
            try:
                await context.bot.send_message(
                    chat_id=int(referrer),
                    text=(
                        "🎉 <b>New Referral Confirmed!</b>\n\n"
                        "A friend joined via your link and completed verification.\n"
                        f"➕ <b>+{REFERRAL_POINTS} points</b> added.\n"
                        f"💳 Your balance: <b>{ref_u['points']} points</b>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Referral notify failed: {e}")
        # initial points sirf pehli baar (no maccaron code yet)
        if not u["maccaron_code"]:
            add_points(user_id, INITIAL_POINTS)

        if not u["maccaron_code"]:
            await query.edit_message_text(
                "✅ <b>Captcha passed! Human verified.</b>\n\n"
                f"🎁 You received <b>+{INITIAL_POINTS} free points</b>!\n\n"
                "Now <b>STEP 3/4</b> — enter your <b>Maccaron Referral Code</b>\n"
                "(the code used to register others under your account).",
                parse_mode="HTML",
                reply_markup=back_button(),
            )
            return REFERRAL

        await query.edit_message_text(
            "✅ <b>Captcha passed! Human verified.</b>\n\nAccess unlocked. 🎉",
            parse_mode="HTML",
        )
        return await show_main_menu(update, context, user_id, edit=True)
    else:
        fails = (get_user(user_id) or {}).get("captcha_fails", 0) + 1
        update_user(user_id, captcha_fails=fails)
        if fails >= CAPTCHA_MAX_FAILS:
            locked_until = time.time() + CAPTCHA_LOCK_SECONDS
            update_user(user_id, captcha_locked_until=locked_until)
            mins = CAPTCHA_LOCK_SECONDS // 60
            await query.edit_message_text(
                f"🚫 <b>Wrong answer!</b>\n\n{max(CAPTCHA_MAX_FAILS,0)} wrong attempts — locked for <b>{mins} min</b>.\n"
                "Send /start to try again later.",
                parse_mode="HTML",
            )
            return ConversationHandler.END
        return await show_captcha(update, context, user_id, edit=True)


# ── STEP 3: maccaron refer code (text) ──
async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    text = update.message.text.strip().upper()

    if not re.fullmatch(r"[A-Z0-9]{4,20}", text):
        await update.message.reply_text(
            "❌ Invalid code. Use only letters/digits, 4-20 characters.\n"
            "Enter your Maccaron Referral Code:",
            reply_markup=back_button(),
        )
        return REFERRAL

    update_user(user_id, maccaron_code=text)
    await update.message.reply_text(
        f"✅ <b>Maccaron Code saved:</b> <code>{text}</code>\n\n"
        "🎉 <b>Access unlocked!</b>\n\n"
        f"💳 <b>Your points:</b> {get_user(user_id)['points']}\n"
        f"🔗 <b>Your link:</b> <code>{refer_link(user_id)}</code>\n\n"
        "📱 Send a phone number to register, or use the menu.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    return PHONE


# ── STEP 4: phone ──
async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    text = update.message.text.strip().replace("+", "").replace(" ", "")

    if not text.isdigit() or len(text) < 10:
        await update.message.reply_text(
            "❌ Invalid phone. Send digits including country code.\n"
            "Example: 919876543210",
            reply_markup=back_button(),
        )
        return PHONE

    u = get_user(user_id)
    if not u["captcha_ok"]:
        await update.message.reply_text("🔒 Complete the captcha first. Send /start")
        return ConversationHandler.END

    ok, points = can_use(user_id)
    if not ok:
        await update.message.reply_text(
            "⛔ <b>No points left!</b>\n\n"
            "Each registration costs 1 point.\n"
            "Refer friends to earn more points 👇\n\n"
            f"🔗 <code>{refer_link(user_id)}</code>\n"
            f"Each friend (join + captcha) = <b>+{REFERRAL_POINTS} points</b>",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return PHONE

    if get_user_phone_status(user_id, text) == "success":
        await update.message.reply_text(
            f"⚠️ Phone <code>{text}</code> is already registered under your account.\n"
            "Try a different number.",
            parse_mode="HTML",
            reply_markup=back_button(),
        )
        return PHONE

    context.user_data["phone"] = text
    referral_code = u["maccaron_code"] or ""

    msg = await update.message.reply_text(
        f"📤 Sending OTP to <code>{text[-10:]}</code>...",
        parse_mode="HTML",
    )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(IO_POOL, do_send_otp, text, referral_code, user_id)

    if result["status"] != "otp_sent":
        await msg.edit_text(
            f"❌ Could not send OTP: {result.get('reason', 'Unknown')}\n\n"
            "Try a different number.",
            reply_markup=back_button(),
        )
        return PHONE

    await msg.edit_text(
        f"✅ OTP sent to <code>{text[-10:]}</code>!\n\n"
        "Enter the OTP you received (4 or 6 digits).\n"
        "Type <b>skip</b> to change the number.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [blue("⏩ Skip Number", cdata="action_skip_otp")],
            [grey("🔙 Back to Menu", cdata="action_back_to_menu")],
        ]),
    )
    return OTP


async def handle_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    if text.lower() == "skip":
        context.user_data.pop("phone", None)
        await update.message.reply_text(
            "⏩ Skipped. Send a new number:",
            reply_markup=main_menu_keyboard(),
        )
        return PHONE

    if not text.isdigit() or len(text) not in (4, 6):
        await update.message.reply_text(
            "❌ Invalid OTP. Must be 4 or 6 digits.\n"
            "Enter OTP, or type 'skip' to change the number:",
            reply_markup=back_button(),
        )
        return OTP

    phone = context.user_data.get("phone")
    u = get_user(user_id)
    if not phone:
        await update.message.reply_text("⚠️ Session expired. Send /start.")
        return ConversationHandler.END
    if not u or not u["captcha_ok"]:
        await update.message.reply_text("🔒 Captcha verification incomplete. Send /start")
        return ConversationHandler.END
    ok, _ = can_use(user_id)
    if not ok:
        await update.message.reply_text("⛔ Out of points. Refer friends to earn points.", parse_mode="HTML",
                                        reply_markup=main_menu_keyboard())
        return PHONE

    referral_code = u["maccaron_code"] or ""
    msg = await update.message.reply_text("⏳ Verifying OTP and signing up...")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(IO_POOL, do_verify_and_signup,
                                        phone, referral_code, text, user_id)

    if result["status"] == "success":
        consume_point(user_id)
        my_success = get_user_success_count(user_id)
        balance = get_user(user_id)["points"]
        await msg.edit_text(
            "🎉 <b>REGISTRATION SUCCESSFUL!</b>\n\n"
            f"📱 Phone: <code>{phone}</code>\n"
            f"👤 Name: {result.get('name', 'N/A')}\n"
            f"📧 Email: {result.get('email', 'N/A')}\n"
            f"🆔 Maccaron ID: {result.get('user_maccaron_id', 'N/A')}\n"
            f"🔑 Refer Code Used: <code>{referral_code}</code>\n\n"
            f"✅ Total registrations: <b>{my_success}</b>\n"
            f"💳 Points left: <b>{balance}</b>\n\n"
            "Send another number, or use the menu to refer friends!",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await msg.edit_text(
            "❌ <b>Registration failed</b>\n\n"
            f"📱 Phone: <code>{phone}</code>\n"
            f"Reason: {result.get('reason', 'Unknown')}\n\n"
            "Try a different number.",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )

    context.user_data.pop("phone", None)
    return PHONE


# ── change code handlers ──
async def handle_change_mc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    text = update.message.text.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4,20}", text):
        await update.message.reply_text("❌ Invalid code (4-20 letters/digits).", reply_markup=back_button())
        return CHANGE_MC
    update_user(user_id, maccaron_code=text)
    await update.message.reply_text(f"✅ Maccaron Code updated: <code>{text}</code>",
                                    parse_mode="HTML", reply_markup=main_menu_keyboard())
    return PHONE




async def handle_change_pc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    text = update.message.text.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{3,12}", text):
        await update.message.reply_text(
            "❌ Code must be 3-12 characters, letters/digits only.\nExample: VIEDBRO",
            reply_markup=back_button())
        return CHANGE_PC
    other = get_user_by_code(text)
    if other and other["user_id"] != user_id:
        await update.message.reply_text(
            "❌ This code is already taken by another user. Choose a new one:",
            reply_markup=back_button())
        return CHANGE_PC
    update_user(user_id, personal_code=text)
    await update.message.reply_text(
        f"✅ Your new referral link code: <code>{text}</code>\n\n"
        f"🔗 <b>{refer_link(user_id)}</b>\n\n"
        "Friends who join via this link will earn you points!",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )
    return PHONE


# ── global callback (menu) ──
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    data = query.data

    # captcha data handled separately but route here too (safety)
    if data.startswith("cap:"):
        return await handle_captcha(update, context)

    if data == "action_check_joined":
        return await handle_channel_check(update, context)

    if data == "action_back_to_start":
        await query.edit_message_text("🔙 Send /start to begin again.")
        return ConversationHandler.END

    if data == "action_cancel_captcha":
        await query.edit_message_text("❌ Cancelled. Send /start to try again.")
        return ConversationHandler.END

    if data == "action_back_to_menu":
        u = get_user(user_id)
        if not u or not u["captcha_ok"]:
            await query.edit_message_text("🔒 Please join the channel and complete captcha first. Send /start")
            return ConversationHandler.END
        return await show_main_menu(update, context, user_id, edit=True)

    if data == "action_skip_otp":
        context.user_data.pop("phone", None)
        await query.edit_message_text("⏩ Skipped. Send a new number:", reply_markup=main_menu_keyboard())
        return PHONE

    if data == "action_register":
        u = get_user(user_id)
        if not u or not u["captcha_ok"]:
            await query.edit_message_text("🔒 Complete channel join + captcha first via /start.")
            return ConversationHandler.END
        ok, points = can_use(user_id)
        if not ok:
            await query.edit_message_text(
                "⛔ <b>No points left!</b>\n\nRefer friends to earn:\n\n"
                f"🔗 <code>{refer_link(user_id)}</code>\n"
                f"Each friend = <b>+{REFERRAL_POINTS} points</b>",
                parse_mode="HTML", reply_markup=main_menu_keyboard())
            return PHONE
        await query.edit_message_text(
            f"📱 Send your phone number (with country code).\n"
            f"Example: 919876543210\n\n💳 Points: <b>{points}</b>",
            parse_mode="HTML", reply_markup=back_button())
        return PHONE

    if data == "action_refer":
        u = get_user(user_id)
        refs = get_referrals(user_id, limit=10)
        lines = [f"👥 {r['full_name'] or r['username'] or r['referred_id']}" for r in refs]
        ref_list = "\n".join(lines) if lines else "None yet"
        await query.edit_message_text(
            "<b>🎁 Referral Program</b>\n\n"
            f"🔗 <b>Your Link:</b>\n<code>{refer_link(user_id)}</code>\n\n"
            "<b>How it works:</b>\n"
            "• Friend opens your link and sends /start\n"
            "• They join the channel + solve captcha\n"
            f"• You get <b>+{REFERRAL_POINTS} points</b> (usable up to 10 times)\n\n"
            f"💳 Your points: <b>{points_display(user_id)}</b>\n"
            f"👥 Referrals: <b>{u['referral_count']}</b>\n\n"
            "📋 <b>Recent referrals:</b>\n" + ref_list,
            parse_mode="HTML", reply_markup=main_menu_keyboard())
        return PHONE

    if data == "action_stats":
        u = get_user(user_id)
        my_success = get_user_success_count(user_id)
        my_total, my_failed = get_user_total_processed(user_id)
        total_users, global_success, _ = get_global_stats()
        await query.edit_message_text(
            "<b>📊 Your Stats</b>\n\n"
            f"🔑 Maccaron Code: <code>{u['maccaron_code'] or 'Not set'}</code>\n"
            f"🔗 Refer Code: <code>{u['personal_code']}</code>\n"
            f"💳 Points: <b>{points_display(user_id)}</b>\n"
            f"✅ Registrations: <b>{my_success}</b>\n"
            f"🔄 Total attempts: {my_total}\n"
            f"❌ Failed: {my_failed}\n"
            f"👥 Referrals: {u['referral_count']}\n\n"
            "<b>🌍 Global</b>\n"
            f"Users: {total_users}\n"
            f"Total registrations: {global_success}",
            parse_mode="HTML", reply_markup=main_menu_keyboard())
        return PHONE

    if data == "action_recent":
        recent = get_user_recent_phones(user_id, limit=10)
        if not recent:
            await query.edit_message_text("📭 No numbers registered yet.",
                                          reply_markup=main_menu_keyboard())
            return PHONE
        lines = ["<b>📋 Recent Numbers</b>\n\n"]
        for ph, status in recent:
            icon = "✅" if status == "success" else "❌"
            lines.append(f"{icon} <code>{ph}</code> — {status}")
        lines.append("\n\nSend a new number or use the menu.")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML",
                                      reply_markup=main_menu_keyboard())
        return PHONE

    if data == "action_change_mc":
        await query.edit_message_text(
            "🔄 Type your new <b>Maccaron Referral Code</b>:",
            parse_mode="HTML", reply_markup=back_button())
        return CHANGE_MC

    if data == "action_change_pc":
        await query.edit_message_text(
            "✏️ Type your new <b>personal referral link code</b>\n"
            "(3-12 letters/digits, must be unique):",
            parse_mode="HTML", reply_markup=back_button())
        return CHANGE_PC

    if data == "action_help":
        await query.edit_message_text(
            "<b>❓ Help & Guide</b>\n\n"
            "<b>4-Step Flow:</b>\n"
            "1️⃣ Join required channel\n"
            "2️⃣ Solve math captcha\n"
            "3️⃣ Enter your Maccaron referral code\n"
            "4️⃣ Send phone -> OTP -> Signup\n\n"
            "<b>Points System:</b>\n"
            f"• New user = <b>+{INITIAL_POINTS}</b> free points\n"
            f"• Each referral (friend joins + captcha) = <b>+{REFERRAL_POINTS}</b>\n"
            f"• Each registration = <b>-{POINTS_PER_USE}</b> point\n\n"
            "<b>Commands:</b>\n"
            "/start — Main menu\n"
            "/stats — Your statistics\n"
            "/admin — Admin panel (admins only)",
            parse_mode="HTML", reply_markup=main_menu_keyboard())
        return PHONE

    # ── admin ──
    if data.startswith("admin_"):
        if str(user_id) not in [str(a) for a in ADMIN_IDS]:
            await query.edit_message_text("⛔ You are not an admin.")
            return PHONE
        return await handle_admin_callback(update, context)

    return PHONE


# ── admin ──
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    data = query.data
    user_id = str(query.from_user.id)

    if data == "admin_stats":
        total_users, global_success, total_phones = get_global_stats()
        await query.edit_message_text(
            "<b>📊 Global Statistics</b>\n\n"
            f"Total users: {total_users}\n"
            f"Total successful registrations: {global_success}\n"
            f"Total phone entries: {total_phones}",
            parse_mode="HTML", reply_markup=admin_menu_keyboard())
        return PHONE

    if data == "admin_broadcast":
        await query.edit_message_text(
            "📢 Type the broadcast message.\nSend /cancel to abort.",
            reply_markup=InlineKeyboardMarkup([[red("❌ Cancel", cdata="admin_back")]]))
        context.user_data["admin_broadcast"] = True
        return PHONE

    if data == "admin_listusers":
        users = get_all_users(limit=20)
        if not users:
            await query.edit_message_text("No users found.", reply_markup=admin_menu_keyboard())
            return PHONE
        text = "<b>👥 Users (last 20)</b>\n\n"
        for u in users:
            cap = "✅" if u["captcha_ok"] else "❌"
            text += (f"🆔 {u['user_id']}\n"
                     f"  Ref: {u['personal_code']} | MC: {u['maccaron_code'] or '-'}\n"
                     f"  Points: {u['points']} | RefC: {u['referral_count']} | Cap: {cap}\n")
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=admin_menu_keyboard())
        return PHONE

    if data == "admin_resetuser":
        await query.edit_message_text(
            "🔄 Enter the Telegram user ID to reset.",
            reply_markup=InlineKeyboardMarkup([[red("❌ Cancel", cdata="admin_back")]]))
        context.user_data["admin_reset"] = True
        return PHONE

    if data == "admin_back":
        u = get_user(user_id)
        if u and u["captcha_ok"]:
            await query.edit_message_text("🔙 Back to main menu.", reply_markup=main_menu_keyboard())
        else:
            await query.edit_message_text("🔙 Back.", reply_markup=admin_menu_keyboard())
        return PHONE

    if data == "admin_confirm_broadcast":
        return await admin_confirm_broadcast(update, context)

    return PHONE


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None:
        return
    if str(update.effective_user.id) not in [str(a) for a in ADMIN_IDS]:
        await update.message.reply_text("⛔ You are not an admin.")
        return
    await update.message.reply_text(
        "🛠 <b>Admin Panel</b>",
        parse_mode="HTML",
        reply_markup=admin_menu_keyboard(),
    )


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None:
        return
    user_id = str(update.effective_user.id)
    if user_id not in [str(a) for a in ADMIN_IDS]:
        return
    text = update.message.text

    if context.user_data.get("admin_broadcast"):
        if text.lower() == "/cancel":
            context.user_data.pop("admin_broadcast", None)
            await update.message.reply_text("❌ Broadcast cancelled.", reply_markup=main_menu_keyboard())
            return
        context.user_data["admin_broadcast_payload"] = text
        context.user_data["admin_broadcast"] = False
        await update.message.reply_text(
            f"📢 Broadcast:\n\n{text}\n\nConfirm?",
            reply_markup=InlineKeyboardMarkup([[green("✅ Confirm", cdata="admin_confirm_broadcast")]]))
        return

    if context.user_data.get("admin_reset"):
        if text.lower() == "/cancel":
            context.user_data.pop("admin_reset", None)
            await update.message.reply_text("❌ Reset cancelled.", reply_markup=main_menu_keyboard())
            return
        target = text.strip()
        if not target.isdigit():
            await update.message.reply_text("❌ Invalid ID. Send a numeric ID.")
            return
        reset_user_session(target)
        context.user_data.pop("admin_reset", None)
        await update.message.reply_text(f"✅ User {target} has been reset.", reply_markup=main_menu_keyboard())


async def admin_confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    payload = context.user_data.get("admin_broadcast_payload")
    if not payload:
        await query.edit_message_text("No broadcast message found.", reply_markup=admin_menu_keyboard())
        return
    with _db_lock:
        conn = _conn()
        try:
            users = conn.execute("SELECT user_id FROM users").fetchall()
        finally:
            conn.close()
    count = 0
    for row in users:
        try:
            await context.bot.send_message(chat_id=int(row["user_id"]), text=payload)
            count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Broadcast to {row['user_id']} failed: {e}")
    context.user_data.pop("admin_broadcast_payload", None)
    context.user_data.pop("admin_broadcast", None)
    await query.edit_message_text(f"✅ Broadcast sent to {count} users.", reply_markup=admin_menu_keyboard())


# ── other commands ──
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None:
        return
    user_id = str(update.effective_user.id)
    u = get_user(user_id)
    if not u:
        await update.message.reply_text("Send /start first.")
        return
    my_success = get_user_success_count(user_id)
    my_total, my_failed = get_user_total_processed(user_id)
    total_users, global_success, _ = get_global_stats()
    await update.message.reply_text(
        "<b>📊 Your Stats</b>\n\n"
        f"🔑 Maccaron Code: <code>{u['maccaron_code'] or 'Not set'}</code>\n"
        f"💳 Points: <b>{points_display(user_id)}</b>\n"
        f"✅ Registrations: <b>{my_success}</b>\n"
        f"🔄 Total attempts: {my_total}\n"
        f"❌ Failed: {my_failed}\n"
        f"👥 Referrals: {u['referral_count']}\n\n"
        "<b>🌍 Global</b>\n"
        f"Users: {total_users}\n"
        f"Total registrations: {global_success}",
        parse_mode="HTML",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.effective_user is not None:
        await update.message.reply_text("❌ Cancelled. Send /start.")
    return ConversationHandler.END


async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None:
        return
    user_id = str(update.effective_user.id)
    u = get_user(user_id)
    referral_code = (u or {}).get("maccaron_code") or ""
    if not referral_code:
        if is_admin(user_id):
            referral_code = DEFAULT_REFERRAL
        else:
            await update.message.reply_text(
                "⚠️ Set your <b>Maccaron Referral Code</b> first (menu -> Change Maccaron Code), "
                "then run /auto.\nThat code will be used for every auto-registration.",
                parse_mode="HTML",
            )
            return
    if not FIREBASE_PANELS:
        await update.message.reply_text(
            "⚠️ No Firebase panels loaded. Add <code>firebase_panels.json</code> next to the script.",
            parse_mode="HTML",
        )
        return
    if AUTO_RUNNING["v"]:
        await update.message.reply_text("⚠️ Automation is already running. Send /autostop to halt.")
        return

    # optional limit: /auto 50
    max_count = None
    if context.args:
        try:
            max_count = int(context.args[0])
        except ValueError:
            pass

    await update.message.reply_text(
        f"🤖 <b>Automation launching…</b>\n"
        f"Referral code: <code>{referral_code}</code>\n"
        f"Panels: {len(FIREBASE_PANELS)} | Limit: {max_count or 'all'}\n\n"
        f"For each device/number it will: send Maccaron OTP -> auto-fetch OTP from Firebase SMS -> signup.\n"
        f"Progress is reported here. Send /autostop to stop.",
        parse_mode="HTML",
    )
    chat_id = update.effective_chat.id
    threading.Thread(
        target=auto_run,
        args=(user_id, chat_id, referral_code, max_count),
        daemon=True,
    ).start()


async def cmd_autostop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AUTO_RUNNING["v"]:
        await update.message.reply_text("⚠️ No automation is currently running.")
        return
    AUTO_STOP_REQUESTED["v"] = True
    await update.message.reply_text("🛑 Stop requested. Automation will halt after the current number.")


async def post_init(application: Application) -> None:
    init_db()
    logger.info("Database initialized.")


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════
def main() -> None:
    # Python 3.14+ fix: set event loop for main thread
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    print("=" * 60)
    print("MACCARON REFERRAL BOT - STEP-BY-STEP EDITION")
    print("Channel gate -> Math captcha -> Refer code -> OTP signup")
    print(f"Initial points: {INITIAL_POINTS} | Referral: +{REFERRAL_POINTS} | Use: -{POINTS_PER_USE}")
    print(f"Bot username: @{BOT_USERNAME}")
    print("=" * 60)

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not found. Set the BOT_TOKEN env var or add it to token.txt")
        return

    # Railway health-check server (so Railway keeps the worker alive)
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        class _Health(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            def log_message(self, *a):
                pass

        port = int(os.getenv("PORT", "8080"))
        _hc = HTTPServer(("0.0.0.0", port), _Health)
        threading.Thread(target=_hc.serve_forever, daemon=True).start()
        print(f"Health-check server listening on port {port}")
    except Exception as e:
        logger.warning(f"Health-check server unavailable: {e}")

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            CHANNEL_CHECK: [CallbackQueryHandler(handle_callback)],
            CAPTCHA: [CallbackQueryHandler(handle_callback)],
            REFERRAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_referral),
                CallbackQueryHandler(handle_callback),
            ],
            PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone),
                CallbackQueryHandler(handle_callback),
            ],
            OTP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_otp),
                CallbackQueryHandler(handle_callback),
            ],
            CHANGE_MC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_change_mc),
                CallbackQueryHandler(handle_callback),
            ],
            CHANGE_PC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_change_pc),
                CallbackQueryHandler(handle_callback),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start", cmd_start),
        ],
        per_user=True,
        per_chat=True,
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("auto", cmd_auto))
    application.add_handler(CommandHandler("autostop", cmd_autostop))
    application.add_handler(CallbackQueryHandler(handle_callback))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            handle_admin_input,
        )
    )

    logger.info("Bot starting...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        IO_POOL.shutdown(wait=False)


if __name__ == "__main__":
    main()
