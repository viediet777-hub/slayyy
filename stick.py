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
from datetime import datetime, timedelta
from typing import Tuple, Optional
import glob

import aiohttp
from aiohttp import ClientSession, FormData

# Fix for Python 3.14 compatibility
try:
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
except AttributeError:
    # Fallback for older versions
    from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
    from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler

# ==================== CONFIG - ENV ONLY ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ BOT_TOKEN environment variable required!")
    sys.exit(1)

ADMIN_IDS = []
admin_id = os.getenv("ADMIN_ID")
if admin_id:
    try:
        ADMIN_IDS = [int(x.strip()) for x in admin_id.split(",") if x.strip()]
    except:
        pass
if not ADMIN_IDS:
    ADMIN_IDS = [1364476174]
    print(f"⚠️ Using default admin: {ADMIN_IDS[0]}")

CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "viedietlooters")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002388556922"))
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/viedietlooterschat")
BASE_URL = os.getenv("BASE_URL", "https://slayyourplaypromo.in")
DB_PATH = os.getenv("DB_PATH", "slay_bot_local.db")

print("=" * 60)
print("🤖 SLAY YOUR PLAY - RAILWAY READY BOT")
print("=" * 60)
print(f"👑 Admin: {ADMIN_IDS}")
print(f"📢 Channel: {CHANNEL_USERNAME}")
print("=" * 60)

# ============================================================
# IMAGE LOADING - RAILWAY FRIENDLY
# ============================================================
def load_image_railway() -> Tuple[bytes, str]:
    """Load image from multiple sources for Railway compatibility"""
    
    # 1. Environment variable (Base64 encoded)
    env_img = os.getenv("IMAGE_BASE64")
    if env_img:
        try:
            data = base64.b64decode(env_img)
            if len(data) > 5000:
                print(f"✅ Image loaded from IMAGE_BASE64 ({len(data)} bytes)")
                return data, "IMG.jpg"
        except:
            pass
    
    # 2. Current directory
    for fname in ["IMG.jpg", "image.jpg", "photo.jpg", "stick.jpg"]:
        if os.path.exists(fname):
            with open(fname, "rb") as f:
                data = f.read()
            if len(data) > 5000:
                print(f"✅ Image loaded from {fname} ({len(data)} bytes)")
                return data, fname
    
    # 3. Render attachments folder
    for path in ["/app/attachments", "/opt/render/project/src/attachments"]:
        if os.path.exists(path):
            for fname in os.listdir(path):
                if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    with open(f"{path}/{fname}", "rb") as f:
                        data = f.read()
                    if len(data) > 5000:
                        print(f"✅ Image loaded from {path}/{fname} ({len(data)} bytes)")
                        return data, fname
    
    # 4. Downloads folder (local testing)
    downloads = os.path.expanduser("~/Downloads")
    if os.path.exists(downloads):
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            files = glob.glob(os.path.join(downloads, f"*{ext}"))
            files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            for fpath in files:
                with open(fpath, "rb") as f:
                    data = f.read()
                if len(data) > 5000:
                    print(f"✅ Image loaded from Downloads: {fpath} ({len(data)} bytes)")
                    return data, os.path.basename(fpath)
    
    # 5. Fallback - Minimal valid JPEG
    print("⚠️ No real image found! Using fallback 1x1 pixel JPEG.")
    minimal_jpeg = base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAb/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGfAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//Z"
    )
    return minimal_jpeg, "IMG.jpg"

IMAGE_BYTES, IMAGE_NAME = load_image_railway()
print(f"📸 Image: {IMAGE_NAME} | Size: {len(IMAGE_BYTES)} bytes")

# ============================================================
# SIGNATURE FUNCTION
# ============================================================
def build_signed_data(payload: dict, data_key: str, for_multipart: bool = False) -> str:
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

    raw_data = f"{u}.{a}.{g}"

    if for_multipart:
        return raw_data
    else:
        return f"userKey={payload['userKey']}&data={urllib.parse.quote_plus(raw_data)}"

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
# DATABASE - WITH AUTO CLEANUP
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
            temp_data TEXT DEFAULT '',
            session_expiry TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    return db

async def cleanup_expired_sessions():
    async with conn_lock:
        db = get_db()
        db.execute("DELETE FROM users WHERE session_expiry < datetime('now')")
        db.commit()
        db.close()

async def db_user(telegram_id: int) -> Optional[dict]:
    await cleanup_expired_sessions()
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

async def db_clear_user_data(telegram_id: int):
    async with conn_lock:
        db = get_db()
        db.execute("""
            UPDATE users 
            SET mobile='', upi='', user_key='', data_key='', 
                access_token='', temp_data='', session_expiry=NULL 
            WHERE telegram_id=?
        """, (telegram_id,))
        db.commit()
        db.close()

async def db_set_temp_data(telegram_id: int, data: dict):
    async with conn_lock:
        db = get_db()
        expiry = (datetime.now() + timedelta(hours=2)).isoformat()
        db.execute("""
            UPDATE users 
            SET temp_data=?, session_expiry=? 
            WHERE telegram_id=?
        """, (json.dumps(data), expiry, telegram_id))
        db.commit()
        db.close()

# ============================================================
# AIOHTTP SESSION
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
    return _http_session

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
            if resp.status == 200:
                data = decode_resp(text)
                return True, data
            return False, {"message": f"HTTP {resp.status}", "detail": text}
    except Exception as e:
        logging.error(f"INIT failed: {e}")
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

    try:
        if files:
            data_part = build_signed_data(payload, data_key, for_multipart=True)

            form = FormData()

            for field_name, (filename, content, content_type) in files.items():
                form.add_field(field_name, content, filename=filename, content_type=content_type)

            form.add_field('data', data_part)
            form.add_field('userKey', str(user_key))

            async with session.post(request_url, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                text = await resp.text()
                try:
                    decoded = decode_resp(text)
                except Exception:
                    decoded = {"statusCode": resp.status, "message": text[:200]}
                status_ok = decoded.get('statusCode', 400) in (200, 201, 202)
                return status_ok, decoded
        else:
            signed_data = build_signed_data(payload, data_key, for_multipart=False)
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
        logging.error(f"{endpoint} error: {e}")
        return False, {"message": str(e)}

# ============================================================
# API STEPS
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
    print(f"📤 Uploading image: {filename} ({len(image_bytes)} bytes)")
    payload = {}
    files = {'media': (filename, image_bytes, 'image/jpeg')}
    return await make_signed_request('/api/users/uploadImage/{userKey}', payload, data_key, user_key, referer='/take-stick-photo', files=files, jwt_token=jwt_token)

async def submit_upi(upi_number: str, user_key, data_key, jwt_token) -> Tuple[bool, dict]:
    payload = {"upiNo": upi_number}
    return await make_signed_request('/api/users/getUpiNo/{userKey}', payload, data_key, user_key, referer='/dashboard', jwt_token=jwt_token)

# ============================================================
# BOT SETUP - COMPATIBLE WITH BOTH VERSIONS
# ============================================================
def setup_bot():
    """Setup bot with compatibility for different python-telegram-bot versions"""
    try:
        # Try new version
        from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
        app = Application.builder().token(BOT_TOKEN).build()
        return app, True
    except (AttributeError, ImportError):
        # Fallback to old version
        from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler
        updater = Updater(BOT_TOKEN, use_context=True)
        app = updater.dispatcher
        return app, False

# Use global for app and is_new_api
APP, IS_NEW_API = setup_bot()

# ============================================================
# KEYBOARDS
# ============================================================
main_keyboard = ReplyKeyboardMarkup([
    ["🚀 Start Process"],
    ["👥 Refer & Earn", "📊 Dashboard"],
    ["📞 Support", "ℹ️ About"],
], resize_keyboard=True)

cancel_keyboard = ReplyKeyboardMarkup([
    ["❌ Cancel"]
], resize_keyboard=True)

admin_keyboard = ReplyKeyboardMarkup([
    ["📊 Admin Stats", "📢 Broadcast"],
    ["💎 Add Credits", "👥 Users List"],
    ["🏠 Back to Main"],
], resize_keyboard=True)

ASK_MOBILE, ASK_OTP, ASK_UPI = range(3)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def is_channel_member(bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ('member', 'administrator', 'creator')
    except Exception as e:
        logger.warning(f"Channel check failed: {e}")
        return False

async def validate_mobile(phone: str) -> bool:
    return len(phone) == 10 and phone.isdigit()

async def validate_otp(code: str) -> bool:
    return len(code) == 6 and code.isdigit()

async def show_main_menu(update: Update, text: str = "Choose an option:"):
    await update.message.reply_text(text, reply_markup=main_keyboard)

# ============================================================
# HANDLERS
# ============================================================
async def start(update: Update, context):
    user = update.effective_user
    tid = user.id
    args = context.args if hasattr(context, 'args') else []

    await cleanup_expired_sessions()
    await db_clear_user_data(tid)

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
    
    welcome_text = f"""
🌟 **Welcome {user.first_name}!** 🌟

💰 **Earn ₹20 per process!**
👥 Refer friends and earn +1 Credit!

📌 **How to use:**
1️⃣ Click "Start Process"
2️⃣ Enter your mobile number
3️⃣ Verify OTP
4️⃣ Enter UPI number
5️⃣ Get ₹20 credited!

🔗 Each referral = 1 Free Process!
"""
    await show_main_menu(update, welcome_text)

async def about(update: Update, context):
    text = """
ℹ️ **About SLAY YOUR PLAY Bot**

💰 **Earn Money**: ₹20 per successful process
👥 **Referral System**: +1 Credit per referral
⚡ **Easy Process**: Just OTP & UPI

🔐 **Privacy**: All data auto-deleted after 2 hours
📱 **Support**: @viedietlooterschat

**Version**: 2.0 (Railway Ready)
"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def support(update: Update, context):
    await update.message.reply_text(
        "📞 Join our support group:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Open Group", url=SUPPORT_LINK)]
        ])
    )

async def dashboard(update: Update, context):
    tid = update.effective_user.id
    u = await db_user(tid)
    if not u:
        await show_main_menu(update, "No data found. Use /start first.")
        return
    
    await db_clear_user_data(tid)
    
    text = f"""
📊 **Your Dashboard**
━━━━━━━━━━━━━━━━━━
👤 Name: {u['name']}
🆔 ID: `{u['telegram_id']}`
📱 Mobile: {u['mobile'] or '❌ Not set'}
💳 UPI: {u['upi'] or '❌ Not set'}
━━━━━━━━━━━━━━━━━━
👥 Referrals: {u['referrals']}
💎 Credits: {u['credits']}
✅ Completed: {u['completed_processes']}
━━━━━━━━━━━━━━━━━━
🔐 Data Auto-Deleted after 2 hours
"""
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard)

async def refer(update: Update, context):
    tid = update.effective_user.id
    bot_user = await context.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={tid}"
    text = f"""
👥 **Refer & Earn**
━━━━━━━━━━━━━━━━━━
Share your referral link and earn **+1 Credit** for every new user!

**Your Link:**
`{link}`

**Benefits:**
• 1 Referral = 1 Credit
• 1 Credit = 1 Process
• 1 Process = ₹20

**Share now and earn more!**
"""
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={link}&text=Join%20this%20bot%20and%20earn%20₹20!")],
        ])
    )
    await show_main_menu(update)

# ============================================================
# PROCESS HANDLERS
# ============================================================
async def start_process(update: Update, context):
    tid = update.effective_user.id
    
    await db_clear_user_data(tid)
    
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
    is_admin = tid in ADMIN_IDS
    credits = u['credits'] if u else 1
    
    if credits < 1 and not is_admin:
        await update.message.reply_text(
            "❌ **No Credits Left!**\n\n"
            "Earn credits by referring friends!\n"
            "1 Referral = 1 Credit = ₹20\n\n"
            "Use /refer to get your referral link.",
            parse_mode="Markdown",
            reply_markup=main_keyboard
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📱 **Enter your 10-digit mobile number:**\n"
        "_(Type /cancel to stop)_",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard
    )
    return ASK_MOBILE

async def ask_mobile(update: Update, context):
    text = update.message.text.strip()
    if text == "❌ Cancel" or text == "/cancel":
        await show_main_menu(update, "❌ Process Cancelled.")
        return ConversationHandler.END

    if not await validate_mobile(text):
        await update.message.reply_text("❌ Invalid number. Enter a 10-digit mobile number:")
        return ASK_MOBILE

    phone = text
    context.user_data['phone'] = phone

    await update.message.reply_text("⏳ **Initializing session...**", parse_mode="Markdown")

    ok, init_data = await api_init()
    if not ok or init_data.get('statusCode') != 200:
        await update.message.reply_text(f"❌ Init failed: {init_data.get('message', 'Unknown')}", reply_markup=main_keyboard)
        return ConversationHandler.END

    user_key = init_data['userKey']
    data_key = init_data['dataKey']
    context.user_data['userKey'] = user_key
    context.user_data['dataKey'] = data_key

    await db_set_temp_data(update.effective_user.id, {
        'phone': phone,
        'userKey': user_key,
        'dataKey': data_key
    })

    await update.message.reply_text("⏳ **Tracking...**", parse_mode="Markdown")
    await click_track(user_key, data_key)

    await update.message.reply_text("⏳ **Sending OTP...**", parse_mode="Markdown")
    reg_ok, reg_data = await register_user(phone, user_key, data_key)
    if not reg_ok:
        await update.message.reply_text(f"❌ Registration failed: {reg_data.get('message', 'Unknown')}", reply_markup=main_keyboard)
        return ConversationHandler.END

    await update.message.reply_text(
        "✅ **OTP Sent!**\n"
        "Enter the 6-digit OTP received on your phone:",
        parse_mode="Markdown"
    )
    return ASK_OTP

async def ask_otp(update: Update, context):
    text = update.message.text.strip()
    if text == "❌ Cancel" or text == "/cancel":
        await show_main_menu(update, "❌ Process Cancelled.")
        return ConversationHandler.END

    if not await validate_otp(text):
        await update.message.reply_text("❌ Invalid OTP. Enter a 6-digit code:")
        return ASK_OTP

    otp = text
    user_key = context.user_data['userKey']
    data_key = context.user_data['dataKey']

    await update.message.reply_text("⏳ **Verifying OTP...**", parse_mode="Markdown")
    ok, data = await verify_otp(otp, user_key, data_key)
    if not ok:
        await update.message.reply_text(f"❌ OTP verification failed: {data.get('message', 'Unknown')}", reply_markup=main_keyboard)
        return ConversationHandler.END

    access_token = data.get('accessToken') or data.get('data', {}).get('accessToken', '')
    if not access_token:
        await update.message.reply_text("❌ No access token received.", reply_markup=main_keyboard)
        return ConversationHandler.END

    context.user_data['jwt'] = access_token
    tid = update.effective_user.id
    
    await db_upsert(tid, mobile=context.user_data['phone'], user_key=str(user_key), data_key=data_key, access_token=access_token)
    await db_set_temp_data(tid, {
        'phone': context.user_data['phone'],
        'userKey': user_key,
        'dataKey': data_key,
        'jwt': access_token
    })

    await update.message.reply_text("✅ **OTP Verified!** Setting up your process...", parse_mode="Markdown")

    await update.message.reply_text("⏳ **Selecting pack...**", parse_mode="Markdown")
    ok, pdata = await select_pack(user_key, data_key, access_token)
    if not ok:
        await update.message.reply_text(f"⚠️ Pack: {pdata.get('message', 'failed')}")

    await update.message.reply_text("⏳ **Selecting vibe...**", parse_mode="Markdown")
    ok, vdata = await select_vibe(user_key, data_key, access_token)
    if not ok:
        await update.message.reply_text(f"⚠️ Vibe: {vdata.get('message', 'failed')}")

    await update.message.reply_text("⏳ **Uploading image...**", parse_mode="Markdown")
    
    if IMAGE_BYTES:
        ok, img_data = await upload_image(IMAGE_BYTES, IMAGE_NAME, user_key, data_key, access_token)
        if ok:
            await update.message.reply_text("✅ **Image uploaded successfully!**", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ Image upload: {img_data.get('message', 'failed')}")
    else:
        await update.message.reply_text("⚠️ No image found. Continuing...")

    await update.message.reply_text(
        "💳 **Enter your UPI-registered mobile number (10 digits):**\n"
        "_(Type /cancel to stop)_",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard
    )
    return ASK_UPI

async def ask_upi(update: Update, context):
    text = update.message.text.strip()
    if text == "❌ Cancel" or text == "/cancel":
        await show_main_menu(update, "❌ Process Cancelled.")
        return ConversationHandler.END

    if not await validate_mobile(text):
        await update.message.reply_text("❌ Invalid number. Enter a 10-digit UPI mobile number:")
        return ASK_UPI

    upi_number = text
    user_key = context.user_data['userKey']
    data_key = context.user_data['dataKey']
    jwt = context.user_data['jwt']
    tid = update.effective_user.id

    await update.message.reply_text("⏳ **Submitting UPI...**", parse_mode="Markdown")
    ok, upi_data = await submit_upi(upi_number, user_key, data_key, jwt)
    if not ok:
        await update.message.reply_text("⏳ **Retrying...**", parse_mode="Markdown")
        ok, upi_data = await submit_upi(upi_number, user_key, data_key, jwt)

    if ok:
        await db_upsert(tid, upi=upi_number)
        await update.message.reply_text("✅ **UPI submitted successfully!**", parse_mode="Markdown")
    else:
        msg = upi_data.get('message', 'Unknown error')
        await update.message.reply_text(f"⚠️ UPI: {msg}")

    is_admin = tid in ADMIN_IDS
    u = await db_user(tid)
    if not is_admin:
        new_credits = max(0, (u['credits'] if u else 1) - 1)
        new_completed = (u['completed_processes'] if u else 0) + 1
        await db_upsert(tid, credits=new_credits, completed_processes=new_completed)
    else:
        new_completed = (u['completed_processes'] if u else 0) + 1
        await db_upsert(tid, completed_processes=new_completed)

    await update.message.reply_text(
        f"✅ **Process Completed!**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💸 Your payment of ₹20 will be credited to your UPI within 24-48 hours.\n\n"
        f"📱 UPI: {upi_number}\n"
        f"💰 Amount: ₹20\n"
        f"📅 Date: {datetime.now().strftime('%d-%m-%Y %H:%M')}\n\n"
        f"🔐 Your data has been auto-deleted.",
        parse_mode="Markdown"
    )

    await db_clear_user_data(tid)
    await show_main_menu(update, "✅ Process finished successfully!")
    return ConversationHandler.END

async def cancel(update: Update, context):
    tid = update.effective_user.id
    await db_clear_user_data(tid)
    await show_main_menu(update, "❌ Cancelled. Your data has been deleted.")
    return ConversationHandler.END

# ============================================================
# ADMIN COMMANDS
# ============================================================
async def admin_panel(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Unauthorized.")
        return
    await update.message.reply_text(
        "👑 **Admin Panel**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 Stats - View user statistics\n"
        "📢 Broadcast - Send message to all\n"
        "💎 Add Credits - Give credits to user\n"
        "👥 Users List - View all users",
        parse_mode="Markdown",
        reply_markup=admin_keyboard
    )

async def admin_stats(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    async with conn_lock:
        db = get_db()
        total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        credits = db.execute("SELECT SUM(credits) FROM users").fetchone()[0] or 0
        completed = db.execute("SELECT SUM(completed_processes) FROM users").fetchone()[0] or 0
        referrals = db.execute("SELECT SUM(referrals) FROM users").fetchone()[0] or 0
        active = db.execute("SELECT COUNT(*) FROM users WHERE session_expiry > datetime('now')").fetchone()[0]
        db.close()
    await update.message.reply_text(
        f"📊 **Admin Stats**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Users: {total}\n"
        f"🟢 Active: {active}\n"
        f"💎 Credits: {credits}\n"
        f"✅ Completed: {completed}\n"
        f"🔗 Referrals: {referrals}",
        parse_mode="Markdown",
        reply_markup=admin_keyboard
    )

async def admin_add_credits(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 3:
            await update.message.reply_text("Usage: /addcredits <telegram_id> <amount>")
            return
        tid = int(parts[1])
        amount = int(parts[2])
    except:
        await update.message.reply_text("Usage: /addcredits <telegram_id> <amount>")
        return
    u = await db_user(tid)
    if u:
        new_credits = u['credits'] + amount
        await db_upsert(tid, credits=new_credits)
        await update.message.reply_text(f"✅ Added {amount} credits to {tid}. Total: {new_credits}")
    else:
        await update.message.reply_text("❌ User not found.")

async def admin_broadcast(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = update.message.text.replace("/broadcast", "").strip()
    if not msg:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    async with conn_lock:
        db = get_db()
        rows = db.execute("SELECT telegram_id FROM users WHERE joined_channel=1").fetchall()
        db.close()
    sent = failed = 0
    for row in rows:
        try:
            await context.bot.send_message(chat_id=row['telegram_id'], text=f"📢 **ANNOUNCEMENT**\n\n{msg}", parse_mode="Markdown")
            sent += 1
        except:
            failed += 1
    await update.message.reply_text(f"📢 Broadcast: {sent} sent, {failed} failed.")

async def admin_users_list(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    async with conn_lock:
        db = get_db()
        rows = db.execute("SELECT telegram_id, name, credits, completed_processes, referrals FROM users ORDER BY created_at DESC LIMIT 20").fetchall()
        db.close()
    if not rows:
        await update.message.reply_text("No users found.")
        return
    text = "👥 **Recent Users**\n━━━━━━━━━━━━━━━━━━\n"
    for row in rows:
        text += f"🆔 {row['telegram_id']} | {row['name'][:15]} | Credits: {row['credits']} | Done: {row['completed_processes']}\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_keyboard)

async def admin_menu_handler(update: Update, context):
    text = update.message.text
    if text == "📊 Admin Stats":
        await admin_stats(update, context)
    elif text == "📢 Broadcast":
        await update.message.reply_text("Send /broadcast <message> to send announcement.")
    elif text == "💎 Add Credits":
        await update.message.reply_text("Send /addcredits <telegram_id> <amount>")
    elif text == "👥 Users List":
        await admin_users_list(update, context)
    elif text == "🏠 Back to Main":
        await show_main_menu(update, "Back to main menu.")
    else:
        await update.message.reply_text("Use admin buttons.", reply_markup=admin_keyboard)

async def menu_handler(update: Update, context):
    text = update.message.text
    if text == "🚀 Start Process":
        return await start_process(update, context)
    elif text == "👥 Refer & Earn":
        await refer(update, context)
    elif text == "📊 Dashboard":
        await dashboard(update, context)
    elif text == "📞 Support":
        await support(update, context)
    elif text == "ℹ️ About":
        await about(update, context)
    elif text == "👑 Admin Panel" and update.effective_user.id in ADMIN_IDS:
        await admin_panel(update, context)
    else:
        await show_main_menu(update)

# ============================================================
# MAIN
# ============================================================
def main():
    print(f"✅ Bot starting...")
    print(f"📸 Image: {IMAGE_NAME} ({len(IMAGE_BYTES)} bytes)")
    
    # Setup conversation handler
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Text("🚀 Start Process"), start_process),
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

    if IS_NEW_API:
        # New API - Application
        app = Application.builder().token(BOT_TOKEN).build()
        
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("support", support))
        app.add_handler(CommandHandler("dashboard", dashboard))
        app.add_handler(CommandHandler("refer", refer))
        app.add_handler(CommandHandler("about", about))
        app.add_handler(CommandHandler("stats", admin_stats))
        app.add_handler(CommandHandler("addcredits", admin_add_credits))
        app.add_handler(CommandHandler("broadcast", admin_broadcast))
        app.add_handler(CommandHandler("users", admin_users_list))
        app.add_handler(CommandHandler("admin", admin_panel))
        app.add_handler(conv_handler)
        app.add_handler(MessageHandler(filters.Text(["👥 Refer & Earn", "📊 Dashboard", "📞 Support", "ℹ️ About"]), menu_handler))
        app.add_handler(MessageHandler(filters.Text(["📊 Admin Stats", "📢 Broadcast", "💎 Add Credits", "👥 Users List", "🏠 Back to Main"]), admin_menu_handler))
        app.add_handler(MessageHandler(filters.Text(["🚀 Start Process", "👑 Admin Panel"]), menu_handler))
        
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        # Old API - Updater
        updater = Updater(BOT_TOKEN, use_context=True)
        dp = updater.dispatcher
        
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("support", support))
        dp.add_handler(CommandHandler("dashboard", dashboard))
        dp.add_handler(CommandHandler("refer", refer))
        dp.add_handler(CommandHandler("about", about))
        dp.add_handler(CommandHandler("stats", admin_stats))
        dp.add_handler(CommandHandler("addcredits", admin_add_credits))
        dp.add_handler(CommandHandler("broadcast", admin_broadcast))
        dp.add_handler(CommandHandler("users", admin_users_list))
        dp.add_handler(CommandHandler("admin", admin_panel))
        dp.add_handler(conv_handler)
        dp.add_handler(MessageHandler(filters.Text(["👥 Refer & Earn", "📊 Dashboard", "📞 Support", "ℹ️ About"]), menu_handler))
        dp.add_handler(MessageHandler(filters.Text(["📊 Admin Stats", "📢 Broadcast", "💎 Add Credits", "👥 Users List", "🏠 Back to Main"]), admin_menu_handler))
        dp.add_handler(MessageHandler(filters.Text(["🚀 Start Process", "👑 Admin Panel"]), menu_handler))
        
        updater.start_polling()
        updater.idle()

if __name__ == "__main__":
    main()
