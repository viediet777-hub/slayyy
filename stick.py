#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import json
import sqlite3
import asyncio
import aiohttp
import random
import string
import csv
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
async def api_request(method: str, endpoint: str, user_key: str = None, data: dict = None, files: dict = None, token: str = None) -> Tuple[bool, dict]:
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
    return await api_request('POST', '/api/users/register/{userKey}', phone, {'phone': phone})

async def send_otp(phone: str) -> Tuple[bool, dict]:
    return await api_request('POST', '/api/users/sendOTP/{userKey}', phone, {'phone': phone})

async def verify_otp(phone: str, otp: str) -> Tuple[bool, dict]:
    return await api_request('POST', '/api/users/verifyOTP/{userKey}', phone, {'phone': phone, 'otp': otp})

async def select_pack(phone: str, token: str) -> Tuple[bool, dict]:
    success, result = await api_request('POST', '/api/users/getPackProgress/{userKey}', phone, token=token)
    if success:
        packs = result.get('data', {}).get('packs', [])
        if packs:
            pack_id = packs[0].get('id')
            if pack_id:
                return await api_request('POST', '/api/users/selectPack/{userKey}', phone, {'packId': pack_id}, token=token)
    return await api_request('POST', '/api/users/selectPack/{userKey}', phone, {}, token=token)

async def select_vibe(phone: str, token: str) -> Tuple[bool, dict]:
    return await api_request('POST', '/api/users/selectVibe/{userKey}', phone, {}, token=token)

async def upload_image(phone: str, token: str, image_data: bytes, image_name: str) -> Tuple[bool, dict]:
    files = {'media': (image_name, image_data, 'image/jpeg')}
    return await api_request('POST', '/api/users/uploadImage/{userKey}', phone, data={}, files=files, token=token)

async def submit_upi(phone: str, upi_number: str, token: str) -> Tuple[bool, dict]:
    return await api_request('POST', '/api/users/getUpiNo/{userKey}', phone, {'upi': upi_number}, token=token)

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
async def check_force_join(user_id: int, bot) -> Tuple[bool, bool]:
    channel_joined = False
    group_joined = False
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        channel_joined = member.status in ['member', 'administrator', 'creator']
    except:
        pass
    try:
        member = await bot.get_chat_member(f"@{GROUP_USERNAME}", user_id)
        group_joined = member.status in ['member', 'administrator', 'creator']
    except:
        pass
    return channel_joined, group_joined

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

    channel_joined, group_joined = await check_force_join(user_id, context.bot)
    if not channel_joined or not group_joined:
        msg = "🔒 **Access Restricted**\n\n"
        if not channel_joined:
            msg += "❌ You haven't joined our channel.\n"
        if not group_joined:
            msg += "❌ You haven't joined our group.\n"
        msg += "\n📢 Channel: https://t.me/" + CHANNEL_USERNAME + "\n"
        msg += "👥 Group: https://t.me/" + GROUP_USERNAME + "\n\n"
        msg += "After joining both, click the button below."
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
        channel_joined, group_joined = await check_force_join(user_id, context.bot)
        if channel_joined and group_joined:
            db_user = get_user(user_id)
            if db_user and db_user.get('is_banned'):
                await update.message.reply_text("🚫 You are banned from using this bot.")
                return
            await update.message.reply_text(
                "✅ All joined! Welcome!\n\nSelect an option below:",
                reply_markup=get_main_keyboard(user_id == ADMIN_ID)
            )
        else:
            msg = "🔒 **Access Restricted**\n\n"
            if not channel_joined:
                msg += "❌ You haven't joined our channel.\n"
            if not group_joined:
                msg += "❌ You haven't joined our group.\n"
            msg += "\n📢 Channel: https://t.me/" + CHANNEL_USERNAME + "\n"
            msg += "👥 Group: https://t.me/" + GROUP_USERNAME + "\n\n"
            msg += "After joining both, click the button below."
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

    await update.message.reply_text("⏳ Registering...", parse_mode="HTML")
    await register_user(phone)

    await update.message.reply_text("⏳ Sending OTP...", parse_mode="HTML")
    success, result = await send_otp(phone)
    if not success:
        await update.message.reply_text(
            "❌ Failed to send OTP. Please try again later.\n\n"
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
    phone = context.user_data.get('phone')

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

    success, result = await verify_otp(phone, otp)
    if not success:
        await update.message.reply_text(
            "❌ OTP verification failed. Please try again.\n\n"
            "Enter the correct OTP or type /cancel:",
            parse_mode="HTML"
        )
        return OTP

    token = result.get('accessToken')
    if token:
        update_user(user_id, jwt_token=token)

    await update.message.reply_text("✅ Verified! Processing your request...", parse_mode="HTML")

    db_user = get_user(user_id)
    phone = db_user.get('phone')
    token = db_user.get('jwt_token')

    if not phone or not token:
        await update.message.reply_text(
            "❌ Session error. Please start over.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(user_id == ADMIN_ID)
        )
        return ConversationHandler.END

    await context.bot.send_message(user_id, "⏳ Processing step 1...", parse_mode="HTML")
    await select_pack(phone, token)
    await asyncio.sleep(0.5)

    await context.bot.send_message(user_id, "⏳ Processing step 2...", parse_mode="HTML")
    await select_vibe(phone, token)
    await asyncio.sleep(0.5)

    await context.bot.send_message(user_id, "⏳ Finalizing...", parse_mode="HTML")
    success_img, image_data = await download_image(IMAGE_URL)
    if success_img and image_data:
        await upload_image(phone, token, image_data, IMAGE_NAME)
    await asyncio.sleep(0.5)

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

    db_user = get_user(user_id)
    phone = db_user.get('phone')
    token = db_user.get('jwt_token')

    update_user(user_id, upi_number=upi_number)
    await submit_upi(phone, upi_number, token)
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
        stats = get_total_stats()
        await context.bot.send_message(
            ADMIN_ID,
            f"🎯 **New Process Completed!**\n\n"
            f"👤 User: {db_user.get('first_name')} (ID: {user_id})\n"
            f"📱 Phone: {phone}\n"
            f"💳 UPI: {upi_number}\n"
            f"💰 Reward: \u20b9{REWARD_PER_PROCESS}\n"
            f"📊 Total: {stats['total_processes']}",
            parse_mode="HTML"
        )
    except:
        pass

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
