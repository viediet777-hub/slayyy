#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import base64
import hashlib
import hmac
import random
import string
import urllib.parse
import asyncio
import sqlite3
import logging
from datetime import datetime
from typing import Tuple, Optional
from pathlib import Path

import aiohttp
from aiohttp import ClientSession, FormData

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, ContextTypes
)

# ==================== CONFIG (Railway Environment Variables) ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set.")

ADMIN_IDS = [int(os.environ.get("ADMIN_ID", 1364476174))]

CHANNEL_USERNAME = "viedietlooters"
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", -1003872664875))
SUPPORT_LINK = "https://t.me/viedietlooterschat"

# ✅ FIXED: Using Kommodo image URL
IMAGE_URL = "https://kommodo.ai/i/oUdlNotAwhZLSfsANSkx"
IMAGE_NAME = "photo_2026-07-28_11-11-35.jpg"

BASE_URL = "https://slayyourplaypromo.in"

# ==================== DATABASE (Persistent Storage) ====================
DB_PATH = os.environ.get("DB_PATH", "/app/data/slay_bot_v3.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SESSION_FILE = os.path.join(os.path.dirname(DB_PATH), "aiohttp_session_cookies.json")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# SIGNATURE FUNCTION
# ============================================================
def build_signed_data(payload: dict, data_key: str) -> str:
    ordered = {}
    for k, v in payload.items():
        if k != "t":
            ordered[k] = v
    ordered["t"] = payload["t"]

    payload_str = json.dumps(ordered, separators=(',', ':'), ensure_ascii=False)
    u = base64.b64encode(str(payload["t"]).encode()).decode()
    a = base64.b64encode(payload_str.encode()).decode()

    hmac_key = data_key[4:18].encode()
    hex_sig = hmac.new(hmac_key, f"{u}.{a}".encode(), hashlib.sha256).hexdigest()

    f2 = base64.b64encode(hex_sig.encode()).decode()

    m = random.randint(1, 6)
    k2 = random.randint(2, 8)
    alpha = string.ascii_letters + string.digits
    h_rand = ''.join(random.choice(alpha) for _ in range(k2))
    g = f"{k2}{m}{f2[0:m]}{h_rand}{f2[m:]}"

    return f"userKey={payload['userKey']}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"


def decode_resp(text: str) -> dict:
    data = json.loads(text)
    if "resp" in data:
        s = data["resp"]
        pad = 4 - len(s) % 4
        if pad != 4:
            s += "=" * pad
        return json.loads(base64.b64decode(s).decode())
    return data


# ============================================================
# DATABASE
# ============================================================
conn_lock = asyncio.Lock()


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            mobile TEXT DEFAULT '',
            upi TEXT DEFAULT '',
            user_key TEXT DEFAULT '',
            data_key TEXT DEFAULT '',
            access_token TEXT DEFAULT '',
            referrals INTEGER DEFAULT 0,
            credits INTEGER DEFAULT 1,
            completed_processes INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT NULL,
            joined_channel INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    return db


async def db_user(telegram_id: int) -> Optional[dict]:
    async with conn_lock:
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        db.close()
        return dict(row) if row else None


async def db_upsert(telegram_id: int, **kwargs):
    async with conn_lock:
        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        if existing:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values()) + [telegram_id]
            db.execute(f"UPDATE users SET {sets} WHERE telegram_id=?", vals)
        else:
            keys = ["telegram_id"] + list(kwargs.keys())
            placeholders = ", ".join("?" for _ in keys)
            vals = [telegram_id] + list(kwargs.values())
            db.execute(f"INSERT INTO users ({', '.join(keys)}) VALUES ({placeholders})", vals)
        db.commit()
        db.close()


async def db_add_referral(referrer_id: int):
    async with conn_lock:
        db = get_db()
        db.execute("UPDATE users SET referrals=referrals+1, credits=credits+1 WHERE telegram_id=?", (referrer_id,))
        db.commit()
        db.close()


# ============================================================
# AIOHTTP SESSION MANAGEMENT
# ============================================================
_http_session: Optional[ClientSession] = None


async def get_http_session() -> ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        jar = aiohttp.CookieJar(unsafe=True)
        _http_session = aiohttp.ClientSession(
            cookie_jar=jar,
            headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        )
        if os.path.exists(SESSION_FILE):
            try:
                with open(SESSION_FILE) as f:
                    cookies = json.load(f)
                for domain, cdata in cookies.items():
                    for name, attrs in cdata.items():
                        jar._cookies[domain][name] = aiohttp.Cookie(
                            name=name, value=attrs.get('value', ''),
                            domain=domain, path=attrs.get('path', '/')
                        )
            except Exception as e:
                logger.warning(f"Could not load cookies: {e}")
    return _http_session


async def save_cookies():
    global _http_session
    if _http_session and not _http_session.closed:
        try:
            cookies = {}
            for domain, domain_cookies in _http_session.cookie_jar._cookies.items():
                cookies[str(domain)] = {}
                for name, cookie in domain_cookies.items():
                    cookies[str(domain)][name] = {
                        'value': cookie.value,
                        'path': cookie['path']
                    }
            with open(SESSION_FILE, 'w') as f:
                json.dump(cookies, f)
        except Exception as e:
            logger.warning(f"Could not save cookies: {e}")


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


# ============================================================
# API HELPER
# ============================================================
async def api_init() -> Tuple[bool, dict]:
    session = await get_http_session()
    url = f"{BASE_URL}/api/users"
    body = {
        "ipInfo": {
            "as": "AS45916 Gujarat Telelink Pvt Ltd",
            "city": "Indore",
            "country": "India",
            "countryCode": "IN",
            "isp": "Gujarat Telelink Pvt Ltd",
            "lat": 22.717,
            "lon": 75.8337,
            "org": "Gtpl Broadband Pvt. Ltd.",
            "query": "43.243.36.233",
            "region": "MP",
            "regionName": "Madhya Pradesh",
            "status": "success",
            "timezone": "Asia/Kolkata",
            "zip": "452009"
        }
    }
    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
        'origin': BASE_URL,
        'referer': f'{BASE_URL}/',
        'x-requested-with': 'XMLHttpRequest',
    }
    try:
        async with session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
            await save_cookies()
            if resp.status == 200:
                data = decode_resp(text)
                return True, data
            return False, {"message": f"HTTP {resp.status}", "detail": text}
    except Exception as e:
        logger.error(f"INIT failed: {e}")
        return False, {"message": str(e)}


async def make_signed_request(
    endpoint: str,
    payload: dict,
    data_key: str,
    user_key,
    referer: str = '/',
    files: dict = None,
    jwt_token: str = None
) -> Tuple[bool, dict]:
    session = await get_http_session()
    url = f"{BASE_URL}{endpoint}".replace('{userKey}', str(user_key))
    t = _now_ms()

    payload['userKey'] = int(user_key)
    payload['t'] = t

    signed_data = build_signed_data(payload, data_key)

    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'origin': BASE_URL,
        'referer': f'{BASE_URL}{referer}',
        'user-agent': 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36',
        'x-requested-with': 'XMLHttpRequest',
    }
    if jwt_token:
        headers['authorization'] = f'Bearer {jwt_token}'

    request_url = f"{url}?t={t}"
    logger.info(f"SIGNED POST {endpoint}")

    try:
        if files:
            form = FormData()
            form.add_field('userKey', str(user_key))
            form.add_field('data', signed_data)
            for field_name, (filename, content, content_type) in files.items():
                form.add_field(field_name, content, filename=filename, content_type=content_type)
            async with session.post(request_url, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                text = await resp.text()
                try:
                    decoded = decode_resp(text)
                except Exception:
                    decoded = {"statusCode": resp.status, "message": text[:200]}
                status_ok = decoded.get('statusCode', 400) in (200, 201, 202)
                return status_ok, decoded
        else:
            headers['content-type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
            async with session.post(request_url, data=signed_data, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                text = await resp.text()
                try:
                    decoded = decode_resp(text)
                except Exception:
                    decoded = {"statusCode": resp.status, "message": text[:200]}
                status_ok = decoded.get('statusCode', 400) in (200, 201, 202)
                return status_ok, decoded
    except Exception as e:
        logger.error(f"{endpoint} error: {e}")
        return False, {"message": str(e)}


# ============================================================
# API STEP FUNCTIONS
# ============================================================
async def click_track(user_key, data_key) -> Tuple[bool, dict]:
    payload = {"smoker": "yes"}
    return await make_signed_request('/api/users/clickTrack/{userKey}', payload, data_key, user_key, referer='/')


async def register_user(phone: str, user_key, data_key) -> Tuple[bool, dict]:
    payload = {"mobile": phone, "limit": ""}
    return await make_signed_request('/api/users/register/{userKey}', payload, data_key, user_key, referer='/register')


async def verify_otp(otp: str, user_key, data_key, jwt_token: str = None) -> Tuple[bool, dict]:
    payload = {"otp": otp}
    return await make_signed_request('/api/users/verifyOTP/{userKey}', payload, data_key, user_key, referer='/login', jwt_token=jwt_token)


async def select_pack(user_key, data_key, jwt_token) -> Tuple[bool, dict]:
    payload = {"pack": "single"}
    return await make_signed_request('/api/users/selectPack/{userKey}', payload, data_key, user_key, referer='/dashboard', jwt_token=jwt_token)


async def select_vibe(user_key, data_key, jwt_token) -> Tuple[bool, dict]:
    payload = {"vibe": "soft savage"}
    return await make_signed_request('/api/users/selectVibe/{userKey}', payload, data_key, user_key, referer='/dashboard', jwt_token=jwt_token)


async def upload_image(image_bytes: bytes, filename: str, user_key, data_key, jwt_token) -> Tuple[bool, dict]:
    payload = {}
    files = {'media': (filename, image_bytes, 'image/jpeg')}
    return await make_signed_request('/api/users/uploadImage/{userKey}', payload, data_key, user_key, referer='/take-stick-photo', files=files, jwt_token=jwt_token)


async def submit_upi(upi_number: str, user_key, data_key, jwt_token) -> Tuple[bool, dict]:
    payload = {"upiNo": upi_number}
    return await make_signed_request('/api/users/getUpiNo/{userKey}', payload, data_key, user_key, referer='/dashboard', jwt_token=jwt_token)


async def get_pack_progress(user_key, data_key, jwt_token) -> Tuple[bool, dict]:
    payload = {}
    return await make_signed_request('/api/users/getPackProgress/{userKey}', payload, data_key, user_key, referer='/dashboard', jwt_token=jwt_token)


async def get_stick_progress(user_key, data_key, jwt_token) -> Tuple[bool, dict]:
    payload = {}
    return await make_signed_request('/api/users/getStickProgress/{userKey}', payload, data_key, user_key, referer='/dashboard', jwt_token=jwt_token)


async def download_image() -> Tuple[Optional[bytes], str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(IMAGE_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return data, IMAGE_NAME
                return None, ""
    except Exception as e:
        logger.error(f"Image download failed: {e}")
        return None, ""


# ============================================================
# KEYBOARDS
# ============================================================
main_keyboard = ReplyKeyboardMarkup([
    [KeyboardButton("🏠 Start Process")],
    [KeyboardButton("👥 Refer & Earn"), KeyboardButton("📊 Dashboard")],
    [KeyboardButton("📞 Support")],
], resize_keyboard=True)

cancel_keyboard = ReplyKeyboardMarkup([
    [KeyboardButton("❌ Cancel")]
], resize_keyboard=True)


# ============================================================
# CONVERSATION STATES
# ============================================================
ASK_MOBILE, ASK_OTP, ASK_UPI = range(3)


# ============================================================
# HELPERS
# ============================================================
async def is_channel_member(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ('member', 'administrator', 'creator')
    except Exception as e:
        logger.warning(f"Channel check failed for {user_id}: {e}")
        return False


async def validate_mobile(phone: str) -> bool:
    return len(phone) == 10 and phone.isdigit()


async def validate_otp(code: str) -> bool:
    return len(code) == 6 and code.isdigit()


async def show_main_menu(update: Update, text: str = "Choose an option:"):
    await update.message.reply_text(text, reply_markup=main_keyboard)


# ============================================================
# COMMAND HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tid = user.id
    args = context.args

    existing = await db_user(tid)
    if existing is None:
        is_admin = 1 if tid in ADMIN_IDS else 0
        await db_upsert(tid, name=user.full_name or "", username=user.username or "", is_admin=is_admin)
        if args and args[0].isdigit():
            referrer_id = int(args[0])
            if referrer_id != tid:
                await db_add_referral(referrer_id)
                await db_upsert(tid, referred_by=referrer_id)

    member = await is_channel_member(context.bot, tid)
    if not member:
        await update.message.reply_text(
            f"⚠️ You must join @{CHANNEL_USERNAME} to use this bot.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]
            ])
        )
        return

    await db_upsert(tid, joined_channel=1)
    await show_main_menu(update, f"👋 Welcome {user.first_name}!")


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📞 Join our support group:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Open Group", url=SUPPORT_LINK)]
        ])
    )


async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    u = await db_user(tid)
    if not u:
        await show_main_menu(update, "No data found. Use /start first.")
        return
    text = (
        f"📊 <b>Your Dashboard</b>\n\n"
        f"👤 Name: {u['name']}\n"
        f"🆔 ID: <code>{u['telegram_id']}</code>\n"
        f"📱 Mobile: {u['mobile'] or 'Not set'}\n"
        f"💳 UPI: {u['upi'] or 'Not set'}\n"
        f"👥 Referrals: {u['referrals']}\n"
        f"💎 Credits: {u['credits']}\n"
        f"✅ Completed: {u['completed_processes']}\n"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard)


async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    bot_user = await context.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={tid}"
    text = (
        f"👥 <b>Refer & Earn</b>\n\n"
        f"Share your referral link and earn <b>+1 Credit</b> for every new user who joins!\n\n"
        f"Your link:\n<code>{link}</code>\n\n"
        f"Each Credit = 1 Process = ₹20"
    )
    await update.message.reply_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={link}&text=Join%20this%20bot!")],
        ])
    )
    await show_main_menu(update)


# ============================================================
# START PROCESS HANDLER
# ============================================================
async def start_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    
    member = await is_channel_member(context.bot, tid)
    if not member:
        await update.message.reply_text(
            f"⚠️ You must join @{CHANNEL_USERNAME} first!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")]
            ])
        )
        return ConversationHandler.END

    u = await db_user(tid)
    
    # Check if admin - unlimited access
    is_admin = tid in ADMIN_IDS
    
    credits = u['credits'] if u else 1
    if credits < 1 and not is_admin:
        await update.message.reply_text(
            "❌ You have no credits left.\n\n"
            "Earn credits by referring friends!\n"
            "1 Referral = 1 Credit = 1 Process = ₹20",
            reply_markup=main_keyboard
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📱 Enter your 10-digit mobile number:",
        reply_markup=cancel_keyboard
    )
    return ASK_MOBILE


async def ask_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Cancel":
        await show_main_menu(update, "Cancelled.")
        return ConversationHandler.END

    if not await validate_mobile(text):
        await update.message.reply_text("❌ Invalid number. Enter a 10-digit mobile number:")
        return ASK_MOBILE

    tid = update.effective_user.id
    phone = text
    context.user_data['phone'] = phone

    await update.message.reply_text("⏳ Initializing session...")

    ok, init_data = await api_init()
    if not ok or init_data.get('statusCode') != 200:
        await update.message.reply_text(f"❌ Init failed: {init_data.get('message', 'Unknown error')}", reply_markup=main_keyboard)
        return ConversationHandler.END

    user_key = init_data['userKey']
    data_key = init_data['dataKey']
    context.user_data['userKey'] = user_key
    context.user_data['dataKey'] = data_key

    await update.message.reply_text("⏳ Tracking...")
    await click_track(user_key, data_key)

    await update.message.reply_text("⏳ Sending OTP...")
    reg_ok, reg_data = await register_user(phone, user_key, data_key)
    if not reg_ok:
        msg = reg_data.get('message', 'Unknown error')
        await update.message.reply_text(f"❌ Registration failed: {msg}", reply_markup=main_keyboard)
        return ConversationHandler.END

    await update.message.reply_text("✅ OTP sent! Enter the 6-digit OTP:")
    return ASK_OTP


async def ask_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Cancel":
        await show_main_menu(update, "Cancelled.")
        return ConversationHandler.END

    if not await validate_otp(text):
        await update.message.reply_text("❌ Invalid OTP. Enter a 6-digit code:")
        return ASK_OTP

    otp = text
    user_key = context.user_data['userKey']
    data_key = context.user_data['dataKey']

    await update.message.reply_text("⏳ Verifying OTP...")
    ok, data = await verify_otp(otp, user_key, data_key)
    if not ok:
        msg = data.get('message', 'Unknown error')
        await update.message.reply_text(f"❌ OTP verification failed: {msg}", reply_markup=main_keyboard)
        return ConversationHandler.END

    access_token = data.get('accessToken') or data.get('data', {}).get('accessToken', '')
    if not access_token:
        await update.message.reply_text("❌ No access token received.", reply_markup=main_keyboard)
        return ConversationHandler.END

    context.user_data['jwt'] = access_token
    tid = update.effective_user.id
    await db_upsert(tid, mobile=context.user_data['phone'], user_key=str(user_key), data_key=data_key, access_token=access_token)

    await update.message.reply_text("✅ OTP verified! Setting up your process...")

    # Auto steps
    await update.message.reply_text("⏳ Selecting pack...")
    ok, pdata = await select_pack(user_key, data_key, access_token)
    if not ok:
        await update.message.reply_text(f"⚠️ Pack: {pdata.get('message', 'failed')}")

    await update.message.reply_text("⏳ Selecting vibe...")
    ok, vdata = await select_vibe(user_key, data_key, access_token)
    if not ok:
        await update.message.reply_text(f"⚠️ Vibe: {vdata.get('message', 'failed')}")

    await update.message.reply_text("⏳ Uploading image...")
    img_bytes, img_name = await download_image()
    if img_bytes:
        ok, img_data = await upload_image(img_bytes, img_name, user_key, data_key, access_token)
        if ok:
            await update.message.reply_text("✅ Image uploaded successfully!")
        else:
            await update.message.reply_text(f"⚠️ Image upload: {img_data.get('message', 'failed')}")
    else:
        await update.message.reply_text("⚠️ Could not download image.")

    await update.message.reply_text(
        "💳 Enter your UPI-registered mobile number (10 digits):",
        reply_markup=cancel_keyboard
    )
    return ASK_UPI


async def ask_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Cancel":
        await show_main_menu(update, "Cancelled.")
        return ConversationHandler.END

    if not await validate_mobile(text):
        await update.message.reply_text("❌ Invalid number. Enter a 10-digit UPI mobile number:")
        return ASK_UPI

    upi_number = text
    user_key = context.user_data['userKey']
    data_key = context.user_data['dataKey']
    jwt = context.user_data['jwt']
    tid = update.effective_user.id

    await update.message.reply_text("⏳ Submitting UPI...")
    ok, upi_data = await submit_upi(upi_number, user_key, data_key, jwt)
    if not ok:
        await update.message.reply_text("⏳ Retrying UPI submission...")
        ok, upi_data = await submit_upi(upi_number, user_key, data_key, jwt)

    if ok:
        await db_upsert(tid, upi=upi_number)
        await update.message.reply_text(f"✅ UPI submitted!")
    else:
        msg = upi_data.get('message', 'Unknown error')
        await update.message.reply_text(f"⚠️ UPI: {msg}")

    # Deduct credit (skip for admin)
    is_admin = tid in ADMIN_IDS
    if not is_admin:
        u = await db_user(tid)
        new_credits = max(0, (u['credits'] if u else 1) - 1)
        new_completed = (u['completed_processes'] if u else 0) + 1
        await db_upsert(tid, credits=new_credits, completed_processes=new_completed)
    else:
        u = await db_user(tid)
        new_completed = (u['completed_processes'] if u else 0) + 1
        await db_upsert(tid, completed_processes=new_completed)

    await update.message.reply_text(
        f"✅ **Process Completed!**\n\n"
        f"💸 Your payment will be credited to your UPI within 24-48 hours.\n\n"
        f"📱 UPI: {upi_number}\n"
        f"💰 Amount: ₹20\n"
        f"📅 Date: {datetime.now().strftime('%d-%m-%Y %H:%M')}",
        parse_mode="HTML"
    )

    await show_main_menu(update, "✅ Process finished!")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update, "Cancelled.")
    return ConversationHandler.END


# ============================================================
# ADMIN COMMANDS
# ============================================================
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    async with conn_lock:
        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        credits = db.execute("SELECT SUM(credits) FROM users").fetchone()[0] or 0
        completed = db.execute("SELECT SUM(completed_processes) FROM users").fetchone()[0] or 0
        referrals = db.execute("SELECT SUM(referrals) FROM users").fetchone()[0] or 0
        db.close()
    await update.message.reply_text(
        f"📊 <b>Admin Stats</b>\n\n"
        f"👥 Total Users: {total}\n"
        f"💎 Total Credits: {credits}\n"
        f"✅ Completed: {completed}\n"
        f"🔗 Referrals: {referrals}",
        parse_mode="HTML"
    )


async def admin_add_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        tid = int(context.args[0])
        amount = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /addcredits <telegram_id> <amount>")
        return
    u = await db_user(tid)
    if u:
        new_credits = u['credits'] + amount
        await db_upsert(tid, credits=new_credits)
        await update.message.reply_text(f"✅ Added {amount} credits to {tid}. Total: {new_credits}")
    else:
        await update.message.reply_text("❌ User not found.")


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    async with conn_lock:
        db = get_db()
        rows = db.execute("SELECT telegram_id FROM users WHERE joined_channel=1").fetchall()
        db.close()
    sent = 0
    failed = 0
    for row in rows:
        try:
            await context.bot.send_message(chat_id=row['telegram_id'], text=msg, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"📢 Broadcast: {sent} sent, {failed} failed.")


# ============================================================
# MENU ROUTER
# ============================================================
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🏠 Start Process":
        return await start_process(update, context)
    elif text == "👥 Refer & Earn":
        await refer(update, context)
    elif text == "📊 Dashboard":
        await dashboard(update, context)
    elif text == "📞 Support":
        await support(update, context)
    else:
        await show_main_menu(update)


# ============================================================
# MAIN
# ============================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Text("🏠 Start Process"), start_process),
            CommandHandler("startprocess", start_process),
        ],
        states={
            ASK_MOBILE: [
                MessageHandler(filters.Text("❌ Cancel"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_mobile),
            ],
            ASK_OTP: [
                MessageHandler(filters.Text("❌ Cancel"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_otp),
            ],
            ASK_UPI: [
                MessageHandler(filters.Text("❌ Cancel"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_upi),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("refer", refer))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("addcredits", admin_add_credits))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Text(["👥 Refer & Earn", "📊 Dashboard", "📞 Support"]), menu_handler))

    logger.info("🤖 Bot started polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
