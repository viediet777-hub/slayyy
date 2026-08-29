import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import sqlite3
import string
import threading
import time
from hashlib import md5
from typing import Optional
from urllib.parse import urlencode

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN") or ""
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "1364476174").split(",") if x.strip()]
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db"))

REQUIRED_CHANNELS = [
    {"username": "viedietlooters", "url": "https://t.me/viedietlooters", "title": "Main Channel"},
    {"username": "viedietbackup", "url": "https://t.me/viedietbackup", "title": "Backup Group"},
]

CREMICA_BASE_URL = "https://cremicabacktoschool.woohoo.in"
MASTER_KEY = "1007327481"
UTM_SOURCE = "telegram_bot"
DEFAULT_BATCH_CODE = os.environ.get("BATCH_CODE") or "CD09G26"
# Multiple batch codes (comma separated) - agar ek fail ho toh agla try karega.
# Format bundle se: "e.g. MBC2024001" (Lot Number, 6+ chars, uppercase)
BATCH_CODES = [c.strip().upper() for c in os.environ.get("BATCH_CODES", DEFAULT_BATCH_CODE).split(",") if c.strip()]
FREE_USES = 10
REFERRAL_BONUS = 10
REDEMPTION_URL = "https://cremicabacktoschool.woohoo.in/redemption"

VALID_STATES = [
    "Punjab", "Uttar Pradesh", "Haryana", "Rajasthan", "Karnataka",
    "Himachal Pradesh", "Jammu and Kashmir", "Delhi", "Uttarakhand",
    "Bihar", "Maharashtra", "Madhya Pradesh", "Assam", "Kerala",
    "West Bengal", "Gujarat", "Telangana", "Ladakh", "Chandigarh",
    "Goa", "Mizoram", "Nagaland", "Andhra Pradesh", "Jharkhand",
    "Meghalaya", "Tripura", "Chhattisgarh", "Odisha", "Manipur",
    "Pondicherry", "Others",
]

INDIAN_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan",
    "Shaurya", "Atharv", "Advik", "Pranav", "Advaith", "Aarush", "Vihaan",
    "Ananya", "Diya", "Myra", "Sara", "Aanya", "Aadhya", "Aarohi", "Anvi",
    "Prisha", "Riya", "Kabir", "Arnav", "Dhruv", "Veer", "Ayaan", "Rudra",
    "Rohan", "Karan", "Nikhil", "Suresh", "Ramesh", "Mahesh", "Rajesh",
    "Priya", "Pooja", "Neha", "Sunita", "Geeta", "Suman", "Kavita", "Meena",
    "Rahul", "Amit", "Sanjay", "Manoj", "Pankaj", "Sunil", "Deepak", "Vikram",
    "Rajesh", "Sachin", "Alok", "Nitin", "Ashish", "Gaurav", "Manish", "Ankit",
    "Shreya", "Pallavi", "Sneha", "Divya", "Aarti", "Rekha", "Sapna", "Nisha",
]

# ── DB (thread-safe single connection) ──────────────────────────────────────

_db_lock = threading.Lock()
_db_conn = None


def _init_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA busy_timeout=30000")
        _db_conn.execute("PRAGMA synchronous=NORMAL")
        for ddl in (
            """CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY, user_id TEXT, username TEXT, first_name TEXT,
                uses_remaining INTEGER DEFAULT %d, total_uses INTEGER DEFAULT 0,
                referred_by INTEGER, referral_code TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now')), is_banned INTEGER DEFAULT 0)""" % FREE_USES,
            """CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                user_key TEXT, batch_code TEXT, status TEXT DEFAULT 'completed',
                reward TEXT DEFAULT 'Rs.20',
                created_at TEXT DEFAULT (datetime('now')))""",
            """CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER,
                referee_id INTEGER, created_at TEXT DEFAULT (datetime('now')))""",
            """CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, amount INTEGER,
                type TEXT, description TEXT, created_at TEXT DEFAULT (datetime('now')))""",
        ):
            _db_conn.execute(ddl)
        _db_conn.commit()
        _migrate_db()


def _migrate_db():
    """Handle schema migration from old bot.db."""
    try:
        cols = [r[1] for r in _db_conn.execute("PRAGMA table_info(users)").fetchall()]
        if "uses_remaining" not in cols:
            _db_conn.execute("ALTER TABLE users ADD COLUMN uses_remaining INTEGER DEFAULT " + str(FREE_USES))
        if "total_uses" not in cols:
            _db_conn.execute("ALTER TABLE users ADD COLUMN total_uses INTEGER DEFAULT 0")
        if "referred_by" not in cols and "referrer_id" not in cols:
            _db_conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "points" in cols:
            _db_conn.execute("UPDATE users SET uses_remaining = points WHERE uses_remaining = 0 AND points > 0")
        sub_cols = [r[1] for r in _db_conn.execute("PRAGMA table_info(submissions)").fetchall()]
        if "mobile" not in sub_cols:
            _db_conn.execute("ALTER TABLE submissions ADD COLUMN mobile TEXT")
        _db_conn.commit()
    except Exception as e:
        log.warning("migration note: %s", e)


def _one(sql, args=()):
    with _db_lock:
        _init_db()
        return _db_conn.execute(sql, args).fetchone()


def _all(sql, args=()):
    with _db_lock:
        _init_db()
        return _db_conn.execute(sql, args).fetchall()


def _run(sql, args=()):
    with _db_lock:
        _init_db()
        _db_conn.execute(sql, args)
        _db_conn.commit()


def _multi(statements):
    with _db_lock:
        _init_db()
        for sql, args in statements:
            _db_conn.execute(sql, args)
        _db_conn.commit()


def get_user(chat_id):
    return _one("SELECT * FROM users WHERE chat_id=?", (chat_id,))


def ensure_user(chat_id, user_id, username, first_name):
    u = get_user(chat_id)
    if u:
        return u
    code = md5(f"{chat_id}_{time.time()}".encode()).hexdigest()[:8]
    _run("INSERT OR IGNORE INTO users (chat_id, user_id, username, first_name, referral_code) VALUES (?,?,?,?,?)",
         (chat_id, str(user_id) or "", username or "", first_name or "", code))
    return get_user(chat_id)


def create_user(chat_id, user_id, username, first_name, referred_by=None):
    code = md5(f"{chat_id}_{time.time()}".encode()).hexdigest()[:8]
    _run("INSERT OR IGNORE INTO users (chat_id, user_id, username, first_name, referral_code, referred_by) VALUES (?,?,?,?,?,?)",
         (chat_id, str(user_id) or "", username or "", first_name or "", code, referred_by))
    return code


def deduct_use(chat_id):
    u = get_user(chat_id)
    if u and u["uses_remaining"] > 0:
        _run("UPDATE users SET uses_remaining = uses_remaining - 1, total_uses = total_uses + 1 WHERE chat_id=?", (chat_id,))
        return True
    return False


def add_uses(chat_id, amount):
    _run("UPDATE users SET uses_remaining = uses_remaining + ? WHERE chat_id=?", (amount, chat_id))


def record_submission(user_id, user_key, batch_code, mobile=""):
    _run("INSERT INTO submissions (user_id, user_key, batch_code, mobile) VALUES (?,?,?,?)",
         (user_id, user_key, batch_code, mobile))


def get_user_submissions(chat_id):
    return _all("SELECT * FROM submissions WHERE user_id=? ORDER BY created_at DESC", (chat_id,))


def count_submissions(chat_id):
    r = _one("SELECT COUNT(*) c FROM submissions WHERE user_id=?", (chat_id,))
    return r["c"] if r else 0


def count_referrals(chat_id):
    r = _one("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (chat_id,))
    return r["c"] if r else 0


def record_referral(referrer_id, referee_id):
    _run("INSERT INTO referrals (referrer_id, referee_id) VALUES (?,?)", (referrer_id, referee_id))


def total_users():
    r = _one("SELECT COUNT(*) c FROM users")
    return r["c"] if r else 0


def total_submissions():
    r = _one("SELECT COUNT(*) c FROM submissions")
    return r["c"] if r else 0


def total_referrals():
    r = _one("SELECT COUNT(*) c FROM referrals")
    return r["c"] if r else 0


def all_users():
    return _all("SELECT * FROM users ORDER BY created_at DESC")


def user_exists(chat_id):
    return _one("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)) is not None


# ── Creamica API ───────────────────────────────────────────────────────────


class CreamicaAPI:
    def __init__(self):
        self.base = CREMICA_BASE_URL
        self.http = requests.Session()
        self.http.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36",
        })

    def create_user(self) -> Optional[dict]:
        try:
            resp = self.http.post(f"{self.base}/api/users",
                                  json={"masterKey": MASTER_KEY, "utm_source": UTM_SOURCE}, timeout=15)
            if resp.ok:
                data = resp.json()
                if "resp" in data and isinstance(data["resp"], str):
                    return json.loads(base64.b64decode(data["resp"]).decode())
                return data
            log.warning("create_user failed: %s %s", resp.status_code, resp.text)
            return None
        except Exception as e:
            log.error("create_user error: %s", e)
            return None

    def _sign_payload(self, payload: dict, user_key: str, data_key: str) -> dict:
        payload["userKey"] = user_key
        payload["t"] = int(time.time() * 1000)
        json_str = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        s = base64.b64encode(json_str.encode()).decode()
        u = base64.b64encode(str(payload["t"]).encode()).decode()
        hmac_key = data_key[4:18]
        hmac_input = f"{u}.{s}"
        hmac_hex = hmac.new(hmac_key.encode(), hmac_input.encode(), hashlib.sha256).hexdigest()
        f = base64.b64encode(hmac_hex.encode()).decode()
        h_val = random.randint(1, 6)
        p_val = random.randint(2, 8)
        random_chars = string.ascii_letters + string.digits
        padding = "".join(random.choice(random_chars) for _ in range(p_val))
        data = f"{u}.{s}.{p_val}{h_val}{f[:h_val]}{padding}{f[h_val:]}"
        return {"userKey": user_key, "data": data}

    def _signed_request(self, endpoint: str, payload: dict, user_key: str, data_key: str,
                        access_token: str = None) -> Optional[dict]:
        try:
            signed = self._sign_payload(payload, user_key, data_key)
            headers = {"Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
            resp = self.http.post(f"{self.base}/api/{endpoint}{user_key}",
                                  data=urlencode(signed), headers=headers, timeout=15)
            if resp.ok:
                data = resp.json()
                if "resp" in data and isinstance(data["resp"], str):
                    return json.loads(base64.b64decode(data["resp"]).decode())
                return data
            log.warning("signed_request %s failed: %s %s", endpoint, resp.status_code, resp.text)
            return None
        except Exception as e:
            log.error("signed_request %s error: %s", endpoint, e)
            return None

    def register(self, name: str, mobile: str, user_key: str, data_key: str):
        return self._signed_request("users/register/", {"name": name, "mobile": mobile}, user_key, data_key)

    def verify_otp(self, otp: str, user_key: str, data_key: str) -> Optional[dict]:
        return self._signed_request("users/verifyOTP/", {"otp": otp}, user_key, data_key)

    def resend_otp(self, user_key: str, data_key: str):
        return self._signed_request("users/resendOtp/", {}, user_key, data_key)

    def get_batch_code(self, batch_code: str, state: str, user_key: str, data_key: str, access_token: str):
        """Submit batch code. Retries on transient failure. Returns dict or None."""
        last = None
        for attempt in range(3):
            last = self._signed_request("users/getBatchCode/",
                                        {"batchCode": batch_code, "state": state}, user_key, data_key, access_token)
            if last is not None:
                return last
            log.warning("get_batch_code attempt %d failed for %s", attempt + 1, batch_code)
            time.sleep(1)
        return last

    def start_game(self, user_key: str, data_key: str, access_token: str = None):
        return self._signed_request("users/startGame/", {}, user_key, data_key, access_token)

    def end_game(self, game_key: str, score: int, time_ms: int, key1: str, key2: str, key3: str,
                 user_key: str, data_key: str, access_token: str = None):
        """Submit game completion + score to users/endGame/ (score = 30 x coins)."""
        payload = {"gameKey": game_key, "score": score, "time": time_ms,
                   "key1": key1, "key2": key2, "key3": key3}
        return self._signed_request("users/endGame/", payload, user_key, data_key, access_token)


# ── CryptoJS-compatible AES (score encryption for endGame) ───────────────────
# Game score keys key1/key2/key3 = CryptoJS AES.encrypt(msg, gameKey) in
# OpenSSL "Salted__" format. Same as game bundle's uS()/dS().

def _evp_bytes_to_key(passphrase: bytes, salt: bytes, key_len=32, iv_len=16):
    d = b""
    prev = b""
    while len(d) < key_len + iv_len:
        prev = hashlib.md5(prev + passphrase + salt).digest()
        d += prev
    return d[:key_len], d[key_len:key_len + iv_len]


def crypto_js_encrypt(plain_text: str, passphrase: str) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    salt = os.urandom(8)
    key, iv = _evp_bytes_to_key(passphrase.encode(), salt)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ct = cipher.encrypt(pad(plain_text.encode(), AES.block_size))
    return ("Salted__" + salt.decode("latin1") + ct.decode("latin1")).encode("latin1").hex()


GAME_TARGET_SCORE = 1020   # 34 coins * 30 = 1020 (1000 se upar)
GAME_TIME_MS = 60000       # 60 sec game time


# ── TELEGRAM BOT ─────────────────────────────────────────────────────────────

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

REGISTRATION_SESSIONS = {}
ADMIN_STATE = {}

# ── helpers ──────────────────────────────────────────────────────────────────


def btn(text, cb=None, style="primary", url=None):
    if url:
        return InlineKeyboardButton(text, url=url)
    return InlineKeyboardButton(text, callback_data=cb, style=style)


def is_admin(uid):
    return uid in ADMIN_IDS


def main_menu(uid=None):
    rows = [
        [btn("👤 Dashboard", "dashboard", "primary"),
         btn("📊 My Stats", "my_stats", "primary")],
        [btn("🎮 Start Registration", "start_reg", "success")],
        [btn("📱 My Numbers", "my_numbers", "primary"),
         btn("🔗 Referral Link", "my_referral", "primary")],
        [btn("📖 Offer Info", "offer_info", "success")],
    ]
    if uid and is_admin(uid):
        rows.append([btn("🔧 Admin Panel", "admin_panel", "danger")])
    return InlineKeyboardMarkup(rows)


# ── force-join ───────────────────────────────────────────────────────────────

CHAT_IDS = {}


async def resolve_chat_ids(app):
    for ch in REQUIRED_CHANNELS:
        try:
            chat = await app.bot.get_chat("@" + ch["username"])
            CHAT_IDS[ch["username"]] = chat.id
            log.info("resolved %s -> %s", ch["username"], chat.id)
        except Exception as e:
            log.warning("could not resolve %s: %s", ch["username"], e)


async def check_membership(bot, user_id):
    for ch in REQUIRED_CHANNELS:
        chat_id = CHAT_IDS.get(ch["username"]) or ("@" + ch["username"])
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ("creator", "administrator", "member", "restricted"):
                return False, ch
        except Exception as e:
            log.warning("membership skip %s user %s: %s", ch["username"], user_id, e)
            continue
    return True, None


def force_join_kb():
    rows = [[InlineKeyboardButton("Join " + ch["title"], url=ch["url"])]
            for ch in REQUIRED_CHANNELS]
    rows.append([btn("I've Joined", "join_verified", "success")])
    return InlineKeyboardMarkup(rows)


def force_join_text():
    lines = ["🔒 **Join our channels to continue**\n"]
    for ch in REQUIRED_CHANNELS:
        lines.append("👉 " + ch["title"] + ": " + ch["url"])
    lines.append("\n✅ Join both, then tap 'I've Joined'.")
    return "\n".join(lines)


async def ensure_joined(update, ctx):
    uid = update.effective_user.id
    if is_admin(uid):
        return True
    ok, _ = await check_membership(ctx.bot, uid)
    if not ok:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(force_join_text(), reply_markup=force_join_kb())
        else:
            await update.message.reply_text(force_join_text(), reply_markup=force_join_kb())
        return False
    return True


async def join_verified(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if is_admin(uid):
        if not user_exists(uid):
            u = q.from_user
            create_user(uid, u.id, u.username, u.first_name)
        await q.edit_message_text("✅ Welcome admin!", reply_markup=main_menu(uid))
        return
    ok, missing = await check_membership(ctx.bot, uid)
    if not ok:
        await q.answer("❌ You haven't joined " + missing["title"] + " yet!", show_alert=True)
        return
    if not user_exists(uid):
        u = q.from_user
        create_user(uid, u.id, u.username, u.first_name)
        await q.edit_message_text(
            "✅ **Verified! Welcome to the bot!**\n\n"
            "🎮 Tap **Start Registration** to play the Cremica game.\n"
            "📖 Tap **Offer Info** to learn more.",
            parse_mode="Markdown", reply_markup=main_menu(uid))
    else:
        await q.edit_message_text("✅ **Verified! Welcome back!**", parse_mode="Markdown",
                                  reply_markup=main_menu(uid))


# ── handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = update.effective_user
    if not await ensure_joined(update, ctx):
        return
    args = ctx.args or []
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0].split("_")[1])
            if referrer_id != uid and not get_user(uid):
                referrer = get_user(referrer_id)
                if referrer:
                    create_user(uid, user.id, user.username, user.first_name, referred_by=referrer_id)
                    record_referral(referrer_id, uid)
                    add_uses(referrer_id, REFERRAL_BONUS)
                    try:
                        await ctx.bot.send_message(referrer_id,
                            f"🎉 New referral!\n\n{user.first_name or 'Someone'} joined via your link.\n+{REFERRAL_BONUS} uses added!")
                    except Exception:
                        pass
        except (ValueError, IndexError):
            pass
    if not user_exists(uid):
        create_user(uid, user.id, user.username, user.first_name)
        await update.message.reply_text(
            f"🎉 Welcome {user.first_name or ''}!\n\n"
            "🍼 **Cremica Back to School** Game Bot\n\n"
            "Participate in Cremica's Back to School campaign.\n"
            "Register with your number, complete the game,\n"
            "and get a chance to win **Rs.20 reward**!\n\n"
            f"🎁 You have **{FREE_USES} free uses**.\n"
            "Refer friends to get more uses!\n\n"
            "⏰ **Redemption time: 8:15 PM daily**\n"
            "Use the same number you registered with to redeem.",
            parse_mode="Markdown", reply_markup=main_menu(uid))
    else:
        await update.message.reply_text(
            f"👋 Welcome back, {user.first_name or ''}!\n\n"
            "🍼 **Cremica Back to School** Game Bot\n"
            "Tap 🎮 Start Registration to begin!",
            parse_mode="Markdown", reply_markup=main_menu(uid))


async def dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, uid, q.from_user.username, q.from_user.first_name)
    u = get_user(uid)
    subs = count_submissions(uid)
    refs = count_referrals(uid)
    txt = (
        f"👤 **Dashboard**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧑 Name: **{u['first_name'] or u['username'] or str(uid)}**\n"
        f"📱 Uses Left: **{u['uses_remaining']}**\n"
        f"✅ Games Played: **{subs}**\n"
        f"👥 Referrals: **{refs}**\n"
        f"📅 Joined: {u['created_at']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ **Redeem at 8:15 PM** with same number!\n"
        f"🔗 Refer friends for +{REFERRAL_BONUS} uses each."
    )
    kb = InlineKeyboardMarkup([
        [btn("🎮 Start Registration", "start_reg", "success")],
        [btn("📱 My Numbers", "my_numbers", "primary"),
         btn("🔗 Referral Link", "my_referral", "primary")],
        [btn("🔙 Menu", "back_menu", "danger")],
    ])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def my_stats_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user = get_user(uid)
    if not user:
        await q.edit_message_text("❌ User not found.")
        return
    subs = count_submissions(uid)
    refs = count_referrals(uid)
    text = (
        f"📊 **Your Stats**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧑 Name: **{user['first_name'] or 'Not set'}**\n"
        f"📱 Uses Left: **{user['uses_remaining']}**\n"
        f"✅ Games Played: **{subs}**\n"
        f"👥 Referrals: **{refs}**\n"
        f"📅 Joined: {user['created_at']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎁 Each game = Rs.20 reward chance!"
    )
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[btn("🔙 Menu", "back_menu", "danger")]]))


async def my_referral_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    refs = count_referrals(uid)
    bot_username = (await ctx.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    await q.edit_message_text(
        f"🔗 **Your Referral Link**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"`{ref_link}`\n\n"
        f"📤 Share with friends!\n"
        f"🎁 Each referral = **+{REFERRAL_BONUS} uses**\n"
        f"👥 Total referrals: **{refs}**\n\n"
        f"━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[btn("🔙 Menu", "back_menu", "danger")]]))


async def back_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    await q.edit_message_text(
        "🍼 **Cremica Back to School**\n\n"
        "Choose an option below:",
        parse_mode="Markdown", reply_markup=main_menu(uid))


async def my_numbers_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    subs = get_user_submissions(uid)
    if not subs:
        await q.edit_message_text(
            "📱 **My Numbers**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "You haven't registered any numbers yet!\n"
            "Tap 🎮 Start Registration to begin.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 Menu", "back_menu", "danger")]]))
        return
    lines = []
    for i, s in enumerate(subs, 1):
        mobile = s["mobile"] or "N/A"
        masked = mobile[:2] + "****" + mobile[-2:] if len(mobile) >= 6 else mobile
        lines.append(f"**{i}.** `+91{masked}` | {s['batch_code']} | {s['created_at']}")
    txt = (
        f"📱 **My Numbers** ({len(subs)} registered)\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n".join(lines) + "\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ Redeem at **8:15 PM** with same number!"
    )
    await q.edit_message_text(txt, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[btn("🔙 Menu", "back_menu", "danger")]]))


OFFER_INFO_TEXT = (
    "📖 **Cremica Back to School Offer**\n"
    "━━━━━━━━━━━━━━━━━━━\n\n"
    "🍼 **What is this?**\n"
    "Cremica's Back to School campaign gives you a chance\n"
    "to win **Rs.20 reward** by registering your product batch code\n"
    "and playing a simple game!\n\n"
    "📋 **How it works:**\n"
    "1. Tap 🎮 **Start Registration**\n"
    "2. Enter your 10-digit mobile number\n"
    "3. Enter the OTP received\n"
    "4. Bot auto-fills name, batch code & state\n"
    "5. Game completed!\n\n"
    "⏰ **Redemption:**\n"
    "Redeem your reward at **8:15 PM daily**\n"
    "Use the **same number** you registered with!\n"
    "Go to: https://cremicabacktoschool.woohoo.in/redemption\n\n"
    "🎁 **Rewards:**\n"
    "Each registration = Rs.20 reward chance\n"
    "Refer friends = +10 uses each!\n\n"
    "━━━━━━━━━━━━━━━━━━━\n"
    "💡 **Pro tip:** Use different numbers for more chances!"
)


async def offer_info_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    await q.edit_message_text(OFFER_INFO_TEXT, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([
                                  [btn("🎮 Start Registration", "start_reg", "success")],
                                  [btn("🔙 Menu", "back_menu", "danger")],
                              ]))


# ── Registration Flow ───────────────────────────────────────────────────────


async def start_reg_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not await ensure_joined(update, ctx):
        return
    user = get_user(uid)
    if not user:
        ensure_user(uid, uid, q.from_user.username, q.from_user.first_name)
        user = get_user(uid)
    if user["uses_remaining"] < 1:
        markup = InlineKeyboardMarkup([
            [btn("🔗 Referral Link", "my_referral", "primary")],
            [btn("🔙 Menu", "back_menu", "danger")],
        ])
        await q.edit_message_text(
            f"❌ **No uses remaining!**\n\n"
            f"🎁 Get +{REFERRAL_BONUS} uses for each referral!\n"
            f"Share your referral link to earn more.",
            parse_mode="Markdown", reply_markup=markup)
        return
    REGISTRATION_SESSIONS[uid] = {"state": "await_mobile"}
    await q.edit_message_text(
        "🎮 **Cremica Registration**\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        f"📱 Uses Left: **{user['uses_remaining']}**\n\n"
        "Step 1/2: Enter your 10-digit mobile number:\n"
        "(Send /cancel to exit)",
        parse_mode="Markdown")


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    REGISTRATION_SESSIONS.pop(uid, None)
    await update.message.reply_text("❌ Cancelled. Use /start for main menu.", reply_markup=main_menu(uid))


# ── callback router ──────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id

    await q.answer()

    if data == "join_verified":
        await join_verified(update, ctx)
        return

    admin_only = data in ("admin_panel", "admin_stats", "admin_users", "admin_broadcast", "admin_add_uses")
    if not admin_only and not is_admin(uid):
        ok, _ = await check_membership(ctx.bot, uid)
        if not ok:
            await q.edit_message_text(force_join_text(), reply_markup=force_join_kb())
            return

    handlers = {
        "dashboard": dashboard, "back_menu": back_menu, "admin_panel": admin_panel,
        "admin_stats": admin_stats, "admin_users": admin_users,
        "start_reg": start_reg_callback, "my_stats": my_stats_callback,
        "my_referral": my_referral_callback, "my_numbers": my_numbers_callback,
        "offer_info": offer_info_callback,
    }
    fn = handlers.get(data)
    if fn:
        try:
            await fn(update, ctx)
        except Exception as e:
            log.exception("handler error for %s", data)
            try:
                await q.edit_message_text("Something went wrong. Please try again.", reply_markup=main_menu(uid))
            except Exception:
                pass


# ── text handler (registration flow) ────────────────────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    uid = update.effective_chat.id
    text = (update.message.text or "").strip()

    if ADMIN_STATE.get(uid) == "await_broadcast":
        ADMIN_STATE.pop(uid, None)
        if not is_admin(uid):
            return
        if text.lower() in ("/cancel", "cancel"):
            await update.message.reply_text("Cancelled.", reply_markup=main_menu(uid))
            return
        rows = _all("SELECT chat_id FROM users")
        sent = 0
        failed = 0
        for row in rows:
            try:
                await ctx.bot.send_message(row["chat_id"], f"**Broadcast**\n\n{text}", parse_mode="Markdown")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        await update.message.reply_text(f"Broadcast sent to {sent} users. Failed: {failed}")
        return

    s = REGISTRATION_SESSIONS.get(uid)
    if not s:
        if not await ensure_joined(update, ctx):
            return
        await update.message.reply_text(
            "📌 Use the buttons below to navigate:",
            reply_markup=main_menu(uid))
        return

    if s["state"] == "await_mobile":
        mobile = "".join(c for c in text if c.isdigit())
        if len(mobile) != 10:
            await update.message.reply_text("⚠️ Enter a valid 10-digit mobile number.")
            return

        status_msg = await update.message.reply_text("⏳ Creating your session ...")
        api = CreamicaAPI()
        user_data = api.create_user()
        if not user_data:
            await status_msg.edit_text("❌ Failed to create session. Try again later.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        user_key = user_data.get("userKey")
        data_key = user_data.get("dataKey")
        if not user_key or not data_key:
            await status_msg.edit_text("❌ Invalid server response. Try again.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        s["user_key"] = user_key
        s["data_key"] = data_key
        s["mobile"] = mobile

        await status_msg.edit_text("📝 Registering your details ...")
        name = random.choice(INDIAN_NAMES)
        s["reg_name"] = name
        reg = api.register(name=name, mobile=mobile, user_key=user_key, data_key=data_key)
        if not reg:
            await status_msg.edit_text("❌ Registration failed. Try again later.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        s["state"] = "await_otp"
        await status_msg.edit_text(
            f"✅ OTP sent to +91{mobile}!\n\n"
            f"Step 2/2: Enter the OTP you received:\n"
            f"(Send /cancel to exit)")

    elif s["state"] == "await_otp":
        otp = "".join(c for c in text if c.isdigit())
        if len(otp) != 6:
            await update.message.reply_text("⚠️ Enter a valid 6-digit OTP.")
            return

        status_msg = await update.message.reply_text("🔐 Verifying OTP ...")
        api = CreamicaAPI()
        user_key = s.get("user_key")
        data_key = s.get("data_key")

        try:
            verify = api.verify_otp(otp, user_key, data_key)
            log.info("verify_otp response: %s", verify)
        except Exception as e:
            log.exception("verify_otp exception")
            await status_msg.edit_text("❌ OTP verification error: " + str(e))
            return

        if not verify or not verify.get("accessToken"):
            log.warning("verify_otp failed: %s", verify)
            await status_msg.edit_text("❌ Invalid OTP. Try again or send /cancel to abort.")
            return

        access_token = verify["accessToken"]
        await status_msg.edit_text("✅ OTP verified! Submitting batch code ...")

        state = random.choice(VALID_STATES)
        batch_result = None
        used_batch = None

        # Try each configured batch code until one works
        for bc in BATCH_CODES:
            try:
                r = api.get_batch_code(bc, state, user_key, data_key, access_token)
                log.info("get_batch_code %s response: %s", bc, r)
            except Exception as e:
                log.exception("get_batch_code exception for %s", bc)
                r = None
            if r:
                # success = HTTP ok; check decoded status if present
                if isinstance(r, dict) and r.get("statusCode") not in (None, 200):
                    log.warning("batch %s rejected: %s", bc, r)
                    continue
                batch_result = r
                used_batch = bc
                break
            log.warning("batch %s failed (None), next...", bc)

        if not batch_result:
            await status_msg.edit_text(
                f"❌ Batch code submission failed.\n"
                f"Tested: {', '.join(BATCH_CODES)}\n"
                f"Set BATCH_CODE/BATCH_CODES env with a valid lot number (format: MBC2024001).")
            REGISTRATION_SESSIONS.pop(uid, None)
            return
        batch_code = used_batch

        await status_msg.edit_text("🎮 Starting game ...")
        game = None
        try:
            game = api.start_game(user_key, data_key, access_token)
            log.info("start_game response: %s", game)
        except Exception as e:
            log.exception("start_game exception")

        # Submit game completion + score (proper game complete). Score = 30 x coins.
        game_key = ""
        if isinstance(game, dict):
            game_key = game.get("gameKey") or game.get("game_key") or ""
        score_ok = False
        if game_key:
            try:
                start_ms = int(time.time() * 1000) - GAME_TIME_MS
                metadata = {"t1": start_ms, "t2": [], "t3": GAME_TARGET_SCORE // 30}
                key1 = crypto_js_encrypt(str(GAME_TARGET_SCORE), game_key)
                key2 = crypto_js_encrypt(str(GAME_TIME_MS), game_key)
                key3 = crypto_js_encrypt(json.dumps(metadata), game_key)
                e_resp = api.end_game(game_key, GAME_TARGET_SCORE, GAME_TIME_MS,
                                      key1, key2, key3, user_key, data_key, access_token)
                log.info("end_game response: %s", e_resp)
                score_ok = e_resp is not None
            except Exception as e:
                log.exception("end_game exception")
        else:
            log.warning("No gameKey from start_game - endGame skip")
        log.info("game score target=%s submitted=%s", GAME_TARGET_SCORE, score_ok)

        if not deduct_use(uid):
            await status_msg.edit_text("❌ No uses remaining.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        record_submission(uid, user_key, batch_code, s["mobile"])

        user = get_user(uid)
        reg_name = s.get("reg_name", "User")
        markup = InlineKeyboardMarkup([
            [btn("🔗 Redeem at 8:15 PM", url=REDEMPTION_URL)],
            [btn("📱 My Numbers", "my_numbers", "primary")],
            [btn("🔙 Menu", "back_menu", "danger")],
        ])
        await status_msg.edit_text(
            f"🎉 **GAME COMPLETED!** 🎉\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"🧑 Name: **{reg_name}**\n"
            f"📱 Mobile: `+91{s['mobile']}`\n"
            f"📦 Batch Code: `{batch_code}`\n"
            f"🎯 Score: **{GAME_TARGET_SCORE}** ({'✅ submitted' if score_ok else '⚠️ not submitted'})\n"
            f"📍 State: **{state}**\n\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ **Redeem at 8:15 PM** with same number!\n"
            f"🔗 Go to redemption page & enter your number.\n\n"
            f"📱 Uses Left: **{user['uses_remaining']}**\n"
            f"━━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown", reply_markup=markup)

        REGISTRATION_SESSIONS.pop(uid, None)


# ── Admin Commands ──────────────────────────────────────────────────────────


async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        await q.edit_message_text("⛔ Access denied.", reply_markup=main_menu(uid))
        return
    txt = (
        f"🔧 **Admin Panel**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Total users: **{total_users()}**\n"
        f"✅ Total submissions: **{total_submissions()}**\n"
        f"🔗 Total referrals: **{total_referrals()}**\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **Commands:**\n"
        f"`/give [user_id] [uses]` - Add uses\n"
        f"`/stats` - Detailed stats\n"
        f"`/users` - List all users\n"
        f"`/broadcast [msg]` - Send to all users"
    )
    kb = InlineKeyboardMarkup([
        [btn("📊 Stats", "admin_stats", "primary"),
         btn("👥 Users", "admin_users", "primary")],
        [btn("📢 Broadcast", "admin_broadcast", "success")],
        [btn("🔙 Menu", "back_menu", "danger")],
    ])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    txt = (
        f"📊 **Bot Statistics**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Total users: **{total_users()}**\n"
        f"✅ Total submissions: **{total_submissions()}**\n"
        f"🔗 Total referrals: **{total_referrals()}**\n"
        f"🆔 Admins: `{ADMIN_IDS}`"
    )
    kb = InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    us = all_users()
    lines = []
    for u in us[:20]:
        lines.append(f"`{u['chat_id']}` | {u['first_name'] or u['username'] or '?'} | uses: `{u['uses_remaining']}`")
    txt = "👥 **Users (last 20)**\n\n" + "\n".join(lines)
    await q.edit_message_text(txt, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]]))


async def admin_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    msg = " ".join(ctx.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    rows = _all("SELECT chat_id FROM users")
    sent = 0
    failed = 0
    for row in rows:
        try:
            await ctx.bot.send_message(row["chat_id"], f"**Broadcast**\n\n{msg}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(f"Broadcast sent to {sent} users. Failed: {failed}")


async def handle_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("Only admins can use this command.")
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/give [user_id] [uses]`", parse_mode="Markdown")
        return
    try:
        target = int(args[0])
        amount = int(args[1])
    except ValueError:
        await update.message.reply_text("Invalid user_id or amount.")
        return
    if not user_exists(target):
        await update.message.reply_text("User `" + str(target) + "` not found.", parse_mode="Markdown")
        return
    add_uses(target, amount)
    await update.message.reply_text(f"Added {amount} uses to user `{target}`.", parse_mode="Markdown")


async def handle_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    txt = (
        "**Bot Statistics**\n\n"
        "Total users: " + str(total_users()) + "\n"
        "Total submissions: " + str(total_submissions()) + "\n"
        "Total referrals: " + str(total_referrals()) + "\n"
        "Admins: " + str(ADMIN_IDS)
    )
    await update.message.reply_text(txt, parse_mode="Markdown")


async def handle_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    us = all_users()
    lines = []
    for u in us:
        lines.append(f"`{u['chat_id']}` | {u['first_name'] or u['username'] or '?'} | uses: `{u['uses_remaining']}` | refs: `{count_referrals(u['chat_id'])}`")
    chunk = "\n".join(lines)
    for i in range(0, len(chunk), 4000):
        await update.message.reply_text(chunk[i:i + 4000], parse_mode="Markdown")


async def error_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log.warning("handler error: %s", ctx.error)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    async def _post_init(app_):
        await resolve_chat_ids(app_)
    app.post_init = _post_init

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("give", handle_give))
    app.add_handler(CommandHandler("stats", handle_stats))
    app.add_handler(CommandHandler("users", handle_users))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot started (admin_ids=%s)", ADMIN_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
