#!/usr/bin/env python3
"""
Lenskart Run For Frame - TELEGRAM BOT
Coupon generator with forced channel join, referral system, inline UI & admin panel.

Made By Viediet
Works locally and on Railway (reads BOT_TOKEN from env).
"""

import os
import json
import random
import time
import uuid
import hashlib
import base64
import asyncio
import threading
import requests
import cloudscraper
import warnings
from datetime import datetime

# Suppress the harmless PTBUserWarning about CallbackQueryHandler + per_message=False
warnings.filterwarnings("ignore", message=".*per_message.*CallbackQueryHandler.*")

from telegram import Update
from telegram.error import Conflict, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "1364476174").split(",") if x.strip()]

# GitHub backup → SEPARATE data repo (so pushes never trigger Railway redeploy)
# Owner/repo default to your dedicated data repo: https://github.com/viediet777-hub/bp
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "viediet777-hub")
GITHUB_REPO = os.getenv("GITHUB_REPO", "bp")
GITHUB_PATH = "bot_data.json"

CHANNEL_USERNAME = "@viedietlooters"
GROUP_USERNAME = "@viedietbackup"
CHANNEL_LINK = "https://t.me/viedietlooters"
GROUP_LINK = "https://t.me/viedietbackup"

DATA_FILE = "bot_data.json"
REWARD_STEPS = 30000

BASE = "https://api-gateway.juno.lenskart.com"

BRANDS = ["xiaomi", "realme", "samsung", "oneplus", "oppo", "vivo"]
MODELS = {
    "xiaomi": ["Mi 11X", "Redmi Note 10", "Mi 10", "Poco X3"],
    "realme": ["RMX3031", "RMX3370", "RMX3360", "RMX3263"],
    "samsung": ["SM-G998B", "SM-G991B", "SM-A526B", "SM-M515F"],
    "oneplus": ["LE2115", "LE2125", "KB2001", "IN2015"],
    "oppo": ["CPH2207", "CPH2249", "CPH2217"],
    "vivo": ["V2024", "V2036", "V2041", "V2115"],
}
ANDROID_VERSIONS = ["13", "14"]


def is_admin(uid):
    return int(uid) in ADMIN_IDS


# ============================================================
# PERSISTENCE
# ============================================================
def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"users": {}, "referrals": {}}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    # Push to GitHub in background so it never blocks the bot event loop
    if GITHUB_TOKEN:
        threading.Thread(target=github_push, daemon=True).start()


def github_push():
    """Backup bot_data.json to GitHub repo (owner/repo above)."""
    try:
        if not GITHUB_TOKEN:
            return
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }
        try:
            with open(DATA_FILE, "r") as f:
                content = f.read()
        except Exception:
            return
        sha = None
        try:
            r = requests.get(url, headers=headers, timeout=20)
            if r.status_code == 200:
                sha = r.json().get("sha")
        except Exception:
            pass
        payload = {
            "message": "Update bot_data.json [bot backup]",
            "content": base64.b64encode(content.encode()).decode(),
        }
        if sha:
            payload["sha"] = sha
        requests.put(url, headers=headers, json=payload, timeout=20)
    except Exception as e:
        print(f"[GitHub Backup] failed: {e}")


DATA = load_data()

DEFAULT_USER = {
    "points": 0, "codes": 0, "referred_by": None,
    "credited": False, "joined": False, "username": None,
    "phone": None, "codes_list": [],
}


def get_user(uid):
    uid = str(uid)
    if uid not in DATA["users"]:
        DATA["users"][uid] = dict(DEFAULT_USER)
    else:
        for k, v in DEFAULT_USER.items():
            if k not in DATA["users"][uid]:
                DATA["users"][uid][k] = v
    return DATA["users"][uid]


# ============================================================
# RAW TELEGRAM API (styled buttons)
# ============================================================
def _tg_request(method, payload):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        r = requests.post(url, json=payload, timeout=30)
        try:
            return r.json()
        except Exception:
            return {"ok": False, "raw": r.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _strip_icons(mk):
    if not isinstance(mk, dict):
        return mk
    out = {}
    for k, v in mk.items():
        if k in ("keyboard", "inline_keyboard"):
            out[k] = [[{kk: vv for kk, vv in b.items() if kk != "icon_custom_emoji_id"} for b in row] for row in v]
        else:
            out[k] = v
    return out


async def tg_send(chat_id, text, reply_markup=None, parse_mode="Markdown",
                  message_id=None, disable_web_page_preview=True):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if disable_web_page_preview:
        payload["disable_web_page_preview"] = True
    method = "editMessageText" if message_id else "sendMessage"
    if message_id:
        payload["message_id"] = message_id
    res = await asyncio.to_thread(_tg_request, method, payload)
    if not res.get("ok") and reply_markup and "icon" in str(res.get("description", "")).lower():
        payload["reply_markup"] = _strip_icons(reply_markup)
        res = await asyncio.to_thread(_tg_request, method, payload)
    return res


# ---- styled keyboard builders ----
def inline_kb(rows):
    return {"inline_keyboard": rows}


ICO = {
    "blue": "5373141891321699086", "red": "5370810157871667232",
    "green": "5471984997361523302", "cancel": "5382224089295365367",
}


def main_menu_kb(uid):
    rows = [
        [{"text": "🎟️ Generate Code", "callback_data": "gen", "style": "success", "icon_custom_emoji_id": ICO["green"]}],
        [
            {"text": "📋 My Codes", "callback_data": "mycodes", "style": "primary", "icon_custom_emoji_id": ICO["blue"]},
            {"text": "👥 My Referrals", "callback_data": "ref", "style": "primary", "icon_custom_emoji_id": ICO["blue"]},
        ],
        [
            {"text": "📊 My Stats", "callback_data": "stats", "style": "primary", "icon_custom_emoji_id": ICO["blue"]},
            {"text": "ℹ️ Help", "callback_data": "help", "style": "danger", "icon_custom_emoji_id": ICO["red"]},
        ],
    ]
    if is_admin(uid):
        rows.append([{"text": "🛡️ Admin Panel", "callback_data": "admin", "style": "danger", "icon_custom_emoji_id": ICO["red"]}])
    return inline_kb(rows)


def join_kb():
    return inline_kb([
        [{"text": "📢 Join Channel", "url": CHANNEL_LINK, "style": "primary", "icon_custom_emoji_id": ICO["blue"]}],
        [{"text": "👥 Join Group", "url": GROUP_LINK, "style": "primary", "icon_custom_emoji_id": ICO["blue"]}],
        [{"text": "✅ I Have Joined", "callback_data": "verify_join", "style": "success", "icon_custom_emoji_id": ICO["green"]}],
    ])


def referral_kb(link):
    return inline_kb([
        [{"text": "📤 Share Link", "url": f"https://t.me/share/url?url={link}&text=Get%20free%20Lenskart%20coupons!", "style": "primary", "icon_custom_emoji_id": ICO["blue"]}],
        [{"text": "🔙 Back", "callback_data": "back_menu", "style": "danger", "icon_custom_emoji_id": ICO["cancel"]}],
    ])


def no_points_kb():
    return inline_kb([
        [{"text": "👥 Get Referral Link", "callback_data": "ref", "style": "primary", "icon_custom_emoji_id": ICO["blue"]}],
        [{"text": "🔙 Back", "callback_data": "back_menu", "style": "danger", "icon_custom_emoji_id": ICO["cancel"]}],
    ])


def cancel_inline():
    return inline_kb([[{"text": "🔙 Back", "callback_data": "gen_cancel", "style": "danger", "icon_custom_emoji_id": ICO["cancel"]}]])


def back_kb():
    return inline_kb([[{"text": "🔙 Back", "callback_data": "back_menu", "style": "danger", "icon_custom_emoji_id": ICO["cancel"]}]])


def admin_panel_kb():
    return inline_kb([
        [{"text": "➕ Give Points to All", "callback_data": "admin_giveall", "style": "success", "icon_custom_emoji_id": ICO["green"]}],
        [{"text": "📢 Broadcast Msg", "callback_data": "admin_broadcast", "style": "primary", "icon_custom_emoji_id": ICO["blue"]}],
        [{"text": "🔄 Refresh", "callback_data": "admin", "style": "primary", "icon_custom_emoji_id": ICO["blue"]},
         {"text": "🔙 Close", "callback_data": "back_menu", "style": "danger", "icon_custom_emoji_id": ICO["cancel"]}],
    ])


# ============================================================
# LENSKART DEVICE LOGIC (cloudscraper bypasses Cloudflare)
# ============================================================
class LenskartDevice:
    def __init__(self, phone: str, phone_code: str = "+91"):
        self.phone = phone
        self.phone_code = phone_code
        self.brand = random.choice(BRANDS)
        self.model = random.choice(MODELS.get(self.brand, ["RMX3031"]))
        self.android_version = random.choice(ANDROID_VERSIONS)
        self.udid = uuid.uuid4().hex[:16]
        self.advertising_id = str(uuid.uuid4())
        self.build_version = f"TP1A.220905.00{random.randint(1,9)}"
        self.session_token = None
        self.auth_token = None
        self.user_id = None
        self.customer_type = "EXISTING"
        self.s = self._new_scraper()
        self.x_assertion = self._x_assertion()
        # Prime: collect Cloudflare cookies so the API call isn't challenged
        try:
            self.s.get(BASE, headers=self._headers(), timeout=20)
        except Exception:
            pass

    def _new_scraper(self):
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )

    def _x_assertion(self):
        d = f"{self.udid}:{self.advertising_id}:{self.brand}:{self.model}:{self.phone}"
        h = hashlib.sha256(d.encode()).digest()
        a = base64.b64encode(h).decode().replace("+", "-").replace("/", "_")
        while len(a) < 100:
            a += random.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        return a[:100]

    def _headers(self, extra=None):
        h = {
            "Content-Type": "application/json; charset=UTF-8",
            "api_key": "valyoo123",
            "x-api-client": "android",
            "x-app-version": "5.8.2 (260713001)",
            "appversion": "5.8.2 (260713001)",
            "X-Build-Version": "260713001",
            "x-country-code": "IN",
            "x-country-code-override": "IN",
            "x-accept-language": "en",
            "accept-language": "en",
            "x-customer-type": self.customer_type,
            "udid": self.udid,
            "uniqueId": self.advertising_id[:16],
            "brand": self.brand,
            "model": self.model,
            "x-b3-traceid": str(int(time.time() * 1000)),
            "User-Agent": f"Dalvik/2.1.0 (Linux; U; Android {self.android_version}; {self.model} Build/{self.build_version})",
            "Accept-Encoding": "gzip",
            "Connection": "Keep-Alive",
            "x-customer-phone": self.phone,
            "x-customer-phone-code": self.phone_code.replace("+", ""),
        }
        if self.session_token:
            h["x-session-token"] = self.session_token
        if self.x_assertion:
            h["x-assertion"] = self.x_assertion
        if extra:
            h.update(extra)
        return h

    def _post(self, path, body=None, params=None):
        url = f"{BASE}{path}"
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return self.s.post(url, headers=self._headers(), json=body, timeout=30)

    def create_session(self):
        last = None
        for attempt in range(4):
            try:
                r = self._post("/v2/sessions", {})
                last = (r.status_code, r.text[:120])
                if r.status_code == 200:
                    sid = r.json().get("result", {}).get("id")
                    if sid:
                        self.session_token = sid
                        return True
            except Exception as e:
                last = ("EXC", str(e)[:120])
            # Fresh scraper to beat a new Cloudflare challenge, then backoff
            self.s = self._new_scraper()
            time.sleep(1.5 * (attempt + 1))
        print(f"[create_session] FAILED after retries. last_response={last}")
        return False

    def send_otp(self):
        if not self.session_token:
            return None
        body = {"phoneCode": self.phone_code, "telephone": self.phone}
        for attempt in range(4):
            try:
                r = self._post("/v3/customers/sendOtp", body)
                if r.status_code == 200:
                    res = r.json().get("result") or {}
                    self.customer_type = "NEW" if res.get("isNewUser") else "EXISTING"
                    return res
            except Exception:
                pass
            self.s = self._new_scraper()
            time.sleep(1.5 * (attempt + 1))
        return None

    def verify_otp(self, code: str):
        body = {"code": code, "phoneCode": self.phone_code, "telephone": self.phone}
        r = self._post("/v2/customers/authenticate/mobile", body)
        if r.status_code == 200:
            res = r.json().get("result") or {}
            self.auth_token = res.get("token")
            self.user_id = res.get("user_id")
            if self.auth_token:
                self.session_token = self.auth_token
            return res
        return None

    def claim_reward(self, steps=REWARD_STEPS):
        def build_payload():
            DAY = 86400000
            ist = 5.5 * 3600 * 1000
            now_utc = int(time.time() * 1000)
            now_ist = now_utc + ist
            midnight_ist = (now_ist // DAY) * DAY
            midnight_utc = midnight_ist - ist
            counts = [0, 0, 0, 0, 0, 0, steps]
            payload = []
            for i in range(6, -1, -1):
                payload.append({"distance": 0.0, "steps": counts[i],
                                "timestamp": int(midnight_utc - i * DAY)})
            return payload

        r = self._post("/v2/customers/bff/campaign/eligibility", build_payload(),
                       {"campaignName": "run-for-frame"})
        try:
            data = r.json()
        except Exception:
            return {"ok": False, "message": f"Error {r.status_code}"}
        if r.status_code == 200:
            res = data.get("result") or {}
            voucher = res.get("giftVoucher")
            if voucher:
                return {"ok": True, "voucher": voucher, "tier": res.get("tier"),
                        "steps": res.get("steps"), "expiry": res.get("giftVoucherExpiryDate")}
            return {"ok": False, "message": res.get("message", "Reward not unlocked")}
        return {"ok": False, "message": f"Error {r.status_code}"}


# ============================================================
# STATES
# ============================================================
PHONE, OTP = range(2)
ADMIN_GIVE, ADMIN_BC = range(2, 4)
PENDING = {}


# ============================================================
# HELPERS
# ============================================================
async def check_membership(user_id, context):
    try:
        c = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        g = await context.bot.get_chat_member(GROUP_USERNAME, user_id)
        return c.status in ("member", "administrator", "creator") and \
               g.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def ask_join(chat_id, message_id=None):
    text = (
        "🔒 *Access Locked*\n\n"
        "To use this bot you must join our official *Channel* and *Group*.\n\n"
        f"📢 Channel: {CHANNEL_USERNAME}\n"
        f"👥 Group: {GROUP_USERNAME}\n\n"
        "Join both, then press ✅ below."
    )
    await tg_send(chat_id, text, reply_markup=join_kb(), message_id=message_id)


async def show_menu(chat_id, uid, message_id=None):
    u = get_user(uid)
    text = (
        "╔══════════════════════════╗\n"
        "║  🎟️ *RUN FOR FRAME* — COUPON HUB 🎟️  ║\n"
        "╚══════════════════════════╝\n\n"
        "👋 Welcome, coupon hunter!\n\n"
        f"💎 *Points:* `{u['points']}`\n"
        f"🎟️ *Codes Left:* `{u['points']}`\n"
        f"📦 *Codes Generated:* `{u['codes']}`\n\n"
        "🔗 Refer friends: 1 Referral = 1 Point = 1 Code 🎯\n\n"
        "_Made By Viediet_"
    )
    await tg_send(chat_id, text, reply_markup=main_menu_kb(uid), message_id=message_id)


async def credit_referrer(uid):
    u = get_user(uid)
    if u["referred_by"] and not u["credited"]:
        ref = u["referred_by"]
        if ref in DATA["users"]:
            DATA["users"][ref]["points"] += 1
            DATA["referrals"].setdefault(ref, []).append(str(uid))
        u["credited"] = True
        save_data(DATA)


def admin_stats_text():
    users = DATA["users"]
    total_users = len(users)
    total_codes = sum(u["codes"] for u in users.values())
    total_points = sum(u["points"] for u in users.values())
    total_refs = sum(len(v) for v in DATA["referrals"].values())
    return (
        "🛡️ *ADMIN PANEL*\n\n"
        f"👥 Total Users: `{total_users}`\n"
        f"🎟️ Total Codes Generated: `{total_codes}`\n"
        f"💎 Points in Circulation: `{total_points}`\n"
        f"🔗 Total Referrals: `{total_refs}`\n\n"
        "Choose an action below 👇"
    )


# ============================================================
# HANDLERS
# ============================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)
    u = get_user(uid)
    u["username"] = user.username or user.first_name
    save_data(DATA)

    if context.args and context.args[0].startswith("ref_"):
        ref = context.args[0][4:]
        if ref != uid and u["referred_by"] is None:
            u["referred_by"] = ref
            save_data(DATA)

    chat_id = update.effective_chat.id
    if not await check_membership(uid, context):
        await ask_join(chat_id)
        return

    u["joined"] = True
    await credit_referrer(uid)
    await show_menu(chat_id, uid)


async def verify_join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = str(q.from_user.id)
    if not await check_membership(uid, context):
        await q.answer("❌ You have not joined both yet!", show_alert=True)
        return
    u = get_user(uid)
    u["joined"] = True
    await credit_referrer(uid)
    await show_menu(q.message.chat_id, uid, message_id=q.message.message_id)


async def back_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_menu(q.message.chat_id, str(q.from_user.id), message_id=q.message.message_id)


async def referrals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, q=None):
    uid = str(update.effective_user.id) if not q else str(q.from_user.id)
    chat_id = update.effective_chat.id if not q else q.message.chat_id
    msg_id = None if not q else q.message.message_id
    u = get_user(uid)
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{uid}"
    count = len(DATA["referrals"].get(uid, []))
    text = (
        "👥 *Your Referral Panel*\n\n"
        f"🔗 *Your Link:*\n`{link}`\n\n"
        f"👤 Total Referrals: `{count}`\n"
        f"💎 Points: `{u['points']}`\n\n"
        "📢 Share your link! Every join gives +1 point.\n"
        "1 Point = 1 Free Code 🎟️"
    )
    await tg_send(chat_id, text, reply_markup=referral_kb(link), message_id=msg_id)


async def ref_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await referrals_cmd(update, context, q)


async def my_codes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, q=None):
    if q:
        await q.answer()
    uid = str(update.effective_user.id) if not q else str(q.from_user.id)
    chat_id = update.effective_chat.id if not q else q.message.chat_id
    msg_id = None if not q else q.message.message_id
    u = get_user(uid)
    codes = u.get("codes_list", [])
    if not codes:
        text = "📋 *My Codes*\n\n❌ You haven't generated any coupon yet.\nUse 🎟️ Generate Code to get one!"
    else:
        lines = ["📋 *My Generated Coupons*\n"]
        for i, c in enumerate(codes, 1):
            lines.append(
                f"\n*{i}. 🎫 Voucher:* `{c.get('voucher','-')}`\n"
                f"   🏆 Tier: `{c.get('tier','-')}`\n"
                f"   ⏰ Expiry: `{c.get('expiry','-')}`\n"
                f"   📱 Phone: `{c.get('phone','-')}`\n"
                f"   🕒 {c.get('time','-')}"
            )
        text = "\n".join(lines)
    kb = inline_kb([[{"text": "🔙 Back", "callback_data": "back_menu", "style": "danger", "icon_custom_emoji_id": ICO["cancel"]}]])
    await tg_send(chat_id, text, reply_markup=kb, message_id=msg_id)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, q=None):
    if q:
        await q.answer()
    uid = str(update.effective_user.id) if not q else str(q.from_user.id)
    chat_id = update.effective_chat.id if not q else q.message.chat_id
    msg_id = None if not q else q.message.message_id
    u = get_user(uid)
    count = len(DATA["referrals"].get(uid, []))
    text = (
        "📊 *Your Stats*\n\n"
        f"💎 Points: `{u['points']}`\n"
        f"🎟️ Codes Available: `{u['points']}`\n"
        f"📦 Codes Generated: `{u['codes']}`\n"
        f"👥 Referrals: `{count}`\n\n"
        "_Made By Viediet_"
    )
    await tg_send(chat_id, text, reply_markup=back_kb(), message_id=msg_id)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, q=None):
    if q:
        await q.answer()
    chat_id = update.effective_chat.id if not q else q.message.chat_id
    msg_id = None if not q else q.message.message_id
    uid = str(update.effective_user.id) if not q else str(q.from_user.id)
    text = (
        "ℹ️ *Help*\n\n"
        "🎟️ *Generate Code* — Use 1 point to generate a Lenskart coupon.\n"
        "👥 *My Referrals* — Get your invite link.\n"
        "📊 *My Stats* — View points & codes.\n\n"
        "📢 Must stay joined to Channel & Group.\n"
        "🔗 Refer = +1 point = +1 code.\n\n"
        "_Made By Viediet_"
    )
    await tg_send(chat_id, text, reply_markup=back_kb(), message_id=msg_id)


# ---- Generate flow ----
async def generate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
    uid = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    if not await check_membership(uid, context):
        await ask_join(chat_id)
        return ConversationHandler.END
    u = get_user(uid)
    if u["points"] <= 0:
        await tg_send(chat_id,
                      "⚠️ *No Points Left!*\n\nYou need *1 point* to generate a code.\nRefer a friend to earn points 💎",
                      reply_markup=no_points_kb())
        return ConversationHandler.END
    mid = q.message.message_id if q else None
    PENDING[str(uid)] = {"dev": None, "mid": mid}
    await tg_send(chat_id,
                  "📱 *Enter your phone number* (10 digit, e.g. 9876543210)\n\nWe'll send an OTP to verify. 🔐",
                  reply_markup=cancel_inline(), message_id=mid)
    return PHONE


async def gen_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    entry = PENDING.pop(str(q.from_user.id), None)
    mid = entry.get("mid") if entry else q.message.message_id
    await show_menu(q.message.chat_id, str(q.from_user.id), message_id=mid)
    return ConversationHandler.END


async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    entry = PENDING.get(uid)
    if not entry:
        await tg_send(update.effective_chat.id, "⏳ Session expired. Start again with 🎟️ Generate Code.")
        return ConversationHandler.END
    phone = update.message.text.strip()
    if not phone.isdigit() or len(phone) != 10:
        await tg_send(update.effective_chat.id, "❌ Invalid number. Send 10 digit mobile number.")
        return PHONE
    dev = LenskartDevice(phone)
    ok = await asyncio.to_thread(dev.create_session)
    if not ok:
        PENDING.pop(uid, None)
        await tg_send(update.effective_chat.id, "❌ Could not create session. Try again later.",
                      reply_markup=cancel_inline(), message_id=entry["mid"])
        return ConversationHandler.END
    sent = await asyncio.to_thread(dev.send_otp)
    if not sent:
        PENDING.pop(uid, None)
        await tg_send(update.effective_chat.id, "❌ Failed to send OTP. Try again.",
                      reply_markup=cancel_inline(), message_id=entry["mid"])
        return ConversationHandler.END
    entry["dev"] = dev
    await tg_send(update.effective_chat.id,
                  f"✅ OTP sent to `{phone}`\n\n🔑 *Enter the OTP* you received:",
                  reply_markup=cancel_inline(), message_id=entry["mid"])
    return OTP


async def otp_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    entry = PENDING.get(uid)
    if not entry or not entry.get("dev"):
        await tg_send(update.effective_chat.id, "⏳ Session expired. Start again with 🎟️ Generate Code.")
        return ConversationHandler.END
    dev = entry["dev"]
    mid = entry["mid"]
    code = update.message.text.strip()
    if not code.isdigit():
        await tg_send(update.effective_chat.id, "❌ OTP must be numbers.", message_id=mid)
        return OTP

    await tg_send(update.effective_chat.id, "⏳ *Verifying & generating your coupon...* 🔄", message_id=mid)

    verify = await asyncio.to_thread(dev.verify_otp, code)
    if not verify:
        await tg_send(update.effective_chat.id, "❌ OTP verification failed. Restart 🎟️ Generate Code.",
                      reply_markup=cancel_inline(), message_id=mid)
        PENDING.pop(uid, None)
        return ConversationHandler.END

    result = await asyncio.to_thread(dev.claim_reward)

    u = get_user(uid)
    if result.get("ok"):
        u["points"] -= 1
        u["codes"] += 1
        u["phone"] = dev.phone
        exp = ""
        if result.get("expiry"):
            try:
                exp = datetime.fromtimestamp(result["expiry"] / 1000).strftime("%d %b %Y")
            except Exception:
                pass
        u["codes_list"].append({
            "voucher": result.get("voucher"),
            "tier": result.get("tier"),
            "steps": result.get("steps"),
            "expiry": exp or "N/A",
            "phone": dev.phone,
            "time": datetime.now().strftime("%d %b %Y %H:%M"),
        })
        save_data(DATA)
        PENDING.pop(uid, None)
        text = (
            "🎉 *COUPON UNLOCKED!*\n\n"
            f"🏆 Tier: `{result.get('tier','-')}`\n"
            f"🎫 *Voucher:* `{result.get('voucher')}`\n"
            f"📊 Steps: `{result.get('steps','-')}`\n"
            f"⏰ Expiry: `{exp or 'N/A'}`\n\n"
            f"💎 Points Left: `{u['points']}`\n\n"
            "_Made By Viediet_"
        )
        await tg_send(update.effective_chat.id, text, reply_markup=main_menu_kb(uid), message_id=mid)
    else:
        await tg_send(update.effective_chat.id,
                      f"⚠️ *Could not generate coupon*\n\nReason: {result.get('message','Unknown')}\n\n"
                      "Your point was NOT deducted. Try again 🎟️",
                      reply_markup=cancel_inline(), message_id=mid)
        PENDING.pop(uid, None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    PENDING.pop(str(update.effective_user.id), None)
    await show_menu(update.effective_chat.id, str(update.effective_user.id))
    return ConversationHandler.END


# ---- Admin panel ----
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = str(q.from_user.id)
    if not is_admin(uid):
        return
    await tg_send(q.message.chat_id, admin_stats_text(),
                  reply_markup=admin_panel_kb(), message_id=q.message.message_id)


async def admin_give_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END
    await tg_send(q.message.chat_id,
                  "➕ *Give Points to ALL users*\n\nSend the amount (e.g. `5`):",
                  reply_markup=cancel_inline(), message_id=q.message.message_id)
    return ADMIN_GIVE


async def admin_give_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = int(update.message.text.strip())
    except ValueError:
        await tg_send(update.effective_chat.id, "❌ Send a valid number.")
        return ADMIN_GIVE
    for u in DATA["users"].values():
        u["points"] += amt
    save_data(DATA)
    await tg_send(update.effective_chat.id,
                  f"✅ Added `{amt}` points to ALL users!",
                  reply_markup=admin_panel_kb())
    return ConversationHandler.END


async def admin_bc_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return ConversationHandler.END
    await tg_send(q.message.chat_id,
                  "📢 *Broadcast*\n\nSend the message to send to all users:",
                  reply_markup=cancel_inline(), message_id=q.message.message_id)
    return ADMIN_BC


async def admin_bc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    sent = 0
    failed = 0
    for uid in list(DATA["users"].keys()):
        res = await tg_send(uid, f"📢 *Broadcast*\n\n{text}")
        if res.get("ok"):
            sent += 1
        else:
            failed += 1
    await tg_send(update.effective_chat.id,
                  f"📢 Broadcast done!\n✅ Sent: `{sent}`\n❌ Failed: `{failed}`",
                  reply_markup=admin_panel_kb())
    return ConversationHandler.END


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Fallback for plain text when no conversation is active
    await tg_send(update.effective_chat.id,
                  "👇 Use the buttons below to navigate.",
                  reply_markup=main_menu_kb(str(update.effective_user.id)))


async def err_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        print("⚠️ Telegram Conflict: two instances are using this bot token. "
              "Run ONLY ONE instance (stop local bot / set Railway replicas to 1).")
        return
    print(f"Unhandled error: {err}")


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", lambda u, c: help_cmd(u, c)))
    app.add_handler(CommandHandler("admin", lambda u, c: admin_panel(u, c)))

    app.add_handler(CallbackQueryHandler(verify_join_cb, pattern="^verify_join$"))
    app.add_handler(CallbackQueryHandler(ref_panel_cb, pattern="^ref$"))
    app.add_handler(CallbackQueryHandler(my_codes_cmd, pattern="^mycodes$"))
    app.add_handler(CallbackQueryHandler(back_menu_cb, pattern="^back_menu$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin$"))
    app.add_handler(CallbackQueryHandler(stats_cmd, pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(help_cmd, pattern="^help$"))

    gen_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(generate_start, pattern="^gen$")],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_received),
                    CallbackQueryHandler(gen_cancel_cb, pattern="^gen_cancel$")],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_received),
                  CallbackQueryHandler(gen_cancel_cb, pattern="^gen_cancel$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(gen_conv)

    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_give_entry, pattern="^admin_giveall$"),
                      CallbackQueryHandler(admin_bc_entry, pattern="^admin_broadcast$")],
        states={
            ADMIN_GIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_give_amount),
                         CallbackQueryHandler(gen_cancel_cb, pattern="^gen_cancel$")],
            ADMIN_BC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_bc_text),
                       CallbackQueryHandler(gen_cancel_cb, pattern="^gen_cancel$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(admin_conv)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    app.add_error_handler(err_handler)

    # Periodic GitHub backup every 5 minutes (background thread, no job-queue needed)
    if GITHUB_TOKEN:
        def _github_backup_loop():
            while True:
                time.sleep(300)
                try:
                    github_push()
                except Exception:
                    pass
        threading.Thread(target=_github_backup_loop, daemon=True).start()

    print("🤖 Bot started... Made By Viediet")

    public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    webhook_url = os.getenv("WEBHOOK_URL")
    use_webhook = bool(public_domain or webhook_url)

    if use_webhook:
        base = webhook_url or f"https://{public_domain}"
        full = f"{base}/{BOT_TOKEN}"
        print(f"🌐 Webhook mode: {base}")
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", "8080")),
            url_path=BOT_TOKEN,
            webhook_url=full,
        )
    else:
        while True:
            try:
                app.run_polling(drop_pending_updates=True, close_loop=False)
                break
            except Conflict:
                print("⚠️ Conflict: another bot instance is already polling this token. "
                      "Make sure ONLY ONE instance is running (stop local bot if deployed, "
                      "or set Railway replicas to 1). Retrying in 15s...")
                time.sleep(15)
            except KeyboardInterrupt:
                break


if __name__ == "__main__":
    main()
