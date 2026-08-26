import base64
import json
import logging
import os
import re
import sqlite3
import time
from hashlib import md5

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "import base64
import json
import logging
import os
import re
import sqlite3
import time
from hashlib import md5

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

# ── DB ───────────────────────────────────────────────────────────────────────

# ── DB (thread-safe single connection) ──────────────────────────────────────

import threading

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
                points INTEGER DEFAULT 0, referral_code TEXT UNIQUE, referred_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')), is_banned INTEGER DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referee_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')))""",
            """CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, amount INTEGER,
                type TEXT, description TEXT, created_at TEXT DEFAULT (datetime('now')))""",
            """CREATE TABLE IF NOT EXISTS offer_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT, link TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now')))""",
        ):
            _db_conn.execute(ddl)
        _db_conn.commit()


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
    """Run several statements atomically under one lock."""
    with _db_lock:
        _init_db()
        for sql, args in statements:
            _db_conn.execute(sql, args)
        _db_conn.commit()


def user_exists(chat_id):
    return _one("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)) is not None


def get_user(chat_id):
    return _one("SELECT * FROM users WHERE chat_id=?", (chat_id,))


def ensure_user(chat_id, user_id, username, first_name):
    u = get_user(chat_id)
    if u:
        return u
    code = md5(f"{chat_id}_{time.time()}".encode()).hexdigest()[:8]
    _run("""INSERT OR IGNORE INTO users (chat_id, user_id, username, first_name, referral_code)
        VALUES (?,?,?,?,?)""", (chat_id, str(user_id) or "", username or "", first_name or "", code))
    return get_user(chat_id)


def create_user(chat_id, user_id, username, first_name, referred_by=None):
    code = md5(f"{chat_id}_{time.time()}".encode()).hexdigest()[:8]
    _run("""INSERT OR IGNORE INTO users (chat_id, user_id, username, first_name, referral_code, referred_by)
        VALUES (?,?,?,?,?,?)""", (chat_id, str(user_id) or "", username or "", first_name or "", code, referred_by))
    return code


def add_points(chat_id, amount, typ, desc):
    _multi([
        ("UPDATE users SET points = points + ? WHERE chat_id=?", (amount, chat_id)),
        ("INSERT INTO transactions (chat_id, amount, type, description) VALUES (?,?,?,?)",
         (chat_id, amount, typ, desc)),
    ])


def get_points(chat_id):
    r = _one("SELECT points FROM users WHERE chat_id=?", (chat_id,))
    return r["points"] if r else 0


def get_leaderboard(limit=10):
    return _all("SELECT chat_id, username, first_name, points FROM users ORDER BY points DESC LIMIT ?", (limit,))


def count_referrals(chat_id):
    r = _one("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (chat_id,))
    return r["c"] if r else 0


def record_referral(referrer_id, referee_id):
    _run("INSERT INTO referrals (referrer_id, referee_id) VALUES (?,?)", (referrer_id, referee_id))


def all_users():
    return _all("SELECT * FROM users ORDER BY created_at DESC")


def total_users():
    r = _one("SELECT COUNT(*) c FROM users")
    return r["c"] if r else 0


def total_points():
    r = _one("SELECT COALESCE(SUM(points),0) s FROM users")
    return r["s"] if r else 0


def total_referrals():
    r = _one("SELECT COUNT(*) c FROM referrals")
    return r["c"] if r else 0


# ── offer links (admin-managed, stored in DB) ───────────────────────────────

def get_offer_links(limit=10):
    """Return stored links (up to limit); falls back to default OFFER_LINKS."""
    rows = _all("SELECT link FROM offer_links ORDER BY id LIMIT ?", (limit,))
    links = [r["link"] for r in rows]
    return links if links else OFFER_LINKS


def add_offer_links(text):
    """Parse bulk links (one per line / comma / space), dedupe, insert.
    Returns (added_count, skipped_duplicates, stored_total)."""
    raw = re.split(r"[\s,]+", text.strip())
    links = []
    seen = set()
    skipped = 0
    for l in raw:
        l = l.strip().strip('"').strip("'")
        if not l or not l.lower().startswith("http"):
            continue
        if l in seen:
            skipped += 1
            continue
        seen.add(l)
        links.append(l)
    added = 0
    with _db_lock:
        _init_db()
        for l in links:
            cur = _db_conn.execute("INSERT OR IGNORE INTO offer_links (link) VALUES (?)", (l,))
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
        _db_conn.commit()
    stored = _one("SELECT COUNT(*) c FROM offer_links")["c"]
    return added, skipped, stored


def clear_offer_links():
    _run("DELETE FROM offer_links")


# ── SWIGGY API ───────────────────────────────────────────────────────────────

CAMPAIGN_HOST = "https://disc.swiggy.com"
CAMPAIGN_ID = "rakhi_wars"
CAMPAIGN_DETAILS = "/api/v1/campaign/details"
CAMPAIGN_SUBMIT = "/api/v1/campaign/submit"
CAMPAIGN_POLL = "/api/v1/campaign/poll"

SMS_OTP_URL = "https://profile.swiggy.com/api/v3/app/sms_otp"
VERIFY_URL = "https://profile.swiggy.com/api/v3/app/login/verify"
DEVICE_ID = "9b69ea5de1ef3f99"

OFFER_LINKS = [
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-1adhdtaCdQ",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-MZxgRHuBN",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-2Out6vNL00",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-3RvWb1Aj0IL",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-8OkuWB2dr",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-8OkuF7L1h",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-1jfCJc8KB6",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-1jfCJxsjBk",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-9ggQmy8CP",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-2SmfgnlXXd",
]

LOGIN_HEADERS = {
    "Accept": "application/json; charset=utf-8", "app-version": "4.106.1", "category": "food",
    "deviceId": DEVICE_ID, "swuid": DEVICE_ID, "os-version": "9", "pl-version": "131",
    "User-Agent": "Swiggy-Android", "version-code": "1716", "x-channel": "swiggy",
}

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 4 Build/RD2A.211001.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.120 Mobile Safari/537.36",
    "platform": "Swiggy-Android", "version-code": "1795", "model-name": "PIXEL 4",
    "manufacturer": "GOOGLE", "application_name": "swiggy-app", "appversion": "4.113.0",
    "cityid": "18", "lat": "22.74215", "lng": "75.9078633",
    "x-requested-with": "in.swiggy.android", "x-theme": "base",
    "accept-language": "en-IN,en-US;q=0.9,en;q=0.8",
}


class SwiggyError(Exception):
    pass


def _checked(r):
    try:
        d = r.json()
    except Exception:
        d = None
    if d is None:
        raise SwiggyError(f"non-JSON HTTP {r.status_code}")
    st = d.get("statusCode")
    if st == 999:
        raise SwiggyError(f"session expired: {d.get('statusMessage')}")
    if isinstance(st, int) and st != 0:
        raise SwiggyError(f"api error {st}: {d.get('statusMessage')}")
    return d


def send_otp(mobile10):
    return _checked(requests.get(f"{SMS_OTP_URL}?mobile={mobile10}", headers=LOGIN_HEADERS, timeout=30))


def verify_otp(mobile10, otp, tid, sid):
    h = {**LOGIN_HEADERS, "sid": sid, "Tid": tid, "Content-Type": "application/json; charset=utf-8"}
    p = {"cloningSignalsData": {"appFilesDirPathInvalid": 0, "developerModeEnabled": 1,
                                "deviceModelVmos": 0, "emulatorStatus": 0,
                                "packageName": "in.swiggy.android", "workProfileEnabled": 0},
         "otp": otp}
    return _checked(requests.post(f"{VERIFY_URL}?otp_source=Sms-automatic", headers=h, json=p, timeout=30))


def _jwt_payload(token):
    try:
        part = token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def build_session(v):
    tid = v.get("tid", "")
    sid = v.get("sid", "")
    userid = _jwt_payload(tid).get("user_id") or ""
    token = ""
    for k in ("token", "accessToken", "refreshToken"):
        if (v.get("data") or {}).get(k):
            token = (v.get("data") or {})[k]
            break
    return {"userid": userid, "tid": tid, "sid": sid, "token": token}


def build_session_from_json(obj):
    tid = obj.get("tid") or ""
    sid = obj.get("sid") or ""
    if "data" in obj and tid:
        return build_session(obj)
    userid = str(obj.get("userid") or obj.get("userId") or obj.get("user_id") or "")
    token = obj.get("token") or ""
    if not userid and tid:
        userid = str(_jwt_payload(tid).get("user_id") or "")
    return {"userid": userid, "tid": tid, "sid": sid, "token": token}


def _sh(session, referral="", **extra):
    h = dict(BASE_HEADERS)
    h.update(session)
    h["campaignId"] = CAMPAIGN_ID
    if referral:
        h["referral-code"] = referral
    h.update(extra)
    return h


def campaign_details(session, r):
    return _checked(requests.get(CAMPAIGN_HOST + CAMPAIGN_DETAILS, headers=_sh(session, r), timeout=30))


def campaign_submit(session, a, t, r=""):
    return _checked(requests.post(CAMPAIGN_HOST + CAMPAIGN_SUBMIT,
                                  headers=_sh(session, r, **{"Content-Type": "application/json"}),
                                  json={"action": a, "teamSelected": t, "campaignId": CAMPAIGN_ID}, timeout=30))


def campaign_poll(session, r=""):
    return _checked(requests.get(CAMPAIGN_HOST + CAMPAIGN_POLL, headers=_sh(session, r), timeout=30))


def run_one(session, ref, team=1):
    try:
        campaign_details(session, ref)
        campaign_submit(session, "JOIN_MATCH", team, ref)
        for _ in range(5):
            atk = campaign_submit(session, "ATTACK", team, ref)
            if (atk.get("data") or {}).get("userState", {}).get("attacksLeft", 0) <= 0:
                break
            time.sleep(1)
        campaign_poll(session, ref)
        return (True, "")
    except SwiggyError as e:
        return (False, str(e))


# ── TELEGRAM BOT ─────────────────────────────────────────────────────────────

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

LOGIN_SESSIONS = {}
ADMIN_STATE = {}

# ── helpers ──────────────────────────────────────────────────────────────────

def btn(text, cb, style="primary"):
    return InlineKeyboardButton(text, callback_data=cb, style=style)


def is_admin(uid):
    return uid in ADMIN_IDS


def main_menu(uid=None):
    rows = [
        [btn("👤 My Balance", "balance", "primary")],
        [btn("👥 Invite Friends", "invite", "success"),
         btn("🏆 Leaderboard", "leaderboard", "primary")],
        [btn("⚡ Rakhi Offer", "rakhi_offer", "danger")],
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
    rows = [[InlineKeyboardButton("🔗 Join " + ch["title"], url=ch["url"])]
            for ch in REQUIRED_CHANNELS]
    rows.append([btn("✅ I've Joined", "join_verified", "success")])
    return InlineKeyboardMarkup(rows)


def force_join_text():
    lines = ["🔒 **Join to continue using the bot**"]
    for ch in REQUIRED_CHANNELS:
        lines.append("• " + ch["title"] + ": " + ch["url"])
    lines.append("\nJoin both, then tap '✅ I've Joined'.")
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
            add_points(uid, 2, "signup", "New user bonus")
        await q.edit_message_text("✅ Welcome admin!", reply_markup=main_menu(uid))
        return
    ok, missing = await check_membership(ctx.bot, uid)
    if not ok:
        await q.answer("❌ You haven't joined " + missing["title"] + " yet!", show_alert=True)
        return
    if not user_exists(uid):
        u = q.from_user
        create_user(uid, u.id, u.username, u.first_name)
        add_points(uid, 2, "signup", "New user bonus")
        await q.edit_message_text("🎉 Welcome! +2 points credited.\n👥 Invite friends to earn more!", reply_markup=main_menu(uid))
    else:
        await q.edit_message_text("✅ Verified! Welcome back.", reply_markup=main_menu(uid))


# ── handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = update.effective_user
    args = ctx.args or []
    referrer_code = args[0] if args else None
    if referrer_code:
        LOGIN_SESSIONS[uid] = {"state": "pending_ref", "referrer_code": referrer_code}
    if not await ensure_joined(update, ctx):
        return
    pending = LOGIN_SESSIONS.pop(uid, None)
    if pending and pending.get("referrer_code") and not user_exists(uid):
        referrer_code = pending["referrer_code"]
    if not user_exists(uid):
        referred_by = None
        if referrer_code:
            r = _one("SELECT chat_id FROM users WHERE referral_code=?", (referrer_code,))
            if r and r["chat_id"] != uid:
                referred_by = r["chat_id"]
        create_user(uid, user.id, user.username, user.first_name, referred_by)
        add_points(uid, 2, "signup", "New user bonus")
        if referred_by:
            record_referral(referred_by, uid)
            add_points(referred_by, 2, "referral_bonus", "Referred user " + str(uid))
            try:
                await ctx.bot.send_message(referred_by, "Referral bonus! +2 points (user @" + str(user.username or uid) + ")")
            except Exception:
                pass
        await update.message.reply_text("🎉 Welcome! +2 points credited.\n👥 Invite friends to earn more!", reply_markup=main_menu(uid))
    else:
        await update.message.reply_text("👋 Welcome back " + (user.first_name or "") + "!", reply_markup=main_menu(uid))


async def balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, uid, q.from_user.username, q.from_user.first_name)
    u = get_user(uid)
    refs = count_referrals(uid)
    txt = ("👤 **Your Dashboard**\n\n"
           "💰 Points: `" + str(u["points"]) + "`\n"
           "👥 Referrals: `" + str(refs) + "`\n"
           "🔗 Code: `" + u["referral_code"] + "`\n"
           "📅 Joined: " + u["created_at"])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=main_menu(uid))


async def invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, uid, q.from_user.username, q.from_user.first_name)
    u = get_user(uid)
    link = "https://t.me/Viedietbypass_bot?start=" + u["referral_code"]
    txt = ("👥 **Invite Friends**\n\n"
           "Share your referral link:\n`" + link + "`\n\n"
           "🎁 You get **+2 points** and your friend gets **+2 points**\n"
           "📊 Referrals: `" + str(count_referrals(uid)) + "`")
    kb = InlineKeyboardMarkup([[btn("📋 Copy Link", f"copy_{link}", "primary")], [btn("🔙 Back", "back_menu", "danger")]])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    lb = get_leaderboard(10)
    lines = []
    for i, row in enumerate(lb, 1):
        name = row["first_name"] or row["username"] or f"User{row['chat_id']}"
        lines.append(f"{i}. {name} - `{row['points']}` pts")
    txt = "🏆 **Leaderboard**\n\n" + "\n".join(lines) + "\n\n🔙 Back to menu"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=main_menu(uid))


async def back_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    await q.edit_message_text("📌 **Main Menu**", parse_mode="Markdown", reply_markup=main_menu(uid))


async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        await q.edit_message_text("⛔ Access denied. Only admins.", reply_markup=main_menu(uid))
        return
    txt = ("🔧 **Admin Panel**\n\n"
           "👥 Total users: `" + str(total_users()) + "`\n"
           "💰 Total points: `" + str(total_points()) + "`\n"
           "🔗 Total referrals: `" + str(total_referrals()) + "`\n\n"
           "Commands:\n"
           "`/give [user_id] [points]`\n"
           "`/stats` - detailed stats\n"
           "`/users` - list all users")
    kb = InlineKeyboardMarkup([
        [btn("📊 Stats", "admin_stats", "primary")],
        [btn("👥 All Users", "admin_users", "primary")],
        [btn("➕ Add Links", "admin_add_links", "success"),
         btn("📋 Links", "admin_view_links", "primary")],
        [btn("🗑 Clear Links", "admin_clear_links", "danger")],
        [btn("🔙 Back", "back_menu", "danger")],
    ])
    await q.edit_message_text(txt, reply_markup=kb)


async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    uid = q.from_user.id
    txt = ("📊 **Bot Statistics**\n\n"
           "👥 Total users: `" + str(total_users()) + "`\n"
           "💰 Total points: `" + str(total_points()) + "`\n"
           "🔗 Total referrals: `" + str(total_referrals()) + "`\n"
           "🆔 Admins: `" + str(ADMIN_IDS) + "`")
    kb = InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    uid = q.from_user.id
    us = all_users()
    lines = []
    for u in us[:20]:
        lines.append(f"`{u['chat_id']}` | {u['first_name'] or u['username'] or '?'} | `{u['points']}` pts")
    txt = "👥 **Users (last 20)**\n\n" + "\n".join(lines)
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]]))


async def admin_add_links(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    ADMIN_STATE[q.from_user.id] = "await_links"
    await q.edit_message_text(
        "➕ **Add Offer Links**\n\n"
        "Paste links (one per line, or comma/space separated).\n"
        "Duplicates are skipped automatically.\n"
        "Bot will use up to 10 stored links.\n\n"
        "Type /cancel to abort.",
        parse_mode="Markdown")


async def admin_view_links(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    stored = _all("SELECT link FROM offer_links ORDER BY id")
    if not stored:
        txt = "📋 **Offer Links**\n\nUsing default 10 links (none added yet)."
    else:
        lines = [f"{i}. `{r['link']}`" for i, r in enumerate(stored, 1)]
        txt = "📋 **Offer Links** (" + str(len(stored)) + " stored)\n\n" + "\n".join(lines)
    kb = InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def admin_clear_links(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    clear_offer_links()
    await q.edit_message_text("🗑 All stored offer links cleared. Bot will use default 10 links.",
                              reply_markup=InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]]))


async def handle_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Only admins can use this command.")
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/give [user_id] [points]`", parse_mode="Markdown")
        return
    try:
        target = int(args[0])
        pts = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id or points.")
        return
    if not user_exists(target):
        await update.message.reply_text("❌ User `" + str(target) + "` not found in database.", parse_mode="Markdown")
        return
    add_points(target, pts, "admin_gift", "Admin " + str(uid) + " gave " + str(pts) + " points")
    await update.message.reply_text("✅ `" + str(pts) + "` points given to user `" + str(target) + "`.", parse_mode="Markdown")


async def handle_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    txt = ("📊 **Bot Statistics**\n\n"
           "👥 Total users: `" + str(total_users()) + "`\n"
           "💰 Total points: `" + str(total_points()) + "`\n"
           "🔗 Total referrals: `" + str(total_referrals()) + "`\n"
           "🆔 Admins: `" + str(ADMIN_IDS) + "`")
    await update.message.reply_text(txt, parse_mode="Markdown")


async def handle_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    us = all_users()
    lines = []
    for u in us:
        lines.append(f"`{u['chat_id']}` | {u['first_name'] or u['username'] or '?'} | `{u['points']}` pts | `{u['referral_code']}`")
    chunk = "\n".join(lines)
    for i in range(0, len(chunk), 4000):
        await update.message.reply_text(chunk[i:i + 4000], parse_mode="Markdown")


# ── Rakhi Offer flow ─────────────────────────────────────────────────────────

def login_method_kb():
    return InlineKeyboardMarkup([
        [btn("📱 Login with OTP", "login_otp", "primary")],
        [btn("📄 Login with JSON", "login_json", "success")],
        [btn("🔙 Back", "back_menu", "danger")],
    ])


async def rakhi_offer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await ensure_joined(update, ctx):
        return
    uid = q.from_user.id
    await q.edit_message_text("⚡ **Rakhi Sibling Wars Offer**\n\nChoose login method:", parse_mode="Markdown", reply_markup=login_method_kb())


async def login_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    LOGIN_SESSIONS[uid] = {"state": "await_mobile", "mobile": None, "tid": None, "sid": None, "headers": None, "team": 1}
    await q.edit_message_text("📱 **OTP Login**\n\nSend your 10-digit mobile number:\n(/cancel to exit)", parse_mode="Markdown")


async def login_json(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    LOGIN_SESSIONS[uid] = {"state": "await_json", "headers": None, "team": 1}
    await q.edit_message_text("📄 **JSON Login**\n\nPaste your session JSON:\n`{\"tid\": \"...\", \"sid\": \"...\", \"userid\": \"...\"}`\nMinimum: tid + sid\n(/cancel to exit)", parse_mode="Markdown")


def team_kb():
    return InlineKeyboardMarkup([
        [btn("👧 Sister (SIS)", "team:1", "primary"), btn("👦 Brother (BRO)", "team:2", "danger")],
        [btn("⚡ Run all 10 offers", "run", "success")],
    ])


async def run_offers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    s = LOGIN_SESSIONS.get(uid)
    if not s or not s.get("headers"):
        await q.edit_message_text("❌ Not logged in. Start again with ⚡ Rakhi Offer.")
        return
    team = s["team"]
    links = get_offer_links()
    msg = await q.edit_message_text("⚡ Running " + str(len(links)) + " offers (team " + str(team) + ")...")
    results = []
    for i, link in enumerate(links, 1):
        m = re.search(r"rakhi_wars-([A-Za-z0-9_-]+)", link)
        code = m.group(1) if m else link
        ok, err = run_one(s["headers"], code, team)
        results.append((code, ok, err))
        lines = "\n".join(f"{'✅' if ok else '❌'}  {c}" for c, ok, *_ in results)
        try:
            await msg.edit_text("⚡ Running " + str(len(links)) + " offers...\n\n" + lines +
                                ("\n\n⏳ Working..." if i < len(links) else "\n\n✅ Done!"))
        except Exception:
            pass
    done = sum(1 for _, ok, _ in results if ok)
    detail = "\n".join(f"{'✅' if ok else '❌'} `{c}`" + (f"\n   {e}" if not ok else "") for c, ok, e in results)
    await ctx.bot.send_message(uid, "🏁 **Finished:** " + str(done) + "/" + str(len(links)) + " completed.\n\n" + detail, parse_mode="Markdown")


# ── callback router ──────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id

    # answer immediately so no loading spinner lingers
    await q.answer()

    # handle copy
    if data.startswith("copy_"):
        await q.answer(text=data[5:], show_alert=True)
        return

    # handle team / run (no membership check needed — already in offer flow)
    if data.startswith("team:"):
        s = LOGIN_SESSIONS.get(uid)
        if s:
            s["team"] = int(data.split(":")[1])
        await q.edit_message_text("Team set. Now Run:", reply_markup=team_kb())
        return
    if data == "run":
        await run_offers(update, ctx)
        return

    # membership gate for non-admin, non-join buttons
    admin_only = data in ("admin_panel", "admin_stats", "admin_users",
                          "admin_add_links", "admin_view_links", "admin_clear_links")
    if data != "join_verified" and not admin_only and not is_admin(uid):
        ok, _ = await check_membership(ctx.bot, uid)
        if not ok:
            await q.edit_message_text(force_join_text(), reply_markup=force_join_kb())
            return

    # dispatch to handler
    handlers = {
        "balance": balance, "invite": invite, "leaderboard": leaderboard,
        "back_menu": back_menu, "admin_panel": admin_panel,
        "admin_stats": admin_stats, "admin_users": admin_users,
        "admin_add_links": admin_add_links, "admin_view_links": admin_view_links,
        "admin_clear_links": admin_clear_links,
        "rakhi_offer": rakhi_offer, "login_otp": login_otp, "login_json": login_json,
        "join_verified": join_verified,
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


# ── text handler ─────────────────────────────────────────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    uid = update.effective_chat.id
    text = (update.message.text or "").strip()

    # admin pasting bulk offer links
    if ADMIN_STATE.get(uid) == "await_links":
        ADMIN_STATE.pop(uid, None)
        if not is_admin(uid):
            return
        if text.lower() in ("/cancel", "cancel"):
            await update.message.reply_text("Cancelled.", reply_markup=main_menu(uid))
            return
        added, skipped, stored = add_offer_links(text)
        await update.message.reply_text(
            "✅ Done!\n"
            "➕ Added: " + str(added) + "\n"
            "⚠️ Duplicates skipped: " + str(skipped) + "\n"
            "📋 Total stored: " + str(stored) + " (bot uses up to 10)\n\n"
            "Send /cancel to finish.",
            reply_markup=main_menu(uid))
        return

    s = LOGIN_SESSIONS.get(uid)
    if not s or s.get("state") not in ("await_mobile", "await_otp", "await_json"):
        if not await ensure_joined(update, ctx):
            return
        await update.message.reply_text("📌 Use the main menu buttons below:", reply_markup=main_menu(uid))
        return

    if s["state"] == "await_mobile":
        if not re.fullmatch(r"\d{10}", text):
            await update.message.reply_text("⚠️ Enter 10-digit number only.")
            return
        try:
            data = send_otp(text)
            s["mobile"] = text; s["tid"] = data["tid"]; s["sid"] = data["sid"]
            s["state"] = "await_otp"
            await update.message.reply_text("📲 OTP sent to +91" + text + ". Send the OTP.")
        except (SwiggyError, Exception) as e:
            log.exception("send_otp")
            await update.message.reply_text("❌ Error: " + str(e))

    elif s["state"] == "await_otp":
        try:
            v = verify_otp(s["mobile"], text, s["tid"], s["sid"])
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "login_debug.json"), "w", encoding="utf-8") as fh:
                    json.dump(v, fh, indent=2, ensure_ascii=False)
            except Exception:
                pass
            h = build_session(v)
            if not h.get("userid"):
                await update.message.reply_text("❌ Login failed: no user in response.")
                return
            s["headers"] = h; s["state"] = "logged_in"
            # test session against campaign API
            test_link = get_offer_links(1)[0]
            test_ref = re.search(r"rakhi_wars-([A-Za-z0-9_-]+)", test_link)
            test_ref = test_ref.group(1) if test_ref else test_link
            try:
                campaign_details(h, test_ref)
                session_ok = "✅ Session valid"
            except SwiggyError as e:
                session_ok = "❌ Session invalid: " + str(e)
            await update.message.reply_text(
                "✅ Logged in as user `" + h["userid"] + "`.\n"
                + session_ok + "\n\n"
                "userid: `" + h["userid"] + "`\n"
                "tid: `" + h["tid"][:30] + "...`\n"
                "sid: `" + h["sid"][:20] + "...`\n"
                "token: `" + ("present" if h["token"] else "MISSING") + "`",
                parse_mode="Markdown", reply_markup=team_kb())
        except (SwiggyError, Exception) as e:
            log.exception("verify")
            await update.message.reply_text("❌ OTP error: " + str(e))

    elif s["state"] == "await_json":
        try:
            obj = json.loads(text)
            h = build_session_from_json(obj)
            if not h.get("tid") and not h.get("userid"):
                raise SwiggyError("JSON missing tid/sid/userid")
            s["headers"] = h; s["state"] = "logged_in"
            await update.message.reply_text("✅ JSON session loaded (userid: `" + str(h.get("userid") or "?") + "`).", parse_mode="Markdown", reply_markup=team_kb())
        except Exception as e:
            await update.message.reply_text("❌ Invalid JSON: " + str(e) + "\nPaste a valid session JSON.")


async def handle_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    LOGIN_SESSIONS.pop(uid, None)
    ADMIN_STATE.pop(uid, None)
    await update.message.reply_text("❌ Cancelled. /start for main menu.", reply_markup=main_menu(uid))


async def error_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log.warning("handler error: %s", ctx.error)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    async def _post_init(app_):
        await resolve_chat_ids(app_)
    app.post_init = _post_init

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("give", handle_give))
    app.add_handler(CommandHandler("stats", handle_stats))
    app.add_handler(CommandHandler("users", handle_users))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot started (admin_ids=%s)", ADMIN_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()"
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "1364476174").split(",") if x.strip()]
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db"))

REQUIRED_CHANNELS = [
    {"username": "viedietlooters", "url": "https://t.me/viedietlooters", "title": "Main Channel"},
    {"username": "viedietbackup", "url": "https://t.me/viedietbackup", "title": "Backup Group"},
]

# ── DB ───────────────────────────────────────────────────────────────────────

# ── DB (thread-safe single connection) ──────────────────────────────────────

import threading

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
                points INTEGER DEFAULT 0, referral_code TEXT UNIQUE, referred_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')), is_banned INTEGER DEFAULT 0)""",
            """CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referee_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')))""",
            """CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, amount INTEGER,
                type TEXT, description TEXT, created_at TEXT DEFAULT (datetime('now')))""",
            """CREATE TABLE IF NOT EXISTS offer_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT, link TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now')))""",
        ):
            _db_conn.execute(ddl)
        _db_conn.commit()


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
    """Run several statements atomically under one lock."""
    with _db_lock:
        _init_db()
        for sql, args in statements:
            _db_conn.execute(sql, args)
        _db_conn.commit()


def user_exists(chat_id):
    return _one("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)) is not None


def get_user(chat_id):
    return _one("SELECT * FROM users WHERE chat_id=?", (chat_id,))


def ensure_user(chat_id, user_id, username, first_name):
    u = get_user(chat_id)
    if u:
        return u
    code = md5(f"{chat_id}_{time.time()}".encode()).hexdigest()[:8]
    _run("""INSERT OR IGNORE INTO users (chat_id, user_id, username, first_name, referral_code)
        VALUES (?,?,?,?,?)""", (chat_id, str(user_id) or "", username or "", first_name or "", code))
    return get_user(chat_id)


def create_user(chat_id, user_id, username, first_name, referred_by=None):
    code = md5(f"{chat_id}_{time.time()}".encode()).hexdigest()[:8]
    _run("""INSERT OR IGNORE INTO users (chat_id, user_id, username, first_name, referral_code, referred_by)
        VALUES (?,?,?,?,?,?)""", (chat_id, str(user_id) or "", username or "", first_name or "", code, referred_by))
    return code


def add_points(chat_id, amount, typ, desc):
    _multi([
        ("UPDATE users SET points = points + ? WHERE chat_id=?", (amount, chat_id)),
        ("INSERT INTO transactions (chat_id, amount, type, description) VALUES (?,?,?,?)",
         (chat_id, amount, typ, desc)),
    ])


def get_points(chat_id):
    r = _one("SELECT points FROM users WHERE chat_id=?", (chat_id,))
    return r["points"] if r else 0


def get_leaderboard(limit=10):
    return _all("SELECT chat_id, username, first_name, points FROM users ORDER BY points DESC LIMIT ?", (limit,))


def count_referrals(chat_id):
    r = _one("SELECT COUNT(*) c FROM referrals WHERE referrer_id=?", (chat_id,))
    return r["c"] if r else 0


def record_referral(referrer_id, referee_id):
    _run("INSERT INTO referrals (referrer_id, referee_id) VALUES (?,?)", (referrer_id, referee_id))


def all_users():
    return _all("SELECT * FROM users ORDER BY created_at DESC")


def total_users():
    r = _one("SELECT COUNT(*) c FROM users")
    return r["c"] if r else 0


def total_points():
    r = _one("SELECT COALESCE(SUM(points),0) s FROM users")
    return r["s"] if r else 0


def total_referrals():
    r = _one("SELECT COUNT(*) c FROM referrals")
    return r["c"] if r else 0


# ── offer links (admin-managed, stored in DB) ───────────────────────────────

def get_offer_links(limit=10):
    """Return stored links (up to limit); falls back to default OFFER_LINKS."""
    rows = _all("SELECT link FROM offer_links ORDER BY id LIMIT ?", (limit,))
    links = [r["link"] for r in rows]
    return links if links else OFFER_LINKS


def add_offer_links(text):
    """Parse bulk links (one per line / comma / space), dedupe, insert.
    Returns (added_count, skipped_duplicates, stored_total)."""
    raw = re.split(r"[\s,]+", text.strip())
    links = []
    seen = set()
    skipped = 0
    for l in raw:
        l = l.strip().strip('"').strip("'")
        if not l or not l.lower().startswith("http"):
            continue
        if l in seen:
            skipped += 1
            continue
        seen.add(l)
        links.append(l)
    added = 0
    with _db_lock:
        _init_db()
        for l in links:
            cur = _db_conn.execute("INSERT OR IGNORE INTO offer_links (link) VALUES (?)", (l,))
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
        _db_conn.commit()
    stored = _one("SELECT COUNT(*) c FROM offer_links")["c"]
    return added, skipped, stored


def clear_offer_links():
    _run("DELETE FROM offer_links")


# ── SWIGGY API ───────────────────────────────────────────────────────────────

CAMPAIGN_HOST = "https://disc.swiggy.com"
CAMPAIGN_ID = "rakhi_wars"
CAMPAIGN_DETAILS = "/api/v1/campaign/details"
CAMPAIGN_SUBMIT = "/api/v1/campaign/submit"
CAMPAIGN_POLL = "/api/v1/campaign/poll"

SMS_OTP_URL = "https://profile.swiggy.com/api/v3/app/sms_otp"
VERIFY_URL = "https://profile.swiggy.com/api/v3/app/login/verify"
DEVICE_ID = "9b69ea5de1ef3f99"

OFFER_LINKS = [
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-1adhdtaCdQ",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-MZxgRHuBN",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-2Out6vNL00",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-3RvWb1Aj0IL",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-8OkuWB2dr",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-8OkuF7L1h",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-1jfCJc8KB6",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-1jfCJxsjBk",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-9ggQmy8CP",
    "https://r.swiggy.com/rakhi_wars/rakhi_wars-2SmfgnlXXd",
]

LOGIN_HEADERS = {
    "Accept": "application/json; charset=utf-8", "app-version": "4.106.1", "category": "food",
    "deviceId": DEVICE_ID, "swuid": DEVICE_ID, "os-version": "9", "pl-version": "131",
    "User-Agent": "Swiggy-Android", "version-code": "1716", "x-channel": "swiggy",
}

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 4 Build/RD2A.211001.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.120 Mobile Safari/537.36",
    "platform": "Swiggy-Android", "version-code": "1795", "model-name": "PIXEL 4",
    "manufacturer": "GOOGLE", "application_name": "swiggy-app", "appversion": "4.113.0",
    "cityid": "18", "lat": "22.74215", "lng": "75.9078633",
    "x-requested-with": "in.swiggy.android", "x-theme": "base",
    "accept-language": "en-IN,en-US;q=0.9,en;q=0.8",
}


class SwiggyError(Exception):
    pass


def _checked(r):
    try:
        d = r.json()
    except Exception:
        d = None
    if d is None:
        raise SwiggyError(f"non-JSON HTTP {r.status_code}")
    st = d.get("statusCode")
    if st == 999:
        raise SwiggyError(f"session expired: {d.get('statusMessage')}")
    if isinstance(st, int) and st != 0:
        raise SwiggyError(f"api error {st}: {d.get('statusMessage')}")
    return d


def send_otp(mobile10):
    return _checked(requests.get(f"{SMS_OTP_URL}?mobile={mobile10}", headers=LOGIN_HEADERS, timeout=30))


def verify_otp(mobile10, otp, tid, sid):
    h = {**LOGIN_HEADERS, "sid": sid, "Tid": tid, "Content-Type": "application/json; charset=utf-8"}
    p = {"cloningSignalsData": {"appFilesDirPathInvalid": 0, "developerModeEnabled": 1,
                                "deviceModelVmos": 0, "emulatorStatus": 0,
                                "packageName": "in.swiggy.android", "workProfileEnabled": 0},
         "otp": otp}
    return _checked(requests.post(f"{VERIFY_URL}?otp_source=Sms-automatic", headers=h, json=p, timeout=30))


def _jwt_payload(token):
    try:
        part = token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def build_session(v):
    tid = v.get("tid", "")
    sid = v.get("sid", "")
    userid = _jwt_payload(tid).get("user_id") or ""
    token = ""
    for k in ("token", "accessToken", "refreshToken"):
        if (v.get("data") or {}).get(k):
            token = (v.get("data") or {})[k]
            break
    return {"userid": userid, "tid": tid, "sid": sid, "token": token}


def build_session_from_json(obj):
    tid = obj.get("tid") or ""
    sid = obj.get("sid") or ""
    if "data" in obj and tid:
        return build_session(obj)
    userid = str(obj.get("userid") or obj.get("userId") or obj.get("user_id") or "")
    token = obj.get("token") or ""
    if not userid and tid:
        userid = str(_jwt_payload(tid).get("user_id") or "")
    return {"userid": userid, "tid": tid, "sid": sid, "token": token}


def _sh(session, referral="", **extra):
    h = dict(BASE_HEADERS)
    h.update(session)
    h["campaignId"] = CAMPAIGN_ID
    if referral:
        h["referral-code"] = referral
    h.update(extra)
    return h


def campaign_details(session, r):
    return _checked(requests.get(CAMPAIGN_HOST + CAMPAIGN_DETAILS, headers=_sh(session, r), timeout=30))


def campaign_submit(session, a, t, r=""):
    return _checked(requests.post(CAMPAIGN_HOST + CAMPAIGN_SUBMIT,
                                  headers=_sh(session, r, **{"Content-Type": "application/json"}),
                                  json={"action": a, "teamSelected": t, "campaignId": CAMPAIGN_ID}, timeout=30))


def campaign_poll(session, r=""):
    return _checked(requests.get(CAMPAIGN_HOST + CAMPAIGN_POLL, headers=_sh(session, r), timeout=30))


def run_one(session, ref, team=1):
    try:
        campaign_details(session, ref)
        campaign_submit(session, "JOIN_MATCH", team, ref)
        for _ in range(5):
            atk = campaign_submit(session, "ATTACK", team, ref)
            if (atk.get("data") or {}).get("userState", {}).get("attacksLeft", 0) <= 0:
                break
            time.sleep(1)
        campaign_poll(session, ref)
        return (True, "")
    except SwiggyError as e:
        return (False, str(e))


# ── TELEGRAM BOT ─────────────────────────────────────────────────────────────

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)

LOGIN_SESSIONS = {}
ADMIN_STATE = {}

# ── helpers ──────────────────────────────────────────────────────────────────

def btn(text, cb, style="primary"):
    return InlineKeyboardButton(text, callback_data=cb, style=style)


def is_admin(uid):
    return uid in ADMIN_IDS


def main_menu(uid=None):
    rows = [
        [btn("👤 My Balance", "balance", "primary")],
        [btn("👥 Invite Friends", "invite", "success"),
         btn("🏆 Leaderboard", "leaderboard", "primary")],
        [btn("⚡ Rakhi Offer", "rakhi_offer", "danger")],
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
    rows = [[InlineKeyboardButton("🔗 Join " + ch["title"], url=ch["url"])]
            for ch in REQUIRED_CHANNELS]
    rows.append([btn("✅ I've Joined", "join_verified", "success")])
    return InlineKeyboardMarkup(rows)


def force_join_text():
    lines = ["🔒 **Join to continue using the bot**"]
    for ch in REQUIRED_CHANNELS:
        lines.append("• " + ch["title"] + ": " + ch["url"])
    lines.append("\nJoin both, then tap '✅ I've Joined'.")
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
            add_points(uid, 2, "signup", "New user bonus")
        await q.edit_message_text("✅ Welcome admin!", reply_markup=main_menu(uid))
        return
    ok, missing = await check_membership(ctx.bot, uid)
    if not ok:
        await q.answer("❌ You haven't joined " + missing["title"] + " yet!", show_alert=True)
        return
    if not user_exists(uid):
        u = q.from_user
        create_user(uid, u.id, u.username, u.first_name)
        add_points(uid, 2, "signup", "New user bonus")
        await q.edit_message_text("🎉 Welcome! +2 points credited.\n👥 Invite friends to earn more!", reply_markup=main_menu(uid))
    else:
        await q.edit_message_text("✅ Verified! Welcome back.", reply_markup=main_menu(uid))


# ── handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = update.effective_user
    args = ctx.args or []
    referrer_code = args[0] if args else None
    if referrer_code:
        LOGIN_SESSIONS[uid] = {"state": "pending_ref", "referrer_code": referrer_code}
    if not await ensure_joined(update, ctx):
        return
    pending = LOGIN_SESSIONS.pop(uid, None)
    if pending and pending.get("referrer_code") and not user_exists(uid):
        referrer_code = pending["referrer_code"]
    if not user_exists(uid):
        referred_by = None
        if referrer_code:
            r = _one("SELECT chat_id FROM users WHERE referral_code=?", (referrer_code,))
            if r and r["chat_id"] != uid:
                referred_by = r["chat_id"]
        create_user(uid, user.id, user.username, user.first_name, referred_by)
        add_points(uid, 2, "signup", "New user bonus")
        if referred_by:
            record_referral(referred_by, uid)
            add_points(referred_by, 2, "referral_bonus", "Referred user " + str(uid))
            try:
                await ctx.bot.send_message(referred_by, "Referral bonus! +2 points (user @" + str(user.username or uid) + ")")
            except Exception:
                pass
        await update.message.reply_text("🎉 Welcome! +2 points credited.\n👥 Invite friends to earn more!", reply_markup=main_menu(uid))
    else:
        await update.message.reply_text("👋 Welcome back " + (user.first_name or "") + "!", reply_markup=main_menu(uid))


async def balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, uid, q.from_user.username, q.from_user.first_name)
    u = get_user(uid)
    refs = count_referrals(uid)
    txt = ("👤 **Your Dashboard**\n\n"
           "💰 Points: `" + str(u["points"]) + "`\n"
           "👥 Referrals: `" + str(refs) + "`\n"
           "🔗 Code: `" + u["referral_code"] + "`\n"
           "📅 Joined: " + u["created_at"])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=main_menu(uid))


async def invite(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    ensure_user(uid, uid, q.from_user.username, q.from_user.first_name)
    u = get_user(uid)
    link = "https://t.me/Viedietbypass_bot?start=" + u["referral_code"]
    txt = ("👥 **Invite Friends**\n\n"
           "Share your referral link:\n`" + link + "`\n\n"
           "🎁 You get **+2 points** and your friend gets **+2 points**\n"
           "📊 Referrals: `" + str(count_referrals(uid)) + "`")
    kb = InlineKeyboardMarkup([[btn("📋 Copy Link", f"copy_{link}", "primary")], [btn("🔙 Back", "back_menu", "danger")]])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    lb = get_leaderboard(10)
    lines = []
    for i, row in enumerate(lb, 1):
        name = row["first_name"] or row["username"] or f"User{row['chat_id']}"
        lines.append(f"{i}. {name} - `{row['points']}` pts")
    txt = "🏆 **Leaderboard**\n\n" + "\n".join(lines) + "\n\n🔙 Back to menu"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=main_menu(uid))


async def back_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    await q.edit_message_text("📌 **Main Menu**", parse_mode="Markdown", reply_markup=main_menu(uid))


async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        await q.edit_message_text("⛔ Access denied. Only admins.", reply_markup=main_menu(uid))
        return
    txt = ("🔧 **Admin Panel**\n\n"
           "👥 Total users: `" + str(total_users()) + "`\n"
           "💰 Total points: `" + str(total_points()) + "`\n"
           "🔗 Total referrals: `" + str(total_referrals()) + "`\n\n"
           "Commands:\n"
           "`/give [user_id] [points]`\n"
           "`/stats` - detailed stats\n"
           "`/users` - list all users")
    kb = InlineKeyboardMarkup([
        [btn("📊 Stats", "admin_stats", "primary")],
        [btn("👥 All Users", "admin_users", "primary")],
        [btn("➕ Add Links", "admin_add_links", "success"),
         btn("📋 Links", "admin_view_links", "primary")],
        [btn("🗑 Clear Links", "admin_clear_links", "danger")],
        [btn("🔙 Back", "back_menu", "danger")],
    ])
    await q.edit_message_text(txt, reply_markup=kb)


async def admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    uid = q.from_user.id
    txt = ("📊 **Bot Statistics**\n\n"
           "👥 Total users: `" + str(total_users()) + "`\n"
           "💰 Total points: `" + str(total_points()) + "`\n"
           "🔗 Total referrals: `" + str(total_referrals()) + "`\n"
           "🆔 Admins: `" + str(ADMIN_IDS) + "`")
    kb = InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    uid = q.from_user.id
    us = all_users()
    lines = []
    for u in us[:20]:
        lines.append(f"`{u['chat_id']}` | {u['first_name'] or u['username'] or '?'} | `{u['points']}` pts")
    txt = "👥 **Users (last 20)**\n\n" + "\n".join(lines)
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]]))


async def admin_add_links(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    ADMIN_STATE[q.from_user.id] = "await_links"
    await q.edit_message_text(
        "➕ **Add Offer Links**\n\n"
        "Paste links (one per line, or comma/space separated).\n"
        "Duplicates are skipped automatically.\n"
        "Bot will use up to 10 stored links.\n\n"
        "Type /cancel to abort.",
        parse_mode="Markdown")


async def admin_view_links(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    stored = _all("SELECT link FROM offer_links ORDER BY id")
    if not stored:
        txt = "📋 **Offer Links**\n\nUsing default 10 links (none added yet)."
    else:
        lines = [f"{i}. `{r['link']}`" for i, r in enumerate(stored, 1)]
        txt = "📋 **Offer Links** (" + str(len(stored)) + " stored)\n\n" + "\n".join(lines)
    kb = InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]])
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=kb)


async def admin_clear_links(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    clear_offer_links()
    await q.edit_message_text("🗑 All stored offer links cleared. Bot will use default 10 links.",
                              reply_markup=InlineKeyboardMarkup([[btn("🔙 Back to Admin", "admin_panel", "danger")]]))


async def handle_give(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Only admins can use this command.")
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Usage: `/give [user_id] [points]`", parse_mode="Markdown")
        return
    try:
        target = int(args[0])
        pts = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid user_id or points.")
        return
    if not user_exists(target):
        await update.message.reply_text("❌ User `" + str(target) + "` not found in database.", parse_mode="Markdown")
        return
    add_points(target, pts, "admin_gift", "Admin " + str(uid) + " gave " + str(pts) + " points")
    await update.message.reply_text("✅ `" + str(pts) + "` points given to user `" + str(target) + "`.", parse_mode="Markdown")


async def handle_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    txt = ("📊 **Bot Statistics**\n\n"
           "👥 Total users: `" + str(total_users()) + "`\n"
           "💰 Total points: `" + str(total_points()) + "`\n"
           "🔗 Total referrals: `" + str(total_referrals()) + "`\n"
           "🆔 Admins: `" + str(ADMIN_IDS) + "`")
    await update.message.reply_text(txt, parse_mode="Markdown")


async def handle_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    us = all_users()
    lines = []
    for u in us:
        lines.append(f"`{u['chat_id']}` | {u['first_name'] or u['username'] or '?'} | `{u['points']}` pts | `{u['referral_code']}`")
    chunk = "\n".join(lines)
    for i in range(0, len(chunk), 4000):
        await update.message.reply_text(chunk[i:i + 4000], parse_mode="Markdown")


# ── Rakhi Offer flow ─────────────────────────────────────────────────────────

def login_method_kb():
    return InlineKeyboardMarkup([
        [btn("📱 Login with OTP", "login_otp", "primary")],
        [btn("📄 Login with JSON", "login_json", "success")],
        [btn("🔙 Back", "back_menu", "danger")],
    ])


async def rakhi_offer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await ensure_joined(update, ctx):
        return
    uid = q.from_user.id
    await q.edit_message_text("⚡ **Rakhi Sibling Wars Offer**\n\nChoose login method:", parse_mode="Markdown", reply_markup=login_method_kb())


async def login_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    LOGIN_SESSIONS[uid] = {"state": "await_mobile", "mobile": None, "tid": None, "sid": None, "headers": None, "team": 1}
    await q.edit_message_text("📱 **OTP Login**\n\nSend your 10-digit mobile number:\n(/cancel to exit)", parse_mode="Markdown")


async def login_json(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    LOGIN_SESSIONS[uid] = {"state": "await_json", "headers": None, "team": 1}
    await q.edit_message_text("📄 **JSON Login**\n\nPaste your session JSON:\n`{\"tid\": \"...\", \"sid\": \"...\", \"userid\": \"...\"}`\nMinimum: tid + sid\n(/cancel to exit)", parse_mode="Markdown")


def team_kb():
    return InlineKeyboardMarkup([
        [btn("👧 Sister (SIS)", "team:1", "primary"), btn("👦 Brother (BRO)", "team:2", "danger")],
        [btn("⚡ Run all 10 offers", "run", "success")],
    ])


async def run_offers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    s = LOGIN_SESSIONS.get(uid)
    if not s or not s.get("headers"):
        await q.edit_message_text("❌ Not logged in. Start again with ⚡ Rakhi Offer.")
        return
    team = s["team"]
    links = get_offer_links()
    msg = await q.edit_message_text("⚡ Running " + str(len(links)) + " offers (team " + str(team) + ")...")
    results = []
    for i, link in enumerate(links, 1):
        m = re.search(r"rakhi_wars-([A-Za-z0-9_-]+)", link)
        code = m.group(1) if m else link
        ok, err = run_one(s["headers"], code, team)
        results.append((code, ok, err))
        lines = "\n".join(f"{'✅' if ok else '❌'}  {c}" for c, ok, *_ in results)
        try:
            await msg.edit_text("⚡ Running " + str(len(links)) + " offers...\n\n" + lines +
                                ("\n\n⏳ Working..." if i < len(links) else "\n\n✅ Done!"))
        except Exception:
            pass
    done = sum(1 for _, ok, _ in results if ok)
    detail = "\n".join(f"{'✅' if ok else '❌'} `{c}`" + (f"\n   {e}" if not ok else "") for c, ok, e in results)
    await ctx.bot.send_message(uid, "🏁 **Finished:** " + str(done) + "/" + str(len(links)) + " completed.\n\n" + detail, parse_mode="Markdown")


# ── callback router ──────────────────────────────────────────────────────────

async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id

    # answer immediately so no loading spinner lingers
    await q.answer()

    # handle copy
    if data.startswith("copy_"):
        await q.answer(text=data[5:], show_alert=True)
        return

    # handle team / run (no membership check needed — already in offer flow)
    if data.startswith("team:"):
        s = LOGIN_SESSIONS.get(uid)
        if s:
            s["team"] = int(data.split(":")[1])
        await q.edit_message_text("Team set. Now Run:", reply_markup=team_kb())
        return
    if data == "run":
        await run_offers(update, ctx)
        return

    # membership gate for non-admin, non-join buttons
    admin_only = data in ("admin_panel", "admin_stats", "admin_users",
                          "admin_add_links", "admin_view_links", "admin_clear_links")
    if data != "join_verified" and not admin_only and not is_admin(uid):
        ok, _ = await check_membership(ctx.bot, uid)
        if not ok:
            await q.edit_message_text(force_join_text(), reply_markup=force_join_kb())
            return

    # dispatch to handler
    handlers = {
        "balance": balance, "invite": invite, "leaderboard": leaderboard,
        "back_menu": back_menu, "admin_panel": admin_panel,
        "admin_stats": admin_stats, "admin_users": admin_users,
        "admin_add_links": admin_add_links, "admin_view_links": admin_view_links,
        "admin_clear_links": admin_clear_links,
        "rakhi_offer": rakhi_offer, "login_otp": login_otp, "login_json": login_json,
        "join_verified": join_verified,
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


# ── text handler ─────────────────────────────────────────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    uid = update.effective_chat.id
    text = (update.message.text or "").strip()

    # admin pasting bulk offer links
    if ADMIN_STATE.get(uid) == "await_links":
        ADMIN_STATE.pop(uid, None)
        if not is_admin(uid):
            return
        if text.lower() in ("/cancel", "cancel"):
            await update.message.reply_text("Cancelled.", reply_markup=main_menu(uid))
            return
        added, skipped, stored = add_offer_links(text)
        await update.message.reply_text(
            "✅ Done!\n"
            "➕ Added: " + str(added) + "\n"
            "⚠️ Duplicates skipped: " + str(skipped) + "\n"
            "📋 Total stored: " + str(stored) + " (bot uses up to 10)\n\n"
            "Send /cancel to finish.",
            reply_markup=main_menu(uid))
        return

    s = LOGIN_SESSIONS.get(uid)
    if not s or s.get("state") not in ("await_mobile", "await_otp", "await_json"):
        if not await ensure_joined(update, ctx):
            return
        await update.message.reply_text("📌 Use the main menu buttons below:", reply_markup=main_menu(uid))
        return

    if s["state"] == "await_mobile":
        if not re.fullmatch(r"\d{10}", text):
            await update.message.reply_text("⚠️ Enter 10-digit number only.")
            return
        try:
            data = send_otp(text)
            s["mobile"] = text; s["tid"] = data["tid"]; s["sid"] = data["sid"]
            s["state"] = "await_otp"
            await update.message.reply_text("📲 OTP sent to +91" + text + ". Send the OTP.")
        except (SwiggyError, Exception) as e:
            log.exception("send_otp")
            await update.message.reply_text("❌ Error: " + str(e))

    elif s["state"] == "await_otp":
        try:
            v = verify_otp(s["mobile"], text, s["tid"], s["sid"])
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "login_debug.json"), "w", encoding="utf-8") as fh:
                    json.dump(v, fh, indent=2, ensure_ascii=False)
            except Exception:
                pass
            h = build_session(v)
            if not h.get("userid"):
                await update.message.reply_text("❌ Login failed: no user in response.")
                return
            s["headers"] = h; s["state"] = "logged_in"
            # test session against campaign API
            test_link = get_offer_links(1)[0]
            test_ref = re.search(r"rakhi_wars-([A-Za-z0-9_-]+)", test_link)
            test_ref = test_ref.group(1) if test_ref else test_link
            try:
                campaign_details(h, test_ref)
                session_ok = "✅ Session valid"
            except SwiggyError as e:
                session_ok = "❌ Session invalid: " + str(e)
            await update.message.reply_text(
                "✅ Logged in as user `" + h["userid"] + "`.\n"
                + session_ok + "\n\n"
                "userid: `" + h["userid"] + "`\n"
                "tid: `" + h["tid"][:30] + "...`\n"
                "sid: `" + h["sid"][:20] + "...`\n"
                "token: `" + ("present" if h["token"] else "MISSING") + "`",
                parse_mode="Markdown", reply_markup=team_kb())
        except (SwiggyError, Exception) as e:
            log.exception("verify")
            await update.message.reply_text("❌ OTP error: " + str(e))

    elif s["state"] == "await_json":
        try:
            obj = json.loads(text)
            h = build_session_from_json(obj)
            if not h.get("tid") and not h.get("userid"):
                raise SwiggyError("JSON missing tid/sid/userid")
            s["headers"] = h; s["state"] = "logged_in"
            await update.message.reply_text("✅ JSON session loaded (userid: `" + str(h.get("userid") or "?") + "`).", parse_mode="Markdown", reply_markup=team_kb())
        except Exception as e:
            await update.message.reply_text("❌ Invalid JSON: " + str(e) + "\nPaste a valid session JSON.")


async def handle_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    LOGIN_SESSIONS.pop(uid, None)
    ADMIN_STATE.pop(uid, None)
    await update.message.reply_text("❌ Cancelled. /start for main menu.", reply_markup=main_menu(uid))


async def error_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log.warning("handler error: %s", ctx.error)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    async def _post_init(app_):
        await resolve_chat_ids(app_)
    app.post_init = _post_init

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("give", handle_give))
    app.add_handler(CommandHandler("stats", handle_stats))
    app.add_handler(CommandHandler("users", handle_users))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot started (admin_ids=%s)", ADMIN_IDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
