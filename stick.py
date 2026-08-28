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
DEFAULT_BATCH_CODE = "CD09G26"
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
    """Handle schema migration from old bot.db (had 'points' column) to new schema (uses_remaining)."""
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


def record_submission(user_id, user_key, batch_code):
    _run("INSERT INTO submissions (user_id, user_key, batch_code) VALUES (?,?,?)", (user_id, user_key, batch_code))


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
        return self._signed_request("users/getBatchCode/",
                                    {"batchCode": batch_code, "state": state}, user_key, data_key, access_token)

    def start_game(self, user_key: str, data_key: str, access_token: str = None):
        return self._signed_request("users/startGame/", {}, user_key, data_key, access_token)


# ── TELEGRAM BOT ─────────────────────────────────────────────────────────────

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

REGISTRATION_SESSIONS = {}
ADMIN_STATE = {}

# ── helpers ──────────────────────────────────────────────────────────────────


def btn(text, cb, style="primary"):
    return InlineKeyboardButton(text, callback_data=cb, style=style)


def is_admin(uid):
    return uid in ADMIN_IDS


def main_menu(uid=None):
    rows = [
        [btn("👤 Dashboard", "dashboard", "primary")],
        [btn("🎮 Start Registration", "start_reg", "success")],
        [btn("📊 My Stats", "my_stats", "primary")],
        [btn("🔗 Referral Link", "my_referral", "primary")],
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
    lines = ["**Join to continue using the bot**"]
    for ch in REQUIRED_CHANNELS:
        lines.append("- " + ch["title"] + ": " + ch["url"])
    lines.append("\nJoin both, then tap 'I've Joined'.")
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
        await q.edit_message_text("Welcome admin!", reply_markup=main_menu(uid))
        return
    ok, missing = await check_membership(ctx.bot, uid)
    if not ok:
        await q.answer("You haven't joined " + missing["title"] + " yet!", show_alert=True)
        return
    if not user_exists(uid):
        u = q.from_user
        create_user(uid, u.id, u.username, u.first_name)
        await q.edit_message_text("Verified! Welcome to the bot.\nTap Start Registration to begin.", reply_markup=main_menu(uid))
    else:
        await q.edit_message_text("Verified! Welcome back.", reply_markup=main_menu(uid))


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
                            f"New referral! {user.first_name or 'Someone'} joined via your link. +{REFERRAL_BONUS} uses added!")
                    except Exception:
                        pass
        except (ValueError, IndexError):
            pass
    if not user_exists(uid):
        create_user(uid, user.id, user.username, user.first_name)
        await update.message.reply_text(
            "Welcome " + (user.first_name or "") + "!\n\n"
            "Cremica Back to School Game Bot\n"
            "Complete registration and get Rs.20 reward!\n\n"
            "You have " + str(FREE_USES) + " free uses. Refer friends for more!",
            reply_markup=main_menu(uid))
    else:
        await update.message.reply_text("Welcome back " + (user.first_name or "") + "!", reply_markup=main_menu(uid))


async def dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, uid, q.from_user.username, q.from_user.first_name)
    u = get_user(uid)
    subs = count_submissions(uid)
    refs = count_referrals(uid)
    txt = (
        "**Your Dashboard**\n\n"
        "Name: " + (u["first_name"] or u["username"] or str(uid)) + "\n"
        "Uses Remaining: " + str(u["uses_remaining"]) + "\n"
        "Total Submissions: " + str(subs) + "\n"
        "Referrals: " + str(refs) + "\n"
        "Joined: " + u["created_at"] + "\n\n"
        "Each registration earns Rs.20 reward!\n"
        "Refer friends to get " + str(REFERRAL_BONUS) + " more uses."
    )
    kb = InlineKeyboardMarkup([
        [btn("Start Registration", "start_reg", "success")],
        [btn("Referral Link", "my_referral", "primary")],
        [btn("Menu", "back_menu", "danger")],
    ])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def my_stats_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user = get_user(uid)
    if not user:
        await q.edit_message_text("User not found.")
        return
    subs = count_submissions(uid)
    refs = count_referrals(uid)
    text = (
        "**Your Stats**\n\n"
        "Name: " + (user["first_name"] or "Not set") + "\n"
        "Uses Remaining: " + str(user["uses_remaining"]) + "\n"
        "Total Submissions: " + str(subs) + "\n"
        "Referrals: " + str(refs) + "\n"
        "Joined: " + user["created_at"]
    )
    await q.edit_message_text(text, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[btn("Menu", "back_menu", "danger")]]))


async def my_referral_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    refs = count_referrals(uid)
    bot_username = (await ctx.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{uid}"
    await q.edit_message_text(
        f"**Your Referral Link**\n\n`{ref_link}`\n\n"
        f"Share with friends!\n"
        f"Each referral gives +{REFERRAL_BONUS} uses.\n"
        f"Total referrals: {refs}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[btn("Menu", "back_menu", "danger")]]))


async def back_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    await q.edit_message_text("**Main Menu**", parse_mode="Markdown", reply_markup=main_menu(uid))


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
            [btn("Referral Link", "my_referral", "primary")],
            [btn("Menu", "back_menu", "danger")],
        ])
        await q.edit_message_text(
            f"No uses remaining!\nGet +{REFERRAL_BONUS} uses for each referral!",
            reply_markup=markup)
        return
    REGISTRATION_SESSIONS[uid] = {"state": "await_mobile"}
    await q.edit_message_text(
        "**Cremica Registration**\n\n"
        "Step 1/2: Enter your 10-digit mobile number:\n"
        "(Send /cancel to exit)",
        parse_mode="Markdown")


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    REGISTRATION_SESSIONS.pop(uid, None)
    await update.message.reply_text("Cancelled. /start for main menu.", reply_markup=main_menu(uid))


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
        "my_referral": my_referral_callback,
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
        await update.message.reply_text("Use the main menu buttons below:", reply_markup=main_menu(uid))
        return

    if s["state"] == "await_mobile":
        mobile = "".join(c for c in text if c.isdigit())
        if len(mobile) != 10:
            await update.message.reply_text("Enter a valid 10-digit mobile number.")
            return

        status_msg = await update.message.reply_text("Creating your session ...")
        api = CreamicaAPI()
        user_data = api.create_user()
        if not user_data:
            await status_msg.edit_text("Failed to create session. Try again later.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        user_key = user_data.get("userKey")
        data_key = user_data.get("dataKey")
        if not user_key or not data_key:
            await status_msg.edit_text("Invalid server response. Try again.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        s["user_key"] = user_key
        s["data_key"] = data_key
        s["mobile"] = mobile

        await status_msg.edit_text("Registering your details ...")
        user = get_user(uid)
        name = user["first_name"] or user["username"] or "User"
        reg = api.register(name=name, mobile=mobile, user_key=user_key, data_key=data_key)
        if not reg:
            await status_msg.edit_text("Registration failed. Try again later.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        s["state"] = "await_otp"
        await status_msg.edit_text(
            "OTP sent to +91" + mobile + "!\n\n"
            "Step 2/2: Enter the OTP you received:\n"
            "(Send /cancel to exit)")

    elif s["state"] == "await_otp":
        otp = "".join(c for c in text if c.isdigit())
        if len(otp) != 6:
            await update.message.reply_text("Enter a valid 6-digit OTP.")
            return

        status_msg = await update.message.reply_text("Verifying OTP ...")
        api = CreamicaAPI()
        user_key = s.get("user_key")
        data_key = s.get("data_key")

        verify = api.verify_otp(otp, user_key, data_key)
        if not verify or not verify.get("accessToken"):
            await status_msg.edit_text("Invalid OTP. Try again or send /cancel to abort.")
            return

        access_token = verify["accessToken"]
        await status_msg.edit_text("OTP verified! Submitting batch code ...")

        batch_code = DEFAULT_BATCH_CODE
        state = random.choice(VALID_STATES)

        batch = api.get_batch_code(batch_code, state, user_key, data_key, access_token)
        if not batch:
            await status_msg.edit_text("Batch code submission failed. Try again later.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        await status_msg.edit_text("Starting game ...")
        api.start_game(user_key, data_key, access_token)

        if not deduct_use(uid):
            await status_msg.edit_text("No uses remaining.")
            REGISTRATION_SESSIONS.pop(uid, None)
            return

        record_submission(uid, user_key, batch_code)

        user = get_user(uid)
        markup = InlineKeyboardMarkup([
            [btn("Redeem Now", url=REDEMPTION_URL)],
            [btn("Menu", "back_menu", "danger")],
        ])
        await status_msg.edit_text(
            "**Registration Complete!**\n\n"
            "Name: " + (user["first_name"] or "User") + "\n"
            "Mobile: " + s["mobile"] + "\n"
            "Batch Code: " + batch_code + "\n"
            "State: " + state + "\n\n"
            "Reward: Rs.20 (credited within 24 hours)\n"
            "Remaining uses: " + str(user["uses_remaining"]) + "\n\n"
            "Tap **Redeem Now** to claim your reward!",
            parse_mode="Markdown", reply_markup=markup)

        REGISTRATION_SESSIONS.pop(uid, None)


# ── Admin Commands ──────────────────────────────────────────────────────────


async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        await q.edit_message_text("Access denied.", reply_markup=main_menu(uid))
        return
    txt = (
        "**Admin Panel**\n\n"
        "Total users: " + str(total_users()) + "\n"
        "Total submissions: " + str(total_submissions()) + "\n"
        "Total referrals: " + str(total_referrals()) + "\n\n"
        "Commands:\n"
        "`/give [user_id] [uses]` - Add uses\n"
        "`/stats` - Detailed stats\n"
        "`/users` - List all users\n"
        "`/broadcast [msg]` - Send message to all users"
    )
    kb = InlineKeyboardMarkup([
        [btn("Stats", "admin_stats", "primary"), btn("All Users", "admin_users", "primary")],
        [btn("Broadcast", "admin_broadcast", "success")],
        [btn("Menu", "back_menu", "danger")],
    ])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    txt = (
        "**Bot Statistics**\n\n"
        "Total users: " + str(total_users()) + "\n"
        "Total submissions: " + str(total_submissions()) + "\n"
        "Total referrals: " + str(total_referrals()) + "\n"
        "Admins: " + str(ADMIN_IDS)
    )
    kb = InlineKeyboardMarkup([[btn("Back to Admin", "admin_panel", "danger")]])
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
    txt = "**Users (last 20)**\n\n" + "\n".join(lines)
    await q.edit_message_text(txt, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([[btn("Back to Admin", "admin_panel", "danger")]]))


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
