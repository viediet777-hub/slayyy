#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SLAY YOUR PLAY - TELEGRAM BOT
Complete automated claim bot with referral system, force join, and auto upload
"""

import os
import logging
import json
import sqlite3
import asyncio
import aiohttp
import aiofiles
import random
import string
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
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
REFERRAL_REQUIRED = 2
REWARD_PER_UPLOAD = 20

# ==================== DATABASE ====================
DB_PATH = "slayyourplay.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Users table
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
        upload_balance INTEGER DEFAULT 0,
        total_claims INTEGER DEFAULT 0,
        successful_uploads INTEGER DEFAULT 0,
        total_rewards INTEGER DEFAULT 0,
        today_claims INTEGER DEFAULT 0,
        today_uploads INTEGER DEFAULT 0,
        last_claim_date TEXT,
        is_banned INTEGER DEFAULT 0,
        registered_at TEXT,
        last_activity TEXT
    )''')
    
    # Referrals table
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        referred_at TEXT,
        is_valid INTEGER DEFAULT 1
    )''')
    
    # Claims table
    c.execute('''CREATE TABLE IF NOT EXISTS claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        claim_type TEXT,
        status TEXT DEFAULT 'pending',
        reward INTEGER DEFAULT 0,
        upi_number TEXT,
        created_at TEXT,
        processed_at TEXT
    )''')
    
    # Upload logs
    c.execute('''CREATE TABLE IF NOT EXISTS upload_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        image_name TEXT,
        status TEXT,
        uploaded_at TEXT
    )''')
    
    conn.commit()
    conn.close()

init_db()

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
            'upload_balance': row[9],
            'total_claims': row[10],
            'successful_uploads': row[11],
            'total_rewards': row[12],
            'today_claims': row[13],
            'today_uploads': row[14],
            'last_claim_date': row[15],
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

def add_upload_balance(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET upload_balance = upload_balance + ? WHERE user_id = ?', (amount, user_id))
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
        # Check if referral milestone reached
        c.execute('SELECT referrals_count FROM users WHERE user_id = ?', (referrer_id,))
        count = c.fetchone()[0]
        if count % REFERRAL_REQUIRED == 0:
            add_upload_balance(referrer_id, 1)
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def add_claim(user_id: int, reward: int, upi_number: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('''INSERT INTO claims (user_id, claim_type, status, reward, upi_number, created_at)
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (user_id, 'upload', 'pending', reward, upi_number, now))
    c.execute('''UPDATE users SET 
                 total_claims = total_claims + 1,
                 successful_uploads = successful_uploads + 1,
                 total_rewards = total_rewards + ?,
                 today_claims = today_claims + 1,
                 today_uploads = today_uploads + 1,
                 last_claim_date = ?,
                 upload_balance = upload_balance - 1
                 WHERE user_id = ?''',
              (reward, now, user_id))
    conn.commit()
    conn.close()

def get_today_reset(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute('SELECT last_claim_date FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    if row and row[0] and row[0].startswith(today):
        return False
    # Reset daily counters
    c.execute('UPDATE users SET today_claims = 0, today_uploads = 0 WHERE user_id = ?', (user_id,))
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
    c.execute('SELECT SUM(total_claims) FROM users')
    total_claims = c.fetchone()[0] or 0
    c.execute('SELECT SUM(successful_uploads) FROM users')
    total_uploads = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM claims WHERE status = "pending"')
    pending_claims = c.fetchone()[0]
    conn.close()
    return {
        'total_users': total_users,
        'total_claims': total_claims,
        'total_uploads': total_uploads,
        'pending_claims': pending_claims
    }

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id, username, first_name, phone, referrals_count, upload_balance, total_claims, is_banned FROM users ORDER BY registered_at DESC')
    rows = c.fetchall()
    conn.close()
    return rows

# ==================== API FUNCTIONS ====================
async def api_request(method: str, endpoint: str, user_key: str = None, data: dict = None, files: dict = None, token: str = None) -> Tuple[bool, dict]:
    """Make API request with retry logic"""
    url = f"{BASE_URL}{endpoint}"
    if user_key:
        url = url.replace('{userKey}', user_key)
    
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with aiohttp.ClientSession() as session:
                if method == 'GET':
                    async with session.get(url, headers=headers, timeout=30) as resp:
                        result = await resp.json()
                        return resp.status == 200, result
                elif method == 'POST':
                    if files:
                        async with session.post(url, data=data, files=files, timeout=60) as resp:
                            result = await resp.json()
                            return resp.status == 200, result
                    else:
                        async with session.post(url, json=data, headers=headers, timeout=30) as resp:
                            try:
                                result = await resp.json()
                            except:
                                result = {'statusCode': resp.status, 'message': await resp.text()}
                            return resp.status in [200, 201, 202], result
        except Exception as e:
            if attempt == max_retries - 1:
                return False, {'error': str(e)}
            await asyncio.sleep(2 ** attempt)
    return False, {'error': 'Max retries exceeded'}

async def register_user(phone: str) -> Tuple[bool, dict]:
    """Register user with phone number"""
    return await api_request('POST', '/api/users/register/{userKey}', phone, {'phone': phone})

async def send_otp(phone: str) -> Tuple[bool, dict]:
    """Send OTP to phone"""
    return await api_request('POST', '/api/users/sendOTP/{userKey}', phone, {'phone': phone})

async def verify_otp(phone: str, otp: str) -> Tuple[bool, dict]:
    """Verify OTP and get token"""
    return await api_request('POST', '/api/users/verifyOTP/{userKey}', phone, {'phone': phone, 'otp': otp})

async def select_pack(phone: str, token: str) -> Tuple[bool, dict]:
    """Select pack automatically"""
    # Try to get available packs first
    success, result = await api_request('POST', '/api/users/getPackProgress/{userKey}', phone, token=token)
    if success:
        packs = result.get('data', {}).get('packs', [])
        if packs:
            # Select first available pack
            pack_id = packs[0].get('id')
            if pack_id:
                return await api_request('POST', '/api/users/selectPack/{userKey}', phone, {'packId': pack_id}, token=token)
    return await api_request('POST', '/api/users/selectPack/{userKey}', phone, {}, token=token)

async def select_vibe(phone: str, token: str) -> Tuple[bool, dict]:
    """Select vibe/stick automatically"""
    return await api_request('POST', '/api/users/selectVibe/{userKey}', phone, {}, token=token)

async def upload_image(phone: str, token: str, image_data: bytes, image_name: str) -> Tuple[bool, dict]:
    """Upload image to API"""
    files = {'media': (image_name, image_data, 'image/jpeg')}
    return await api_request('POST', '/api/users/uploadImage/{userKey}', phone, data={}, files=files, token=token)

async def submit_upi(phone: str, upi_number: str, token: str) -> Tuple[bool, dict]:
    """Submit UPI number"""
    return await api_request('POST', '/api/users/getUpiNo/{userKey}', phone, {'upi': upi_number}, token=token)

async def download_image(url: str) -> Tuple[bool, bytes]:
    """Download image from URL"""
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
async def check_force_join(user_id: int) -> Tuple[bool, bool]:
    """Check if user joined channel and group"""
    channel_joined = False
    group_joined = False
    
    try:
        # Check channel
        member = await application.bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        channel_joined = member.status in ['member', 'administrator', 'creator']
    except:
        pass
    
    try:
        # Check group
        member = await application.bot.get_chat_member(f"@{GROUP_USERNAME}", user_id)
        group_joined = member.status in ['member', 'administrator', 'creator']
    except:
        pass
    
    return channel_joined, group_joined

def get_force_join_keyboard():
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("👥 Join Group", url=f"https://t.me/{GROUP_USERNAME}")],
        [InlineKeyboardButton("✅ Check Again", callback_data="check_join")]
    ])
    return kb

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or "User"
    
    # Check ban
    db_user = get_user(user_id)
    if db_user and db_user.get('is_banned'):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return
    
    # Check force join
    channel_joined, group_joined = await check_force_join(user_id)
    if not channel_joined or not group_joined:
        msg = "🔒 **Access Restricted**\n\n"
        if not channel_joined:
            msg += "❌ You haven't joined our channel.\n"
        if not group_joined:
            msg += "❌ You haven't joined our group.\n"
        msg += "\nPlease join both to access the bot."
        await update.message.reply_text(msg, reply_markup=get_force_join_keyboard(), parse_mode="HTML")
        return
    
    # Create user if not exists
    if not db_user:
        create_user(user_id, username, first_name)
        db_user = get_user(user_id)
    
    # Check referral from start param
    if context.args and context.args[0].startswith('ref_'):
        ref_code = context.args[0].replace('ref_', '')
        referrer_id = get_user_by_referral_code(ref_code)
        if referrer_id and referrer_id != user_id:
            add_referral(referrer_id, user_id)
            # Notify referrer
            try:
                await application.bot.send_message(
                    referrer_id,
                    f"🎉 **New Referral!**\n\n"
                    f"@{username or first_name} joined using your referral link.\n"
                    f"Referrals: {get_user(referrer_id)['referrals_count']}\n"
                    f"Upload Balance: {get_user(referrer_id)['upload_balance']}",
                    parse_mode="HTML"
                )
            except:
                pass
    
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Claim", callback_data="start_claim")],
        [InlineKeyboardButton("👥 Refer & Earn", callback_data="refer")],
        [InlineKeyboardButton("💰 Upload Balance", callback_data="balance")],
        [InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("🎁 Rewards", callback_data="rewards")],
        [InlineKeyboardButton("📞 Support", callback_data="support")]
    ])
    
    text = f"""
🏠 **Home**

Welcome back, {db_user['first_name']}! 👋

📊 Quick Stats:
• 👥 Referrals: {db_user['referrals_count']}
• 🖼 Upload Balance: {db_user['upload_balance']}
• ✅ Claims: {db_user['total_claims']}
• 🎁 Rewards: {db_user['total_rewards']}

Select an option below:
"""
    
    if update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# ==================== CLAIM FLOW ====================
async def start_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    db_user = get_user(user_id)
    
    # Check upload balance
    if db_user['upload_balance'] <= 0:
        await query.edit_message_text(
            "❌ You don't have any upload balance.\n\n"
            f"Invite **{REFERRAL_REQUIRED} friends** to unlock 1 image upload.\n\n"
            f"Current Referrals: {db_user['referrals_count']}\n"
            f"Need: {REFERRAL_REQUIRED - (db_user['referrals_count'] % REFERRAL_REQUIRED)} more for next upload.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Get Referral Link", callback_data="refer")],
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ])
        )
        return
    
    # Check if already has phone
    if db_user.get('phone'):
        await query.edit_message_text(
            "📱 You're already registered!\n"
            "Starting claim process...",
            parse_mode="HTML"
        )
        await process_claim(query.from_user.id, context)
        return
    
    # Start phone registration
    await query.edit_message_text(
        "📱 **Enter your mobile number**\n\n"
        "Please enter your 10-digit mobile number:\n"
        "(Example: 9876543210)\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )
    context.user_data['state'] = 'phone'
    return PHONE

async def phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    
    if phone.lower() == '/cancel':
        await update.message.reply_text("❌ Claim cancelled.")
        return ConversationHandler.END
    
    if not phone.isdigit() or len(phone) != 10:
        await update.message.reply_text(
            "❌ Please enter a valid 10-digit mobile number.\n\n"
            "Enter your number:",
            parse_mode="HTML"
        )
        return PHONE
    
    # Register user
    context.user_data['phone'] = phone
    
    # Save phone to database
    update_user(user_id, phone=phone)
    
    # Register with API
    success, result = await register_user(phone)
    if not success:
        await update.message.reply_text(
            f"❌ Registration failed: {result.get('message', 'Unknown error')}\n\n"
            "Please try again later.",
            parse_mode="HTML"
        )
        return ConversationHandler.END
    
    # Send OTP
    success, result = await send_otp(phone)
    if not success:
        await update.message.reply_text(
            f"❌ Failed to send OTP: {result.get('message', 'Unknown error')}\n\n"
            "Please try again.",
            parse_mode="HTML"
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"✅ OTP sent to {phone}!\n\n"
        "📱 Enter the 6-digit OTP:\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )
    return OTP

async def otp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    otp = update.message.text.strip()
    phone = context.user_data.get('phone')
    
    if otp.lower() == '/cancel':
        await update.message.reply_text("❌ OTP verification cancelled.")
        return ConversationHandler.END
    
    if not otp.isdigit() or len(otp) != 6:
        await update.message.reply_text(
            "❌ Please enter a valid 6-digit OTP:\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        return OTP
    
    # Verify OTP
    success, result = await verify_otp(phone, otp)
    if not success:
        await update.message.reply_text(
            f"❌ OTP verification failed: {result.get('message', 'Invalid OTP')}\n\n"
            "Please try again.",
            parse_mode="HTML"
        )
        return OTP
    
    # Save JWT token
    token = result.get('accessToken')
    if token:
        update_user(user_id, jwt_token=token)
    
    await update.message.reply_text(
        "✅ **OTP Verified Successfully!**\n\n"
        "🔄 Processing your claim automatically...",
        parse_mode="HTML"
    )
    
    # Process claim automatically
    await process_claim(user_id, context)
    return ConversationHandler.END

async def process_claim(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Automatic claim process - all steps happen here without user input"""
    db_user = get_user(user_id)
    phone = db_user.get('phone')
    token = db_user.get('jwt_token')
    
    if not phone or not token:
        await context.bot.send_message(
            user_id,
            "❌ Missing authentication. Please start over.",
            parse_mode="HTML"
        )
        return
    
    # Step 1: Select Pack (automatic)
    await context.bot.send_message(
        user_id,
        "📦 Selecting best pack for you...",
        parse_mode="HTML"
    )
    
    success, result = await select_pack(phone, token)
    if not success:
        await context.bot.send_message(
            user_id,
            f"⚠️ Pack selection issue: {result.get('message', 'Unknown')}\n"
            "Continuing with default pack...",
            parse_mode="HTML"
        )
    
    await asyncio.sleep(1)
    
    # Step 2: Select Vibe/Stick (automatic)
    await context.bot.send_message(
        user_id,
        "🎯 Selecting vibe/stick for you...",
        parse_mode="HTML"
    )
    
    success, result = await select_vibe(phone, token)
    if not success:
        await context.bot.send_message(
            user_id,
            f"⚠️ Vibe selection issue: {result.get('message', 'Unknown')}\n"
            "Continuing with default vibe...",
            parse_mode="HTML"
        )
    
    await asyncio.sleep(1)
    
    # Step 3: Download image (automatic)
    await context.bot.send_message(
        user_id,
        "📥 Downloading image for upload...",
        parse_mode="HTML"
    )
    
    success, image_data = await download_image(IMAGE_URL)
    if not success or not image_data:
        await context.bot.send_message(
            user_id,
            "❌ Failed to download image. Please try again later.",
            parse_mode="HTML"
        )
        return
    
    await asyncio.sleep(1)
    
    # Step 4: Upload image (automatic)
    await context.bot.send_message(
        user_id,
        "📤 Uploading image...",
        parse_mode="HTML"
    )
    
    success, result = await upload_image(phone, token, image_data, IMAGE_NAME)
    if not success:
        await context.bot.send_message(
            user_id,
            f"❌ Image upload failed: {result.get('message', 'Unknown error')}\n\n"
            "Please try again later.",
            parse_mode="HTML"
        )
        return
    
    await context.bot.send_message(
        user_id,
        "✅ Image uploaded successfully!",
        parse_mode="HTML"
    )
    
    await asyncio.sleep(1)
    
    # Step 5: Ask UPI number
    await context.bot.send_message(
        user_id,
        "📱 **Enter your UPI registered mobile number.**\n\n"
        "Please enter the mobile number linked to your UPI:\n"
        "(Example: 9876543210)\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )
    context.user_data['state'] = 'upi'
    return UPI

async def upi_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    upi_number = update.message.text.strip()
    
    if upi_number.lower() == '/cancel':
        await update.message.reply_text("❌ Claim cancelled.")
        return ConversationHandler.END
    
    if not upi_number.isdigit() or len(upi_number) != 10:
        await update.message.reply_text(
            "❌ Please enter a valid 10-digit mobile number:\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        return UPI
    
    db_user = get_user(user_id)
    phone = db_user.get('phone')
    token = db_user.get('jwt_token')
    
    # Save UPI number
    update_user(user_id, upi_number=upi_number)
    
    # Submit UPI number to API
    success, result = await submit_upi(phone, upi_number, token)
    
    # Add claim record
    add_claim(user_id, REWARD_PER_UPLOAD, upi_number)
    
    await update.message.reply_text(
        f"""
✅ **Claim Submitted Successfully**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💸 Your reward/payment will be credited to your registered UPI number within 24–48 hours.

📱 UPI Number: {upi_number}
💰 Reward: ₹{REWARD_PER_UPLOAD}
📅 Date: {datetime.now().strftime('%d-%m-%Y %H:%M')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ])
    )
    
    # Notify admin
    try:
        stats = get_total_stats()
        await application.bot.send_message(
            ADMIN_ID,
            f"🎯 **New Claim!**\n\n"
            f"👤 User: {db_user.get('first_name')} (ID: {user_id})\n"
            f"📱 Phone: {phone}\n"
            f"💳 UPI: {upi_number}\n"
            f"💰 Reward: ₹{REWARD_PER_UPLOAD}\n"
            f"📊 Total Claims: {stats['total_claims']}",
            parse_mode="HTML"
        )
    except:
        pass
    
    return ConversationHandler.END

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# ==================== CALLBACK HANDLERS ====================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "check_join":
        channel_joined, group_joined = await check_force_join(user_id)
        if channel_joined and group_joined:
            db_user = get_user(user_id)
            if db_user and db_user.get('is_banned'):
                await query.edit_message_text("🚫 You are banned from using this bot.")
                return
            await query.edit_message_text("✅ All joined! Welcome to Slay Your Play Bot!")
            await show_main_menu(update, context)
        else:
            msg = "🔒 **Access Restricted**\n\n"
            if not channel_joined:
                msg += "❌ You haven't joined our channel.\n"
            if not group_joined:
                msg += "❌ You haven't joined our group.\n"
            msg += "\nPlease join both to access the bot."
            await query.edit_message_text(msg, reply_markup=get_force_join_keyboard(), parse_mode="HTML")
    
    elif data == "home":
        await show_main_menu(update, context)
    
    elif data == "start_claim":
        await start_claim(update, context)
    
    elif data == "refer":
        db_user = get_user(user_id)
        bot_info = await application.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{db_user['referral_code']}"
        
        await query.edit_message_text(
            f"""
🔗 **Refer & Earn**

👥 Your Referral Link:
<code>{ref_link}</code>

📊 Your Stats:
• Referrals: {db_user['referrals_count']}
• Upload Balance: {db_user['upload_balance']}

💡 **How it works:**
• Each friend who joins = 1 referral
• Every {REFERRAL_REQUIRED} referrals = 1 free upload
• Each upload = ₹{REWARD_PER_UPLOAD}

Share your link with friends and earn rewards! 🎉
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Share Link", switch_inline_query=f"Join Slay Your Play and earn rewards! {ref_link}")],
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ])
        )
    
    elif data == "balance":
        db_user = get_user(user_id)
        await query.edit_message_text(
            f"""
💰 **Upload Balance**

🖼 Available Uploads: {db_user['upload_balance']}

📊 Referral Progress:
• Referrals: {db_user['referrals_count']}
• Next Upload: {REFERRAL_REQUIRED - (db_user['referrals_count'] % REFERRAL_REQUIRED)} more referrals

Each upload gives you ₹{REWARD_PER_UPLOAD} reward! 🎁
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Refer & Earn", callback_data="refer")],
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ])
        )
    
    elif data == "dashboard":
        db_user = get_user(user_id)
        get_today_reset(user_id)
        db_user = get_user(user_id)
        stats = get_total_stats()
        
        await query.edit_message_text(
            f"""
📊 **Dashboard**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

👤 **Your Stats:**
• Name: {db_user['first_name']}
• Telegram ID: {user_id}
• 📱 Phone: {db_user.get('phone', 'Not set')}
• 👥 Referrals: {db_user['referrals_count']}
• 🖼 Upload Balance: {db_user['upload_balance']}
• ✅ Claims: {db_user['total_claims']}
• 🎁 Rewards: ₹{db_user['total_rewards']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📅 **Today:**
• Today's Claims: {db_user['today_claims']}
• Today's Uploads: {db_user['today_uploads']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 **Overall Statistics:**
• Total Users: {stats['total_users']}
• Total Claims: {stats['total_claims']}
• Successful Uploads: {stats['total_uploads']}
• Pending Claims: {stats['pending_claims']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="dashboard")],
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ])
        )
    
    elif data == "rewards":
        db_user = get_user(user_id)
        await query.edit_message_text(
            f"""
🎁 **Rewards**

Total Rewards: ₹{db_user['total_rewards']}

📊 Reward Details:
• Per Upload: ₹{REWARD_PER_UPLOAD}
• Successful Uploads: {db_user['successful_uploads']}
• Total Claims: {db_user['total_claims']}

💡 Keep uploading to earn more rewards!
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ])
        )
    
    elif data == "support":
        await query.edit_message_text(
            f"""
📞 **Support**

Need help? Contact us:

👑 Admin: @{get_admin_username()}

📢 Channel: @{CHANNEL_USERNAME}
👥 Group: @{GROUP_USERNAME}

💬 Response time: Usually within 24 hours.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**FAQ:**
❓ How do I get upload balance?
→ Invite {REFERRAL_REQUIRED} friends.

❓ How much do I earn?
→ ₹{REWARD_PER_UPLOAD} per successful upload.

❓ When do I get paid?
→ Within 24-48 hours after claim.
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="home")]
            ])
        )

async def get_admin_username():
    try:
        admin = await application.bot.get_chat(ADMIN_ID)
        return admin.username or "admin"
    except:
        return "admin"

# ==================== ADMIN COMMANDS ====================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👤 User Management", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_balance")],
        [InlineKeyboardButton("➖ Remove Balance", callback_data="admin_remove_balance")],
        [InlineKeyboardButton("📂 Export DB", callback_data="admin_export")]
    ])
    
    stats = get_total_stats()
    await update.message.reply_text(
        f"""
👑 **Admin Panel**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 **Quick Stats:**
• Total Users: {stats['total_users']}
• Total Claims: {stats['total_claims']}
• Total Uploads: {stats['total_uploads']}
• Pending Claims: {stats['pending_claims']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Select an option:
""",
        parse_mode="HTML",
        reply_markup=kb
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ Unauthorized!")
        return
    
    data = query.data
    
    if data == "admin_stats":
        stats = get_total_stats()
        await query.edit_message_text(
            f"""
📊 **Statistics**

👥 Total Users: {stats['total_users']}
📋 Total Claims: {stats['total_claims']}
🖼 Total Uploads: {stats['total_uploads']}
⏳ Pending Claims: {stats['pending_claims']}
""",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
            ])
        )
    
    elif data == "admin_users":
        users = get_all_users()
        if not users:
            await query.edit_message_text("No users found.")
            return
        
        text = "👤 **Users List:**\n\n"
        for uid, username, fname, phone, refs, balance, claims, banned in users[:20]:
            name = fname or username or f"User_{uid}"
            status = "🚫" if banned else "✅"
            text += f"{status} {name} - Ref: {refs} | Bal: {balance} | Claims: {claims}\n"
        
        if len(users) > 20:
            text += f"\n... and {len(users) - 20} more"
        
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
            ])
        )
    
    elif data == "admin_add_balance":
        await query.edit_message_text(
            "➕ **Add Upload Balance**\n\n"
            "Enter: `user_id amount`\n"
            "Example: `123456789 5`\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        context.user_data['admin_action'] = 'add_balance'
    
    elif data == "admin_remove_balance":
        await query.edit_message_text(
            "➖ **Remove Upload Balance**\n\n"
            "Enter: `user_id amount`\n"
            "Example: `123456789 3`\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        context.user_data['admin_action'] = 'remove_balance'
    
    elif data == "admin_broadcast":
        await query.edit_message_text(
            "📢 **Broadcast**\n\n"
            "Send the message to broadcast to all users.\n"
            "Send /cancel to abort.",
            parse_mode="HTML"
        )
        context.user_data['admin_action'] = 'broadcast'
    
    elif data == "admin_export":
        # Export database
        import csv
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT * FROM users')
        rows = c.fetchall()
        conn.close()
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['user_id', 'username', 'first_name', 'phone', 'upi_number', 
                           'referrals_count', 'upload_balance', 'total_claims', 'total_rewards'])
            for row in rows:
                writer.writerow(row[:9])  # First 9 columns
        
        with open(filename, 'rb') as f:
            await query.message.reply_document(document=f, filename=filename)
        os.remove(filename)
        
        await query.edit_message_text("✅ Database exported!", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
        ]))

async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    text = update.message.text
    action = context.user_data.get('admin_action')
    
    if not action:
        return
    
    if text.lower() == '/cancel':
        context.user_data['admin_action'] = None
        await update.message.reply_text("❌ Cancelled.")
        return
    
    if action == 'add_balance':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `user_id amount`", parse_mode="HTML")
            return
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            add_upload_balance(target_id, amount)
            await update.message.reply_text(f"✅ Added {amount} upload balance to user {target_id}")
        except:
            await update.message.reply_text("❌ Invalid input.")
        context.user_data['admin_action'] = None
        await admin_command(update, context)
    
    elif action == 'remove_balance':
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Invalid format. Use: `user_id amount`", parse_mode="HTML")
            return
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            add_upload_balance(target_id, -amount)
            await update.message.reply_text(f"✅ Removed {amount} upload balance from user {target_id}")
        except:
            await update.message.reply_text("❌ Invalid input.")
        context.user_data['admin_action'] = None
        await admin_command(update, context)
    
    elif action == 'broadcast':
        users = get_all_users()
        success = 0
        failed = 0
        msg = await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
        
        for uid, username, fname, phone, refs, balance, claims, banned in users:
            if banned:
                continue
            try:
                await application.bot.send_message(
                    uid,
                    f"📢 **Announcement**\n\n{text}",
                    parse_mode="HTML"
                )
                success += 1
            except:
                failed += 1
            await asyncio.sleep(0.05)
        
        await msg.edit_text(f"✅ Broadcast Complete!\n\n✅ Sent: {success}\n❌ Failed: {failed}")
        context.user_data['admin_action'] = None
        await admin_command(update, context)

# ==================== MAIN ====================
def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler for claim flow
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_handler)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_handler)],
            UPI: [MessageHandler(filters.TEXT & ~filters.COMMAND, upi_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel_handler)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(callback_handler, pattern="^(?!admin_)"))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    application.add_handler(CommandHandler('admin', admin_command))
    application.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_ID), admin_message_handler))
    
    # Start bot
    print("=" * 60)
    print("🤖 SLAY YOUR PLAY - TELEGRAM BOT")
    print("=" * 60)
    print(f"Bot Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Admin ID: {ADMIN_ID}")
    print(f"Channel: @{CHANNEL_USERNAME}")
    print(f"Group: @{GROUP_USERNAME}")
    print("=" * 60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
