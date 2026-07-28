#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import json
import sqlite3
import asyncio
import aiohttp
from yarl import URL as YarlURL
import random
import string
import csv
import base64
import hashlib
import hmac
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set.")

ADMIN_ID = int(os.environ.get("ADMIN_ID", 1364476174))
BASE_URL = "https://slayyourplaypromo.in"
CHANNEL_USERNAME = "viedietlooters"
GROUP_USERNAME = "viedietlooterschat"
IMAGE_URL = "https://cdn.phototourl.com/free/2026-07-28-56446cd9-7512-40c6-b11e-e66ceb923351.jpg"
IMAGE_NAME = "photo_2026-07-28_11-11-35.jpg"
REFERRAL_REQUIRED = 1
REWARD_PER_PROCESS = 20
DB_PATH = "slayyourplay.db"

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        phone TEXT,
        upi_number TEXT,
        jwt_token TEXT,
        referral_code TEXT UNIQUE,
        referred_by INTEGER DEFAULT NULL,
        referrals_count INTEGER DEFAULT 0,
        process_credits INTEGER DEFAULT 0,
        total_processes INTEGER DEFAULT 0,
        successful_processes INTEGER DEFAULT 0,
        total_rewards INTEGER DEFAULT 0,
        today_processes INTEGER DEFAULT 0,
        today_completed INTEGER DEFAULT 0,
        last_process_date TEXT,
        is_banned INTEGER DEFAULT 0,
        registered_at TEXT,
        last_activity TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        referred_at TEXT,
        is_valid INTEGER DEFAULT 1
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS processes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        status TEXT DEFAULT 'pending',
        reward INTEGER DEFAULT 0,
        upi_number TEXT,
        created_at TEXT,
        completed_at TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# ==================== GLOBAL API SESSION ====================
_api_session: Optional[aiohttp.ClientSession] = None
_api_headers = {
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.9',
    'user-agent': 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36',
    'origin': BASE_URL,
    'x-requested-with': 'XMLHttpRequest',
}

async def get_api_session() -> aiohttp.ClientSession:
    global _api_session
    if _api_session is None or _api_session.closed:
        jar = aiohttp.CookieJar()
        _api_session = aiohttp.ClientSession(headers=_api_headers, cookie_jar=jar)
    return _api_session

async def close_api_session():
    global _api_session
    if _api_session and not _api_session.closed:
        await _api_session.close()

def decode_resp(resp_text: str) -> dict:
    try:
        data = json.loads(resp_text)
        if 'resp' in data:
            decoded = base64.b64decode(data['resp']).decode()
            return json.loads(decoded)
        return data
    except Exception as e:
        return {'error': str(e), 'raw': resp_text[:500]}

def build_signed_data(payload: dict, data_key: str) -> str:
    payload_str = json.dumps(payload, separators=(',', ':'))
    a = base64.b64encode(payload_str.encode()).decode()
    t = str(payload['t'])
    u = base64.b64encode(t.encode()).decode()
    hmac_key = data_key[4:18].encode()
    hex_sig = hmac.new(hmac_key, f'{u}.{a}'.encode(), hashlib.sha256).hexdigest()
    f2 = base64.b64encode(hex_sig.encode()).decode()
    m = random.randint(1, 6)
    k2 = random.randint(2, 8)
    alpha = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    h_rand = ''.join(random.choice(alpha) for _ in range(k2))
    g = f'{k2}{m}{f2[0:m]}{h_rand}{f2[m:]}'
    return f'userKey={urllib.parse.quote_plus(str(payload.get("userKey", "")))}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}'

# ==================== DATABASE FUNCTIONS ====================
def get_user(user_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'phone': row[3],
            'upi_number': row[4],
            'jwt_token': row[5],
            'referral_code': row[6],
            'referred_by': row[7],
            'referrals_count': row[8],
            'process_credits': row[9],
            'total_processes': row[10],
            'successful_processes': row[11],
            'total_rewards': row[12],
            'today_processes': row[13],
            'today_completed': row[14],
            'last_process_date': row[15],
            'is_banned': row[16],
            'registered_at': row[17],
            'last_activity': row[18]
        }
    return None

def create_user(user_id: int, username: str, first_name: str, phone: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    ref_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    c.execute('''INSERT OR IGNORE INTO users 
        (user_id, username, first_name, phone, referral_code, registered_at, last_activity)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (user_id, username, first_name, phone, ref_code, now, now))
    conn.commit()
    conn.close()

def update_user(user_id: int, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updates = []
    values = []
    for key, value in kwargs.items():
        updates.append(f"{key} = ?")
        values.append(value)
    values.append(user_id)
    c.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", values)
    conn.commit()
    conn.close()

def add_process_credits(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET process_credits = process_credits + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

def add_referral(referrer_id: int, referred_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute('INSERT INTO referrals (referrer_id, referred_id, referred_at) VALUES (?, ?, ?)',
                  (referrer_id, referred_id, now))
        c.execute('UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id = ?', (referrer_id,))
        c.execute('SELECT referrals_count FROM users WHERE user_id = ?', (referrer_id,))
        count = c.fetchone()[0]
        if count % REFERRAL_REQUIRED == 0:
            add_process_credits(referrer_id, 1)
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def add_process(user_id: int, reward: int, upi_number: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('''INSERT INTO processes (user_id, status, reward, upi_number, created_at)
                 VALUES (?, ?, ?, ?, ?)''',
              (user_id, 'completed', reward, upi_number, now))
    c.execute('''UPDATE users SET 
                 total_processes = total_processes + 1,
                 successful_processes = successful_processes + 1,
                 total_rewards = total_rewards + ?,
                 today_processes = today_processes + 1,
                 today_completed = today_completed + 1,
                 last_process_date = ?,
                 process_credits = process_credits - 1
                 WHERE user_id = ?''',
              (reward, now, user_id))
    conn.commit()
    conn.close()

def get_today_reset(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute('SELECT last_process_date FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    if row and row[0] and row[0].startswith(today):
        conn.close()
        return False
    c.execute('UPDATE users SET today_processes = 0, today_completed = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()
    return True

def get_user_by_referral_code(code: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE referral_code = ?', (code,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_total_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT SUM(total_processes) FROM users')
    total_processes = c.fetchone()[0] or 0
    c.execute('SELECT SUM(successful_processes) FROM users')
    total_completed = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM processes WHERE status = "pending"')
    pending = c.fetchone()[0]
    conn.close()
    return {
        'total_users': total_users,
        'total_processes': total_processes,
        'total_completed': total_completed,
        'pending': pending
    }

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id, username, first_name, phone, referrals_count, process_credits, total_processes, is_banned FROM users ORDER BY registered_at DESC')
    rows = c.fetchall()
    conn.close()
    return rows

# ==================== API FUNCTIONS ====================
async def _log_response(resp, label: str):
    """Log full request/response details for debugging."""
    try:
        text = await resp.text()
    except:
        text = '<could not read body>'
    print(f"\n{'='*60}")
    print(f"RESPONSE: {label}")
    print(f"{'='*60}")
    print(f"Status: {resp.status} {resp.reason}")
    print(f"URL: {resp.url}")
    print(f"Response Headers:")
    for k, v in sorted(dict(resp.headers).items()):
        print(f"  {k}: {v}")
    # Try to decode and pretty-print the response body
    try:
        decoded = decode_resp(text)
        print(f"Response Body (decoded):")
        print(json.dumps(decoded, indent=2))
        print(f"Raw body length: {len(text)} chars")
    except:
        print(f"Response Body (raw, {len(text)} chars):")
        print(text[:2000])
    print(f"{'='*60}\n")
    return text

async def _make_signed_request(endpoint: str, payload: dict, data_key: str, user_key, referer: str = '/', files: dict = None) -> Tuple[bool, dict]:
    url = f"{BASE_URL}{endpoint}"
    url = url.replace('{userKey}', str(user_key))
    t = int(datetime.now().timestamp() * 1000)
    payload['t'] = t
    payload['userKey'] = int(user_key) if isinstance(user_key, str) and user_key.isdigit() else user_key

    start_time = datetime.now()
    
    print(f"\n{'='*60}")
    print(f"API CALL: {endpoint}")
    print(f"{'='*60}")
    print(f"Method: POST")
    print(f"URL: {url}")
    print(f"Timestamp (t): {t}")
    print(f"Payload before signing: {json.dumps(payload, indent=2)}")
    print(f"dataKey (HMAC source): {data_key}")
    print(f"HMAC key (data_key[4:18]): {data_key[4:18]}")
    
    if files:
        body = aiohttp.FormData()
        for field_name, file_info in files.items():
            print(f"Form field: {field_name} = {file_info[0]} ({file_info[2]}, {len(file_info[1])} bytes)")
            body.add_field(field_name, file_info[1], filename=file_info[0], content_type=file_info[2])
        data_field = build_signed_data(payload, data_key)
        parts = data_field.split('&')
        data_val = urllib.parse.unquote(parts[1].split('=')[1]) if len(parts) > 1 else ''
        user_key_val = urllib.parse.unquote(parts[0].split('=')[1]) if len(parts) > 0 else ''
        body.add_field('data', data_val)
        body.add_field('userKey', user_key_val)
        print(f"Signed data field: {data_val[:100]}...")
        print(f"userKey field: {user_key_val}")

        req_url = f"{url}?t={t}"
        session = await get_api_session()
        
        # Print cookies
        cookies = dict(session.cookie_jar.filter_cookies(YarlURL(BASE_URL)))
        print(f"Cookies: {dict(cookies)}")
        
        req_headers = {'referer': f'{BASE_URL}{referer}'}
        print(f"Request headers: {req_headers}")
        print(f"Request URL: {req_url}")
        
        async with session.post(req_url, data=body, headers=req_headers) as resp:
            elapsed = (datetime.now() - start_time).total_seconds()
            resp_text = await _log_response(resp, f"SIGNED FILE UPLOAD REQUEST: {endpoint}")
            print(f"Time taken: {elapsed:.2f}s")
            decoded = decode_resp(resp_text)
            status_code = decoded.get('statusCode', 400)
            print(f"Decoded statusCode: {status_code}")
            print(f"Decoded message: {decoded.get('message', 'N/A')}")
            print(f"{'='*60}\n")
            return status_code in [200, 201, 202], decoded
    else:
        signed_body_str = build_signed_data(payload, data_key)
        req_url = f"{url}?t={t}"
        headers = {
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'referer': f'{BASE_URL}{referer}',
        }
        
        # Parse signed body components
        parsed_qs = urllib.parse.parse_qs(signed_body_str)
        data_parts = parsed_qs.get('data', [''])[0].split('.')
        print(f"\nSigned body components:")
        print(f"  userKey: {parsed_qs.get('userKey', [''])[0]}")
        if len(data_parts) >= 1:
            decoded_t = base64.b64decode(data_parts[0] + '==').decode() if data_parts[0] else 'N/A'
            print(f"  data.t (timestamp): {data_parts[0][:30]}... -> decoded: {decoded_t}")
        if len(data_parts) >= 2:
            try:
                decoded_payload = base64.b64decode(data_parts[1] + '==').decode()
                print(f"  data.a (payload): {data_parts[1][:30]}... -> decoded: {decoded_payload}")
            except:
                print(f"  data.a (payload): {data_parts[1][:30]}... -> decoding failed")
        print(f"  data.g (signature): {data_parts[2][:30]}... ({len(data_parts[2])} chars)" if len(data_parts) >= 3 else "  data.g: N/A")
        
        session = await get_api_session()
        cookies = dict(session.cookie_jar.filter_cookies(YarlURL(BASE_URL)))
        print(f"Cookies: {cookies}")
        print(f"Request URL: {req_url}")
        print(f"Request headers: {headers}")
        print(f"Request body: {signed_body_str[:400]}")
        
        async with session.post(req_url, data=signed_body_str, headers=headers) as resp:
            elapsed = (datetime.now() - start_time).total_seconds()
            resp_text = await _log_response(resp, f"SIGNED REQUEST: {endpoint}")
            print(f"Time taken: {elapsed:.2f}s")
            decoded = decode_resp(resp_text)
            status_code = decoded.get('statusCode', 400)
            print(f"Decoded statusCode: {status_code}")
            print(f"Decoded message: {decoded.get('message', 'N/A')}")
            print(f"{'='*60}\n")
            return status_code in [200, 201, 202], decoded

async def api_init() -> Tuple[bool, dict]:
    start = datetime.now()
    master_key = str(random.randint(100000000, 999999999))
    session = await get_api_session()
    session.cookie_jar.update_cookies({'thumsup_and_sprite-id': master_key}, YarlURL(BASE_URL))
    
    print(f"\n{'='*60}")
    print(f"API CALL: INIT")
    print(f"{'='*60}")
    print(f"Method: POST")
    print(f"URL: {BASE_URL}/api/users")
    print(f"masterKey: {master_key}")
    print(f"Cookie set: thumsup_and_sprite-id={master_key}")
    
    ip_info = {
        'as': 'AS24560', 'city': 'Delhi', 'country': 'India', 'countryCode': 'IN',
        'isp': 'Airtel', 'lat': 28.65, 'lon': 77.23, 'org': 'Airtel',
        'query': '0.0.0.0', 'region': 'DL', 'regionName': 'Delhi',
        'status': 'success', 'timezone': 'Asia/Kolkata', 'zip': '110001'
    }
    body = {'masterKey': master_key, 'ipInfo': ip_info}
    print(f"Request body: {json.dumps(body, indent=2)}")
    
    url = f"{BASE_URL}/api/users"
    headers = {'content-type': 'application/json', 'referer': f'{BASE_URL}/'}
    print(f"Request headers: {headers}")
    
    async with session.post(url, json=body, headers=headers) as resp:
        resp_text = await _log_response(resp, 'INIT API')
        elapsed = (datetime.now() - start).total_seconds()
        print(f"Time taken: {elapsed:.2f}s")
        
        decoded = decode_resp(resp_text)
        if decoded.get('statusCode') == 200:
            user_key = decoded.get('userKey')
            data_key = decoded.get('dataKey')
            print(f">>> INIT SUCCESS: userKey={user_key}, dataKey={data_key}")
            print(f"{'='*60}\n")
            return True, {'userKey': user_key, 'dataKey': data_key, 'masterKey': master_key}
        print(f">>> INIT FAILED: {json.dumps(decoded, indent=2)}")
        print(f"{'='*60}\n")
        return False, decoded

async def register_user(phone: str, user_key, data_key: str) -> Tuple[bool, dict]:
    payload = {'mobile': phone, 'limit': ''}
    return await _make_signed_request('/api/users/register/{userKey}', payload, data_key, user_key, referer='/register')

async def verify_otp(user_key, data_key: str, otp: str) -> Tuple[bool, dict]:
    payload = {'otp': otp}
    return await _make_signed_request('/api/users/verifyOTP/{userKey}', payload, data_key, user_key, referer='/register')

async def select_pack(user_key, data_key: str) -> Tuple[bool, dict]:
    payload = {'pack': 'single'}
    return await _make_signed_request('/api/users/selectPack/{userKey}', payload, data_key, user_key, referer='/choose-reward')

async def select_vibe(user_key, data_key: str) -> Tuple[bool, dict]:
    payload = {'vibe': 'soft savage'}
    return await _make_signed_request('/api/users/selectVibe/{userKey}', payload, data_key, user_key, referer='/ai-rap-home')

async def upload_image(user_key, data_key: str, image_data: bytes, image_name: str) -> Tuple[bool, dict]:
    """
    Upload image using multipart/form-data with ONLY the media field.
    Matches HAR exactly: no data or userKey fields in the form.
    """
    start = datetime.now()
    t = int(datetime.now().timestamp() * 1000)
    url = f"{BASE_URL}/api/users/uploadImage/{user_key}?t={t}"

    print(f"\n{'='*60}")
    print(f"API CALL: UPLOAD IMAGE")
    print(f"{'='*60}")
    print(f"Method: POST")
    print(f"URL: {url}")
    print(f"Content-Type: multipart/form-data")
    print(f"Form fields: media={image_name} ({len(image_data)} bytes, image/jpeg)")
    print(f"NOTE: No data/userKey form fields per HAR spec")

    form = aiohttp.FormData()
    form.add_field('media', image_data, filename=image_name, content_type='image/jpeg')

    session = await get_api_session()
    cookies = dict(session.cookie_jar.filter_cookies(YarlURL(BASE_URL)))
    print(f"Cookies: {cookies}")

    headers = {
        'referer': f'{BASE_URL}/take-stick-photo',
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'user-agent': 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36',
        'origin': BASE_URL,
        'x-requested-with': 'XMLHttpRequest',
    }

    async with session.post(url, data=form, headers=headers) as resp:
        elapsed = (datetime.now() - start).total_seconds()
        resp_text = await _log_response(resp, 'UPLOAD IMAGE')
        print(f"Time taken: {elapsed:.2f}s")
        decoded = decode_resp(resp_text)
        status_code = decoded.get('statusCode', 400)
        print(f"Decoded statusCode: {status_code}")
        print(f"Decoded message: {decoded.get('message', 'N/A')}")
        print(f"{'='*60}\n")
        return status_code in [200, 201, 202], decoded

async def submit_upi(user_key, data_key: str, upi_number: str) -> Tuple[bool, dict]:
    payload = {'upiNo': upi_number}
    return await _make_signed_request('/api/users/getUpiNo/{userKey}', payload, data_key, user_key, referer='/stick-cashback')

async def get_pack_progress(user_key, data_key: str) -> Tuple[bool, dict]:
    payload = {}
    return await _make_signed_request('/api/users/getPackProgress/{userKey}', payload, data_key, user_key, referer='/stick-cashback')

async def get_stick_progress(user_key, data_key: str) -> Tuple[bool, dict]:
    payload = {}
    return await _make_signed_request('/api/users/getStickProgress/{userKey}', payload, data_key, user_key, referer='/stick-cashback')

async def click_track(user_key, data_key: str) -> Tuple[bool, dict]:
    payload = {}
    return await _make_signed_request('/api/users/clickTrack/{userKey}', payload, data_key, user_key, referer='/stick-cashback')

async def download_image(url: str) -> Tuple[bool, bytes]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as resp:
                if resp.status == 200:
                    return True, await resp.read()
                return False, None
    except Exception:
        return False, None

# ==================== CONVERSATION STATES ====================
PHONE, OTP, UPI = range(3)

# ==================== FORCE JOIN CHECK ====================
async def check_force_join(user_id: int, bot) -> bool:
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

# ==================== KEYBOARDS ====================
def get_main_keyboard(is_admin: bool = False):
    buttons = [
        ["🏠 Start Process"],
        ["👥 Refer & Earn"],
        ["📊 Dashboard"],
        ["📞 Support"]
    ]
    if is_admin:
        buttons.append(["🔐 Admin Panel"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_admin_keyboard():
    buttons = [
        ["📊 Admin Stats", "👥 Users List"],
        ["➕ Add Credits", "➖ Remove Credits"],
        ["📢 Broadcast", "📂 Export DB"],
        ["🔙 Back to Menu"]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_join_keyboard():
    return ReplyKeyboardMarkup([["✅ Check Membership"]], resize_keyboard=True)

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or "User"

    db_user = get_user(user_id)
    if db_user and db_user.get('is_banned'):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    channel_joined = await check_force_join(user_id, context.bot)
    if not channel_joined:
        msg = "🔒 **Access Restricted**\n\n"
        msg += "❌ You haven't joined our channel.\n"
        msg += "\n📢 Channel: https://t.me/" + CHANNEL_USERNAME + "\n\n"
        msg += "After joining, click the button below."
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_join_keyboard())
        return

    if not db_user:
        create_user(user_id, username, first_name)
        db_user = get_user(user_id)

    if context.args and context.args[0].startswith('ref_'):
        ref_code = context.args[0].replace('ref_', '')
        referrer_id = get_user_by_referral_code(ref_code)
        if referrer_id and referrer_id != user_id:
            add_referral(referrer_id, user_id)
            try:
                await context.bot.send_message(
                    referrer_id,
                    f"🎉 **New Referral!**\n\n"
                    f"@{username or first_name} joined using your referral link.\n"
                    f"Referrals: {get_user(referrer_id)['referrals_count']}\n"
                    f"Process Credits: {get_user(referrer_id)['process_credits']}",
                    parse_mode="HTML"
                )
            except:
                pass

    is_admin = (user_id == ADMIN_ID)
    await update.message.reply_text(
        f"👋 Welcome, {first_name}!\n\n"
        f"Select an option below to get started.",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(is_admin)
    )

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    is_admin = (user_id == ADMIN_ID)

    if is_admin and context.user_data.get('admin_action'):
        await admin_input_handler(update, context)
        return

    if text == "✅ Check Membership":
        channel_joined = await check_force_join(user_id, context.bot)
        if channel_joined:
            db_user = get_user(user_id)
            if db_user and db_user.get('is_banned'):
                await update.message.reply_text("🚫 You are banned from using this bot.")
                return
            await update.message.reply_text(
                "✅ You have joined! Welcome!\n\nSelect an option below:",
                reply_markup=get_main_keyboard(user_id == ADMIN_ID)
            )
        else:
            msg = "🔒 **Access Restricted**\n\n"
            msg += "❌ You haven't joined our channel.\n"
            msg += "\n📢 Channel: https://t.me/" + CHANNEL_USERNAME + "\n\n"
            msg += "After joining, click the button below."
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=get_join_keyboard())
        return

    if text == "🏠 Start Process":
        await start_process(update, context)
    elif text == "👥 Refer & Earn":
        await refer_earn(update, context)
    elif text == "📊 Dashboard":
        await dashboard(update, context)
    elif text == "📞 Support":
        await support(update, context)
    elif text == "🔐 Admin Panel":
        await admin_command(update, context)
    elif text == "🔙 Back to Menu":
        context.user_data['admin_mode'] = False
        context.user_data['admin_action'] = None
        await update.message.reply_text(
            "👋 Welcome back!",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
    else:
        if context.user_data.get('admin_mode', False):
            await admin_handler(update, context)
        else:
            await update.message.reply_text(
                "Please use the buttons below.",
                reply_markup=get_main_keyboard(user_id == ADMIN_ID)
            )

# ==================== START PROCESS ====================
async def start_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)

    if db_user.get('process_credits', 0) <= 0:
        await update.message.reply_text(
            "❌ You don't have any Process Credits.\n\n"
            "Invite one friend to unlock one new process.\n\n"
            f"Your Referrals: {db_user['referrals_count']}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📱 **Enter your mobile number**\n\n"
        "Please enter your 10-digit mobile number:\n"
        "Example: 9876543210\n\n"
        "Type /cancel to go back.",
        parse_mode="HTML"
    )
    return PHONE

async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if text in ["🔙 Back", "Back", "/cancel"]:
        await update.message.reply_text(
            "Returning to main menu.",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    menu_buttons = ["🏠 Start Process", "👥 Refer & Earn", "📊 Dashboard", "📞 Support", "🔐 Admin Panel"]
    if text in menu_buttons:
        context.user_data['pending_menu'] = text
        await update.message.reply_text(
            "Process cancelled.",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    if not text.isdigit() or len(text) != 10:
        await update.message.reply_text(
            "❌ Please enter a valid 10-digit mobile number.\n\n"
            "Enter your number or type /cancel:",
            parse_mode="HTML"
        )
        return PHONE

    phone = text
    context.user_data['phone'] = phone
    update_user(user_id, phone=phone)

    await update.message.reply_text("⏳ Initializing...", parse_mode="HTML")

    # Step 1: API Init - get userKey and dataKey
    init_success, init_result = await api_init()
    if not init_success:
        print(f"API INIT FAILED: {json.dumps(init_result, indent=2)}")
        await update.message.reply_text(
            "❌ Initialization failed. Please try again later.\n\n"
            "Type /start to restart.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    user_key = init_result.get('userKey')
    data_key = init_result.get('dataKey')
    print(f"INIT SUCCESS: userKey={user_key}, dataKey={data_key}")

    # Store credentials for subsequent steps
    context.user_data['userKey'] = user_key
    context.user_data['dataKey'] = data_key

    # Step 2: Register (this sends the OTP)
    await update.message.reply_text("⏳ Registering & sending OTP...", parse_mode="HTML")
    reg_success, reg_result = await register_user(phone, user_key, data_key)
    print(f"REGISTER RESULT: {json.dumps(reg_result, indent=2)}")

    if not reg_success:
        error_msg = reg_result.get('message', 'Unknown error')
        status_code = reg_result.get('statusCode', 0)
        print(f"REGISTER FAILED: statusCode={status_code}, message={error_msg}")
        await update.message.reply_text(
            f"❌ Registration failed (Code: {status_code}).\n"
            f"Reason: {error_msg}\n\n"
            "Type /start to restart.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ OTP sent to {phone}!\n\n"
        "📱 Enter the 6-digit OTP you received:\n"
        "Type /cancel to go back.",
        parse_mode="HTML"
    )
    return OTP

async def otp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    otp = update.message.text.strip()

    if otp in ["🔙 Back", "Back", "/cancel"]:
        await update.message.reply_text(
            "Returning to main menu.",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text(
            "❌ Please enter a valid 6-digit OTP:\n"
            "Type /cancel to go back.",
            parse_mode="HTML"
        )
        return OTP

    # Get stored credentials from init
    user_key = context.user_data.get('userKey')
    data_key = context.user_data.get('dataKey')
    if not user_key or not data_key:
        await update.message.reply_text(
            "❌ Session expired. Please start over.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    success, result = await verify_otp(user_key, data_key, otp)
    print(f"VERIFY OTP RESULT: {json.dumps(result, indent=2)}")

    if not success:
        error_msg = result.get('message', 'Verification failed')
        await update.message.reply_text(
            f"❌ OTP verification failed.\nReason: {error_msg}\n\n"
            "Enter the correct OTP or type /cancel:",
            parse_mode="HTML"
        )
        return OTP

    token = result.get('accessToken')
    if not token:
        await update.message.reply_text(
            "❌ Server did not return access token. Please start over.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    update_user(user_id, jwt_token=token)
    context.user_data['jwt'] = token
    print(f"JWT TOKEN SAVED: {token[:50]}...")

    # ===== STEP 1: SELECT PACK =====
    await context.bot.send_message(user_id, "⏳ Selecting pack...", parse_mode="HTML")
    pack_success, pack_result = await select_pack(user_key, data_key)
    print(f"SELECT PACK RESULT: {json.dumps(pack_result, indent=2)}")
    if not pack_success:
        err = pack_result.get('message', 'Pack selection failed')
        print(f"PACK SELECTION FAILED: {err}")
        await update.message.reply_text(
            f"❌ Pack selection failed.\nServer: {err}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print("PACK SELECTION SUCCESS")

    # ===== STEP 2: SELECT VIBE =====
    await asyncio.sleep(0.5)
    await context.bot.send_message(user_id, "⏳ Selecting vibe...", parse_mode="HTML")
    vibe_success, vibe_result = await select_vibe(user_key, data_key)
    print(f"SELECT VIBE RESULT: {json.dumps(vibe_result, indent=2)}")
    if not vibe_success:
        err = vibe_result.get('message', 'Vibe selection failed')
        print(f"VIBE SELECTION FAILED: {err}")
        await update.message.reply_text(
            f"❌ Vibe selection failed.\nServer: {err}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print("VIBE SELECTION SUCCESS")

    # ===== STEP 3: DOWNLOAD IMAGE =====
    await asyncio.sleep(0.5)
    await context.bot.send_message(user_id, "⏳ Downloading image...", parse_mode="HTML")
    img_ok, image_data = await download_image(IMAGE_URL)
    if not img_ok or not image_data:
        print("IMAGE DOWNLOAD FAILED")
        await update.message.reply_text(
            "❌ Failed to download image. Please try again.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print(f"IMAGE DOWNLOADED: {len(image_data)} bytes")

    # ===== STEP 4: UPLOAD IMAGE =====
    await context.bot.send_message(user_id, "⏳ Uploading image...", parse_mode="HTML")
    upload_success, upload_result = await upload_image(user_key, data_key, image_data, IMAGE_NAME)
    print(f"UPLOAD IMAGE RESULT: {json.dumps(upload_result, indent=2)}")
    if not upload_success:
        err = upload_result.get('message', 'Image upload failed')
        print(f"IMAGE UPLOAD FAILED: {err}")
        await update.message.reply_text(
            f"❌ Image upload failed.\nServer: {err}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print("IMAGE UPLOAD SUCCESS")

    await update.message.reply_text(
        "📱 **Please enter your UPI-registered mobile number.**\n\n"
        "This is the number linked to your UPI account:\n"
        "Example: 9876543210\n\n"
        "Type /cancel to go back.",
        parse_mode="HTML"
    )
    return UPI

async def upi_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    upi_number = update.message.text.strip()

    if upi_number in ["🔙 Back", "Back", "/cancel"]:
        await update.message.reply_text(
            "Returning to main menu.",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    if not upi_number.isdigit() or len(upi_number) != 10:
        await update.message.reply_text(
            "❌ Please enter a valid 10-digit mobile number:\n"
            "Type /cancel to go back.",
            parse_mode="HTML"
        )
        return UPI

    user_key = context.user_data.get('userKey')
    data_key = context.user_data.get('dataKey')

    if not user_key or not data_key:
        await update.message.reply_text(
            "❌ Session expired. Please start over.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    # ===== STEP 1: SUBMIT UPI =====
    # HAR note: First attempt may return 400, later attempts return 200
    await context.bot.send_message(user_id, "⏳ Submitting UPI...", parse_mode="HTML")
    upi_success, upi_result = await submit_upi(user_key, data_key, upi_number)
    print(f"SUBMIT UPI RESULT: {json.dumps(upi_result, indent=2)}")
    if not upi_success:
        # First attempt may return 400 per HAR spec - retry once
        print("UPI first attempt returned non-200. Retrying once (per HAR pattern)...")
        await asyncio.sleep(1)
        upi_success, upi_result = await submit_upi(user_key, data_key, upi_number)
        print(f"SUBMIT UPI RETRY RESULT: {json.dumps(upi_result, indent=2)}")
    if not upi_success:
        err = upi_result.get('message', 'UPI submission failed')
        print(f"UPI SUBMISSION FAILED AFTER RETRY: {err}")
        await update.message.reply_text(
            f"❌ UPI submission failed.\nServer: {err}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print("UPI SUBMISSION SUCCESS")

    # ===== STEP 2: GET PACK PROGRESS =====
    await asyncio.sleep(0.5)
    await context.bot.send_message(user_id, "⏳ Checking pack progress...", parse_mode="HTML")
    pack_prog_success, pack_prog_result = await get_pack_progress(user_key, data_key)
    print(f"GET PACK PROGRESS RESULT: {json.dumps(pack_prog_result, indent=2)}")
    if not pack_prog_success:
        err = pack_prog_result.get('message', 'Pack progress check failed')
        print(f"PACK PROGRESS CHECK FAILED: {err}")
        await update.message.reply_text(
            f"❌ Pack progress check failed.\nServer: {err}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print("PACK PROGRESS CHECK SUCCESS")

    # ===== STEP 3: GET STICK PROGRESS =====
    await asyncio.sleep(0.5)
    await context.bot.send_message(user_id, "⏳ Checking stick progress...", parse_mode="HTML")
    stick_prog_success, stick_prog_result = await get_stick_progress(user_key, data_key)
    print(f"GET STICK PROGRESS RESULT: {json.dumps(stick_prog_result, indent=2)}")
    if not stick_prog_success:
        err = stick_prog_result.get('message', 'Stick progress check failed')
        print(f"STICK PROGRESS CHECK FAILED: {err}")
        await update.message.reply_text(
            f"❌ Stick progress check failed.\nServer: {err}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print("STICK PROGRESS CHECK SUCCESS")

    # ===== STEP 4: CLICK TRACK =====
    await asyncio.sleep(0.5)
    await context.bot.send_message(user_id, "⏳ Finalizing...", parse_mode="HTML")
    track_success, track_result = await click_track(user_key, data_key)
    print(f"CLICK TRACK RESULT: {json.dumps(track_result, indent=2)}")
    if not track_success:
        err = track_result.get('message', 'Track click failed')
        print(f"CLICK TRACK FAILED: {err}")
        await update.message.reply_text(
            f"❌ Finalization failed.\nServer: {err}",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END
    print("CLICK TRACK SUCCESS")

    # ===== ALL STEPS PASSED - Record success =====
    update_user(user_id, upi_number=upi_number)
    add_process(user_id, REWARD_PER_PROCESS, upi_number)

    await update.message.reply_text(
        "✅ **Process Completed Successfully**\n\n"
        "💸 Your payment will be credited to your registered UPI number within 24\u201348 hours.\n\n"
        f"📱 UPI Number: {upi_number}\n"
        f"💰 Amount: \u20b9{REWARD_PER_PROCESS}\n"
        f"📅 Date: {datetime.now().strftime('%d-%m-%Y %H:%M')}",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(user_id == ADMIN_ID)
    )

    try:
        db_user = get_user(user_id)
        stats = get_total_stats()
        await context.bot.send_message(
            ADMIN_ID,
            f"🎯 **New Process Completed!**\n\n"
            f"👤 User: {db_user.get('first_name')} (ID: {user_id})\n"
            f"📱 Phone: {db_user.get('phone')}\n"
            f"💳 UPI: {upi_number}\n"
            f"💰 Reward: \u20b9{REWARD_PER_PROCESS}\n"
            f"📊 Total: {stats['total_processes']}",
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"ADMIN NOTIFICATION FAILED: {e}")

    return ConversationHandler.END

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Cancelled.", reply_markup=get_main_keyboard(user_id == ADMIN_ID))
    return ConversationHandler.END

# ==================== MENU FUNCTIONS ====================
async def refer_earn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{db_user['referral_code']}"

    await update.message.reply_text(
        f"🔗 **Refer & Earn**\n\n"
        f"👥 Your Referral Link:\n"
        f"<code>{ref_link}</code>\n\n"
        f"📊 Your Stats:\n"
        f"• Referrals: {db_user['referrals_count']}\n"
        f"• Process Credits: {db_user['process_credits']}\n\n"
        f"💡 **How it works:**\n"
        f"• Each friend who joins = 1 referral\n"
        f"• Every {REFERRAL_REQUIRED} referral = 1 Process Credit\n"
        f"• Each Process = \u20b9{REWARD_PER_PROCESS}\n\n"
        f"Share your link and earn!",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(user_id == ADMIN_ID)
    )

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    get_today_reset(user_id)
    db_user = get_user(user_id)
    stats = get_total_stats()

    await update.message.reply_text(
        f"📊 **Dashboard**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **Your Stats:**\n"
        f"• Name: {db_user['first_name']}\n"
        f"• 👥 Referrals: {db_user['referrals_count']}\n"
        f"• 💳 Process Credits: {db_user['process_credits']}\n"
        f"• ✅ Processes: {db_user['total_processes']}\n"
        f"• 🎁 Rewards: \u20b9{db_user['total_rewards']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📅 **Today:**\n"
        f"• Today's Processes: {db_user['today_processes']}\n"
        f"• Today's Completed: {db_user['today_completed']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 **Overall:**\n"
        f"• Total Users: {stats['total_users']}\n"
        f"• Total Processes: {stats['total_processes']}\n"
        f"• Completed: {stats['total_completed']}\n"
        f"• Pending: {stats['pending']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(user_id == ADMIN_ID)
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📞 **Support**\n\n"
        f"Need help? Join our support group:\n\n"
        f"💬 @{GROUP_USERNAME}\n\n"
        f"Click the link above to open Telegram and join the group.\n"
        f"Our team will assist you within 24 hours.",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(update.effective_user.id == ADMIN_ID)
    )

# ==================== ADMIN COMMANDS ====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return

    context.user_data['admin_mode'] = True
    stats = get_total_stats()
    await update.message.reply_text(
        f"👑 **Admin Panel**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 **Quick Stats:**\n"
        f"• Total Users: {stats['total_users']}\n"
        f"• Total Processes: {stats['total_processes']}\n"
        f"• Completed: {stats['total_completed']}\n"
        f"• Pending: {stats['pending']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Select an option:",
        parse_mode="HTML",
        reply_markup=get_admin_keyboard()
    )

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    text = update.message.text

    if text == "📊 Admin Stats":
        stats = get_total_stats()
        await update.message.reply_text(
            f"📊 **Statistics**\n\n"
            f"👥 Total Users: {stats['total_users']}\n"
            f"📋 Total Processes: {stats['total_processes']}\n"
            f"✅ Completed: {stats['total_completed']}\n"
            f"⏳ Pending: {stats['pending']}",
            parse_mode="HTML",
            reply_markup=get_admin_keyboard()
        )
        return

    if text == "👥 Users List":
        users = get_all_users()
        if not users:
            await update.message.reply_text("No users found.", reply_markup=get_admin_keyboard())
            return
        text_msg = "👤 **Users List:**\n\n"
        for uid, username, fname, phone, refs, credits, processes, banned in users[:20]:
            name = fname or username or f"User_{uid}"
            status = "🚫" if banned else "✅"
            text_msg += f"{status} {name} - Ref: {refs} | Credits: {credits} | Processes: {processes}\n"
        if len(users) > 20:
            text_msg += f"\n... and {len(users) - 20} more"
        await update.message.reply_text(text_msg, parse_mode="HTML", reply_markup=get_admin_keyboard())
        return

    if text == "➕ Add Credits":
        context.user_data['admin_action'] = 'add_credits'
        await update.message.reply_text(
            "➕ **Add Process Credits**\n\n"
            "Enter: `user_id amount`\n"
            "Example: `123456789 5`\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        return

    if text == "➖ Remove Credits":
        context.user_data['admin_action'] = 'remove_credits'
        await update.message.reply_text(
            "➖ **Remove Process Credits**\n\n"
            "Enter: `user_id amount`\n"
            "Example: `123456789 3`\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        return

    if text == "📢 Broadcast":
        context.user_data['admin_action'] = 'broadcast'
        await update.message.reply_text(
            "📢 **Broadcast**\n\n"
            "Send the message to broadcast to all users.\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        return

    if text == "📂 Export DB":
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT * FROM users')
        rows = c.fetchall()
        conn.close()
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['user_id', 'username', 'first_name', 'phone', 'upi_number',
                           'referrals_count', 'process_credits', 'total_processes', 'total_rewards'])
            for row in rows:
                writer.writerow(row[:9])
        with open(filename, 'rb') as f:
            await update.message.reply_document(document=f, filename=filename)
        os.remove(filename)
        await update.message.reply_text("✅ Database exported!", reply_markup=get_admin_keyboard())
        return

    if text == "🔙 Back to Menu":
        context.user_data['admin_mode'] = False
        context.user_data['admin_action'] = None
        await update.message.reply_text(
            "👋 Welcome back!",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return

    # If none matched, it might be input for admin action
    await admin_input_handler(update, context)

async def admin_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    text = update.message.text
    action = context.user_data.get('admin_action')

    if not action:
        return

    if text.lower() == '/cancel':
        context.user_data['admin_action'] = None
        await update.message.reply_text("Cancelled.", reply_markup=get_admin_keyboard())
        return

    if action == 'add_credits':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `user_id amount`", parse_mode="HTML")
            return
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            add_process_credits(target_id, amount)
            await update.message.reply_text(
                f"✅ Added {amount} Process Credits to user {target_id}",
                reply_markup=get_admin_keyboard()
            )
        except:
            await update.message.reply_text("❌ Invalid input.", reply_markup=get_admin_keyboard())
        context.user_data['admin_action'] = None
        return

    if action == 'remove_credits':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `user_id amount`", parse_mode="HTML")
            return
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            add_process_credits(target_id, -amount)
            await update.message.reply_text(
                f"✅ Removed {amount} Process Credits from user {target_id}",
                reply_markup=get_admin_keyboard()
            )
        except:
            await update.message.reply_text("❌ Invalid input.", reply_markup=get_admin_keyboard())
        context.user_data['admin_action'] = None
        return

    if action == 'broadcast':
        users = get_all_users()
        success = 0
        failed = 0
        for uid, username, fname, phone, refs, credits, processes, banned in users:
            if banned:
                continue
            try:
                await context.bot.send_message(
                    uid,
                    f"📢 **Announcement**\n\n{text}",
                    parse_mode="HTML"
                )
                success += 1
            except:
                failed += 1
            await asyncio.sleep(0.05)
        await update.message.reply_text(
            f"✅ Broadcast Complete!\n\nSent: {success}\nFailed: {failed}",
            reply_markup=get_admin_keyboard()
        )
        context.user_data['admin_action'] = None
        return

    await update.message.reply_text("Unknown command.", reply_markup=get_admin_keyboard())

# ==================== MAIN ====================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🏠 Start Process$'), start_process)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_handler)],
            UPI: [MessageHandler(filters.TEXT & ~filters.COMMAND, upi_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel_handler)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start_command))
    application.add_handler(CommandHandler('admin', admin_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))

    print("=" * 60)
    print("SALY V2 - TELEGRAM BOT")
    print("=" * 60)
    print(f"Bot Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Admin ID: {ADMIN_ID}")
    print(f"Channel: @{CHANNEL_USERNAME}")
    print(f"Group: @{GROUP_USERNAME}")
    print("=" * 60)

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
