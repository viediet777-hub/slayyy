#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# NRTECNO SYSTEM - SLAY BOT v3.0
# FIXED: Valid code detection + Better UI + Mobile hiding

import os
import logging
import telebot
import json
import time
import threading
import random
import sqlite3
import subprocess
import sys
import re
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==================== CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logging.error("BOT_TOKEN environment variable not set.")
    exit(1)

ADMIN_ID = int(os.environ.get("ADMIN_ID", 1364476174))
CHANNEL_USERNAME = "viedietlooters"

# Credit System
NEW_USER_BONUS = 0
REFERRAL_BONUS = 1
REFERRAL_STAY_HOURS = 0
SCAN_COST = 1

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
DB_PATH = "slay_bot.db"

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance INTEGER DEFAULT 0,
        status TEXT DEFAULT 'ACTIVE',
        registered_at TEXT,
        last_used TEXT,
        referred_by INTEGER DEFAULT NULL,
        referral_code TEXT UNIQUE,
        slay_logged_in INTEGER DEFAULT 0,
        slay_codes_found INTEGER DEFAULT 0,
        channel_joined INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        join_timestamp TEXT,
        points_awarded INTEGER DEFAULT 0,
        is_valid INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS pending_referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER UNIQUE,
        join_timestamp TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS usage_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        module TEXT,
        details TEXT,
        timestamp TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS found_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        code TEXT,
        mobile TEXT,
        reward TEXT,
        found_at TEXT,
        reward_submitted INTEGER DEFAULT 0
    )''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

init_db()

# ==================== DATABASE FUNCTIONS ====================
def get_user(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            'user_id': row[0],
            'username': row[1],
            'first_name': row[2],
            'balance': row[3],
            'status': row[4],
            'registered_at': row[5],
            'last_used': row[6],
            'referred_by': row[7],
            'referral_code': row[8],
            'slay_logged_in': row[9] if len(row) > 9 else 0,
            'slay_codes_found': row[10] if len(row) > 10 else 0,
            'channel_joined': row[11] if len(row) > 11 else 0
        }
    return None

def create_user(user_id, username, first_name, referred_by=None):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    ref_code = f"REF{user_id}{random.randint(1000, 9999)}"
    
    c.execute('''INSERT OR IGNORE INTO users 
        (user_id, username, first_name, balance, status, registered_at, last_used, referred_by, referral_code, slay_logged_in, slay_codes_found, channel_joined)
        VALUES (?, ?, ?, 0, 'ACTIVE', ?, ?, ?, ?, 0, 0, 0)''',
        (user_id, username, first_name, now, now, referred_by, ref_code))
    conn.commit()
    conn.close()
    
    if referred_by:
        add_pending_referral(referred_by, user_id)
    return 0

def update_user_balance(user_id, delta):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (delta, user_id))
    conn.commit()
    conn.close()

def get_user_balance(user_id):
    user = get_user(user_id)
    return user['balance'] if user else 0

def add_pending_referral(referrer_id, referred_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute('INSERT INTO pending_referrals (referrer_id, referred_id, join_timestamp) VALUES (?, ?, ?)',
                  (referrer_id, referred_id, now))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def get_referral_count(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND is_valid = 1', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_pending_referral_count(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM pending_referrals WHERE referrer_id = ?', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def log_usage(user_id, module, details=""):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('INSERT INTO usage_logs (user_id, module, details, timestamp) VALUES (?, ?, ?, ?)',
              (user_id, module, details, now))
    conn.commit()
    conn.close()

def save_found_code(user_id, code, mobile, reward=""):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('INSERT INTO found_codes (user_id, code, mobile, reward, found_at) VALUES (?, ?, ?, ?, ?)',
              (user_id, code, mobile, reward, now))
    conn.commit()
    conn.close()

def get_found_codes(user_id):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT code, mobile, reward, found_at FROM found_codes WHERE user_id = ? ORDER BY found_at DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT user_id, username, balance, status FROM users ORDER BY balance DESC')
    rows = c.fetchall()
    conn.close()
    return rows

def get_total_users():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    count = c.fetchone()[0]
    conn.close()
    return count

def get_total_coins():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT SUM(balance) FROM users')
    total = c.fetchone()[0]
    conn.close()
    return total if total else 0

def update_channel_status(user_id, status):
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('UPDATE users SET channel_joined = ? WHERE user_id = ?', (1 if status else 0, user_id))
    conn.commit()
    conn.close()

def mask_mobile(mobile):
    """Mask mobile number - show only first 2 and last 2 digits"""
    if not mobile or len(mobile) < 4:
        return mobile
    return f"{mobile[:2]}******{mobile[-2:]}"

# ==================== CHANNEL CHECK ====================
def check_channel_membership(user_id):
    try:
        member = bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        is_member = member.status in ['member', 'administrator', 'creator']
        if is_member:
            update_channel_status(user_id, True)
        return is_member
    except:
        return False

def channel_join_force_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(InlineKeyboardButton("📢 JOIN CHANNEL", url=f"https://t.me/{CHANNEL_USERNAME}"))
    kb.row(InlineKeyboardButton("✅ CHECKED JOINED", callback_data="check_channel"))
    return kb

def channel_join_message():
    return f"""
╔═══════════════════════════════════════════════╗
║           🔒 **CHANNEL REQUIRED**            ║
╚═══════════════════════════════════════════════╝

⚠️ <b>You must join our channel to use this bot!</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📢 <b>Channel:</b> @{CHANNEL_USERNAME}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Click below to join, then click <b>✅ CHECKED JOINED</b>
"""

# ==================== REFERRAL CHECK ====================
def check_and_award_referrals():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    now = datetime.now()
    
    c.execute('SELECT id, referrer_id, referred_id, join_timestamp FROM pending_referrals')
    pending = c.fetchall()
    
    for pid, referrer_id, referred_id, join_ts in pending:
        try:
            join_time = datetime.fromisoformat(join_ts)
            if (now - join_time) >= timedelta(hours=REFERRAL_STAY_HOURS):
                c.execute('SELECT status FROM users WHERE user_id = ?', (referred_id,))
                user_row = c.fetchone()
                if user_row and user_row[0] == 'ACTIVE':
                    update_user_balance(referrer_id, REFERRAL_BONUS)
                    c.execute('INSERT INTO referrals (referrer_id, referred_id, join_timestamp, points_awarded, is_valid) VALUES (?, ?, ?, ?, 1)',
                              (referrer_id, referred_id, join_ts, REFERRAL_BONUS))
                    c.execute('DELETE FROM pending_referrals WHERE id = ?', (pid,))
                    conn.commit()
                    try:
                        bot.send_message(referrer_id, f"🎉 <b>Referral Bonus!</b>\n\nYou earned <b>+{REFERRAL_BONUS} Credit</b> for referring a user!\n💰 New balance: {get_user_balance(referrer_id)}", parse_mode="HTML")
                    except:
                        pass
        except Exception as e:
            logger.error(f"Referral award error: {e}")
    
    conn.close()

def run_scheduled_tasks():
    while True:
        try:
            check_and_award_referrals()
        except Exception as e:
            logger.error(f"Scheduled task error: {e}")
        time.sleep(300)

# ==================== MENU FUNCTIONS ====================
def main_menu_text(user_id, first_name, balance, status, codes_found=0):
    return f"""
    
🎮 **VIEDIET SLAY BOT**        

👋 Welcome back, <b>{first_name}</b>!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 <b>Balance:</b> <code>{balance}</code> Credits
👤 <b>User ID:</b> <code>{user_id}</code>
📊 <b>Status:</b> {status}
🎯 <b>Codes Found:</b> <code>{codes_found}</code>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>⚡ Quick Actions:</b>
• Click <b>🎮 START SCAN</b> to find codes
• Share <b>🔗 REFERRAL</b> to earn credits

<b>💡 Want Points:</b> 
• <b>1 scan = {SCAN_COST} credit</b>
• <b>1 referral = +{REFERRAL_BONUS} credit</b>
• Auto-stop on valid code!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

def main_menu_keyboard(is_admin=False):
    kb = InlineKeyboardMarkup(row_width=2)
    
    kb.row(
        InlineKeyboardButton("🎮 START SCAN", callback_data="slay_start"),
        InlineKeyboardButton("📊 STATUS", callback_data="slay_status")
    )
    kb.row(
        InlineKeyboardButton("🔗 REFERRAL", callback_data="referral"),
        InlineKeyboardButton("📋 MY CODES", callback_data="my_codes")
    )
    kb.row(
        InlineKeyboardButton("🔄 REFRESH", callback_data="refresh")
    )
    if is_admin:
        kb.row(
            InlineKeyboardButton("👑 ADMIN PANEL", callback_data="admin_panel")
        )
    return kb

def back_button():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu"))
    return kb

def referral_menu_text(user_id, balance, referral_count, pending_count):
    link = get_referral_link(user_id)
    
    return f"""
╔═══════════════════════════════════════════════╗
║           🔗 **REFERRAL SYSTEM**             ║
╚═══════════════════════════════════════════════╝

<b>📌 How it works:</b>
┌─────────────────────────────────────────────┐
│ 1️⃣ Share your referral link with friends   │
│ 2️⃣ They join via your link                 │
│ 3️⃣ You earn <b>+{REFERRAL_BONUS} Credit</b> │
└─────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📤 Your Referral Link:</b>
<code>{link}</code>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📊 Your Stats:</b>
┌─────────────────────────────────────────────┐
│ 👥 Successful: <code>{referral_count}</code>   │
│ ⏳ Pending: <code>{pending_count}</code>       │
│ 💰 Balance: <code>{balance}</code>             │
│ 🎁 Bonus: <code>+{REFERRAL_BONUS} Credit</code>│
└─────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 <b>Tip:</b> Share with friends and earn free credits!
"""

def referral_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(InlineKeyboardButton("📤 SHARE LINK", callback_data="referral_share"))
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def my_codes_text(user_id):
    codes = get_found_codes(user_id)
    
    if not codes:
        return """
╔═══════════════════════════════════════════════╗
║           📋 **MY CODES**                    ║
╚═══════════════════════════════════════════════╝

❌ <b>No codes found yet!</b>

Start a scan to find valid codes.
Click <b>🎮 START SCAN</b> from the menu.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    text = """
╔═══════════════════════════════════════════════╗
║           📋 **MY CODES**                    ║
╚═══════════════════════════════════════════════╝

<b>🎯 Your Found Codes:</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    for code, mobile, reward, found_at in codes:
        masked_mobile = mask_mobile(mobile) if mobile else "N/A"
        reward_text = f"💰 Reward: {reward}" if reward else ""
        text += f"""
┌─────────────────────────────────────────────┐
│ ✅ <b>CODE:</b> <code>{code}</code>               │
│ 📱 Mobile: <code>{masked_mobile}</code>         │
│ {reward_text}                               │
│ 📅 Found: {found_at[:10]}                   │
└─────────────────────────────────────────────┘
"""
    
    text += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 <b>Tip:</b> Submit this code on SlayYourPlay!
"""
    return text

def my_codes_keyboard():
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

def admin_panel_text():
    total_users = get_total_users()
    total_coins = get_total_coins()
    
    return f"""
╔═══════════════════════════════════════════════╗
║           👑 **ADMIN PANEL**                 ║
╚═══════════════════════════════════════════════╝

<b>📊 Statistics:</b>
┌─────────────────────────────────────────────┐
│ 👥 Total Users: <code>{total_users}</code>    │
│ 💰 Total Coins: <code>{total_coins}</code>    │
│ 💳 Scan Cost: <code>{SCAN_COST}</code>        │
└─────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>👑 Admin Commands:</b>
┌─────────────────────────────────────────────┐
│ • <code>/addcoins AMOUNT</code>              │
│   Add to ALL users                          │
│ • <code>/addcoins USER_ID AMOUNT</code>      │
│   Add to specific user                      │
│ • <code>/removecoins AMOUNT</code>           │
│   Remove from ALL users                     │
│ • <code>/removecoins USER_ID AMOUNT</code>   │
│   Remove from specific user                 │
│ • <code>/setcost AMOUNT</code>               │
│   Set scan cost                             │
│ • <code>/broadcast</code>                    │
│   Send message to all users                 │
└─────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

def admin_panel_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        InlineKeyboardButton("📊 STATS", callback_data="admin_stats"),
        InlineKeyboardButton("👥 USERS", callback_data="admin_users")
    )
    kb.row(InlineKeyboardButton("🔙 BACK", callback_data="back_menu"))
    return kb

# ==================== SLAY ENGINE ====================
def check_workingslay():
    if not os.path.exists("workingslay.py"):
        return False
    return True

class SlayScanEngine:
    def __init__(self, bot, chat_id, user_id):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.process = None
        self.is_running = False
        self.thread = None
        self.valid_code = None
        self.update_count = 0
        self.found_code = None
        self.mobile = None
        self.codes_found = []
        self.tested_count = 0
        self.valid_count = 0
    
    def start_scan(self, mobile: str, reward_mobile: str = None) -> str:
        if self.is_running:
            return "⚠️ Scan already running!"
        
        if not mobile or len(mobile) != 10:
            return "❌ Invalid mobile number!"
        
        if not check_workingslay():
            return "❌ workingslay.py not found! Please add the file."
        
        reward = reward_mobile or mobile
        self.mobile = mobile
        self.codes_found = []
        self.tested_count = 0
        self.valid_count = 0
        
        balance = get_user_balance(self.user_id)
        if balance < SCAN_COST:
            return f"❌ Insufficient credits! Need {SCAN_COST}, have {balance}"
        
        update_user_balance(self.user_id, -SCAN_COST)
        
        cmd = [
            sys.executable, "workingslay.py",
            "--mobile", mobile,
            "--reward-mobile", reward,
            "--delay", "0.5",  # Slower delay for better validation
            "--expiry", "30",
            "--no-proxy"
        ]
        
        self.is_running = True
        self.valid_code = None
        self.found_code = None
        self.update_count = 0
        
        try:
            if os.name == 'nt':
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
        except Exception as e:
            self.is_running = False
            update_user_balance(self.user_id, SCAN_COST)
            return f"❌ Failed to start scan: {e}"
        
        self.thread = threading.Thread(target=self._monitor_output, daemon=True)
        self.thread.start()
        
        return f"✅ Scan started!\n📱 Mobile: {mask_mobile(mobile)}\n💰 Cost: {SCAN_COST} credit"
    
    def _monitor_output(self):
        """Monitor workingslay.py output in real-time"""
        last_stats_update = time.time()
        
        while self.is_running and self.process:
            try:
                line = self.process.stdout.readline()
                
                if not line:
                    if self.process.poll() is not None:
                        self.is_running = False
                        break
                    continue
                
                line = line.strip()
                if not line:
                    continue
                
                self.update_count += 1
                
                # Extract stats from line
                if "Tested:" in line or "STATS" in line:
                    # Parse stats
                    tested_match = re.search(r'Tested:\s*(\d+)', line)
                    valid_match = re.search(r'Valid:\s*(\d+)', line)
                    if tested_match:
                        self.tested_count = int(tested_match.group(1))
                    if valid_match:
                        self.valid_count = int(valid_match.group(1))
                    self._send_update(f"📊 {line}")
                    continue
                
                # Skip invalid codes to avoid spam
                if "INVALID" in line.upper():
                    continue
                
                # Send important messages
                keywords = ["VALID", "REWARD", "FOUND", "ERROR", "FINAL", "CODE", "LIVE"]
                if any(k in line.upper() for k in keywords):
                    self._send_update(f"📡 {line[:200]}")
                
                # ===== DETECT VALID CODE =====
                is_valid = False
                code = None
                reward = None
                
                # Pattern 1: "VALID CODE MILA: 123456789012" or "VALID" in line
                if "VALID" in line.upper() or "CODE MILA" in line:
                    is_valid = True
                    code_match = re.search(r'\b\d{12}\b', line)
                    if code_match:
                        code = code_match.group()
                
                # Pattern 2: "[LIVE] 123456789012 | VALID"
                if "[LIVE]" in line and "VALID" in line.upper():
                    is_valid = True
                    code_match = re.search(r'\b\d{12}\b', line)
                    if code_match:
                        code = code_match.group()
                
                # Pattern 3: "[REWARD] Valid code: 123456789012"
                if "[REWARD]" in line and "Valid code" in line:
                    is_valid = True
                    code_match = re.search(r'\b\d{12}\b', line)
                    if code_match:
                        code = code_match.group()
                
                # Extract reward amount if present
                reward_match = re.search(r'₹\s*(\d+)', line)
                if reward_match:
                    reward = f"₹{reward_match.group(1)}"
                elif "Reward" in line:
                    reward_match = re.search(r'Reward[:\s]+([^\s]+)', line)
                    if reward_match:
                        reward = reward_match.group(1)
                
                # If valid code found, handle it
                if is_valid and code:
                    logger.info(f"✅ VALID CODE FOUND: {code}")
                    
                    # Save to database
                    save_found_code(self.user_id, code, self.mobile or "Unknown", reward or "")
                    
                    # Update user stats
                    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                    c = conn.cursor()
                    c.execute('UPDATE users SET slay_codes_found = slay_codes_found + 1 WHERE user_id = ?', (self.user_id,))
                    conn.commit()
                    conn.close()
                    
                    masked_mobile = mask_mobile(self.mobile) if self.mobile else "N/A"
                    
                    # Send CLEAN code to user with masked mobile
                    code_msg = f"""
✅ <b>VALID CODE FOUND!</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 <b>Code:</b> <code>{code}</code>
📱 <b>Mobile:</b> <code>{masked_mobile}</code>
{f"💰 <b>Reward:</b> <code>{reward}</code>" if reward else ""}
🕐 <b>Found at:</b> <code>{datetime.now().strftime('%H:%M:%S')}</code>

<b>📊 Scan Stats:</b>
├─ Codes Tested: <code>{self.tested_count or self.update_count}</code>
└─ Valid Codes: <code>{self.valid_count + 1}</code>
💾 Code saved in <b>"My Codes"</b> section
"""
                    self.bot.send_message(
                        self.chat_id,
                        code_msg,
                        parse_mode="HTML"
                    )
                    
                    self.codes_found.append(code)
                    self.found_code = code
                    
                    # Stop the scan immediately
                    self.stop_scan()
                    break
                
                # Update stats every 5 seconds
                if time.time() - last_stats_update > 5:
                    last_stats_update = time.time()
                    if self.tested_count > 0:
                        self._send_update(f"📊 Codes Tested: {self.tested_count} | Valid: {self.valid_count}")
                
                # Check for "FINAL" stats - scan ending
                if "FINAL" in line.upper() and "STATS" in line.upper():
                    # Check if any code was found in this scan
                    if not self.codes_found:
                        self._send_update("❌ No valid codes found in this scan.")
                    else:
                        self._send_update(f"✅ Scan completed! Found {len(self.codes_found)} valid codes.")
                    
                    self.is_running = False
                    break
                    
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                break
        
        # Final check - scan ended
        if self.is_running:
            self.is_running = False
        
        if not self.codes_found:
            self._send_update(f"❌ Scan ended. Tested {self.tested_count or self.update_count} codes, no valid found.")
        else:
            self._send_update(f"✅ Scan completed! Found {len(self.codes_found)} valid codes.")
    
    def _send_update(self, message):
        """Send update to Telegram"""
        try:
            # Clean the message
            clean_msg = message[:500]
            
            # Send important messages only
            if "📊" in message or "VALID" in message or "REWARD" in message or "FOUND" in message:
                self.bot.send_message(
                    self.chat_id,
                    f"{clean_msg}",
                    parse_mode="Markdown" if "**" in message else "HTML"
                )
            elif "STATS" in message.upper() or "Tested:" in message:
                self.bot.send_message(
                    self.chat_id,
                    f"📊 `{clean_msg}`",
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Send update error: {e}")
    
    def stop_scan(self):
        """Stop the running scan"""
        if self.process and self.is_running:
            try:
                self.process.terminate()
                time.sleep(0.5)
                self.process.kill()
            except:
                pass
            self.is_running = False
            return "⏹ Scan stopped."
        return "⚠️ No scan running."

# ==================== GLOBAL STATES ====================
user_slay_state = {}
slay_otp_data = {}
slay_engines = {}

# ==================== START COMMAND ====================
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or "User"
    
    user = get_user(user_id)
    
    referred_by = None
    if message.text and "ref_" in message.text:
        try:
            referred_by = int(message.text.split("ref_")[1].split()[0])
        except:
            pass
    
    if not user:
        create_user(user_id, username, first_name, referred_by)
        user = get_user(user_id)
    
    # Check channel membership
    if not check_channel_membership(user_id):
        bot.send_message(
            user_id,
            channel_join_message(),
            reply_markup=channel_join_force_keyboard(),
            parse_mode="HTML"
        )
        return
    
    status = "✅ Member" if check_channel_membership(user_id) else "❌ Not Joined"
    is_admin = user_id == ADMIN_ID
    codes_found = user.get('slay_codes_found', 0) if user else 0
    
    bot.send_message(
        user_id,
        main_menu_text(user_id, first_name, get_user_balance(user_id), status, codes_found),
        reply_markup=main_menu_keyboard(is_admin),
        parse_mode="HTML"
    )

# ==================== MEMBERSHIP CHECK ====================
def check_membership(user_id):
    return check_channel_membership(user_id)

# ==================== ADMIN COMMANDS ====================
@bot.message_handler(commands=['addcoins'])
def addcoins_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized! Admin only.")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) == 2:
            amount = int(parts[1])
            if amount <= 0:
                bot.reply_to(message, "❌ Amount must be positive!")
                return
            
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('UPDATE users SET balance = balance + ?', (amount,))
            affected = c.rowcount
            conn.commit()
            conn.close()
            
            bot.reply_to(
                message,
                f"✅ <b>Added {amount} Credits to ALL Users!</b>\n\n"
                f"👥 Affected: <code>{affected}</code> users",
                parse_mode="HTML"
            )
            return
        
        elif len(parts) == 3:
            target_id = int(parts[1])
            amount = int(parts[2])
            
            if amount <= 0:
                bot.reply_to(message, "❌ Amount must be positive!")
                return
            
            user = get_user(target_id)
            if not user:
                bot.reply_to(message, f"❌ User {target_id} not found!")
                return
            
            update_user_balance(target_id, amount)
            new_balance = get_user_balance(target_id)
            
            bot.reply_to(
                message,
                f"✅ <b>Added {amount} Credits</b>\n\n"
                f"👤 User: {user['first_name']} (ID: {target_id})\n"
                f"💰 New Balance: <code>{new_balance}</code>",
                parse_mode="HTML"
            )
            
            try:
                bot.send_message(
                    target_id,
                    f"🎉 <b>Admin Added Credits!</b>\n\n"
                    f"➕ <code>+{amount} Credits</code> added.\n"
                    f"💰 New Balance: <code>{new_balance}</code>",
                    parse_mode="HTML"
                )
            except:
                pass
            return
        
        else:
            bot.reply_to(
                message,
                "❌ <b>Invalid format!</b>\n\n"
                "1️⃣ Add to ALL: <code>/addcoins AMOUNT</code>\n"
                "2️⃣ Add to specific: <code>/addcoins USER_ID AMOUNT</code>",
                parse_mode="HTML"
            )
            
    except ValueError:
        bot.reply_to(message, "❌ Invalid number format!")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(commands=['removecoins'])
def removecoins_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized! Admin only.")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) == 2:
            amount = int(parts[1])
            if amount <= 0:
                bot.reply_to(message, "❌ Amount must be positive!")
                return
            
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('UPDATE users SET balance = balance - ? WHERE balance >= ?', (amount, amount))
            affected = c.rowcount
            conn.commit()
            conn.close()
            
            bot.reply_to(
                message,
                f"✅ <b>Removed {amount} Credits from ALL Users!</b>\n\n"
                f"👥 Affected: <code>{affected}</code> users",
                parse_mode="HTML"
            )
            return
        
        elif len(parts) == 3:
            target_id = int(parts[1])
            amount = int(parts[2])
            
            if amount <= 0:
                bot.reply_to(message, "❌ Amount must be positive!")
                return
            
            user = get_user(target_id)
            if not user:
                bot.reply_to(message, f"❌ User {target_id} not found!")
                return
            
            current_balance = user['balance']
            if current_balance < amount:
                bot.reply_to(
                    message,
                    f"❌ User has insufficient balance!\nCurrent: <code>{current_balance}</code>",
                    parse_mode="HTML"
                )
                return
            
            update_user_balance(target_id, -amount)
            new_balance = get_user_balance(target_id)
            
            bot.reply_to(
                message,
                f"✅ <b>Removed {amount} Credits</b>\n\n"
                f"👤 User: {user['first_name']} (ID: {target_id})\n"
                f"💰 New Balance: <code>{new_balance}</code>",
                parse_mode="HTML"
            )
            
            try:
                bot.send_message(
                    target_id,
                    f"⚠️ <b>Admin Removed Credits</b>\n\n"
                    f"➖ <code>-{amount} Credits</code> removed.\n"
                    f"💰 New Balance: <code>{new_balance}</code>",
                    parse_mode="HTML"
                )
            except:
                pass
            return
        
        else:
            bot.reply_to(
                message,
                "❌ <b>Invalid format!</b>\n\n"
                "1️⃣ Remove from ALL: <code>/removecoins AMOUNT</code>\n"
                "2️⃣ Remove from specific: <code>/removecoins USER_ID AMOUNT</code>",
                parse_mode="HTML"
            )
            
    except ValueError:
        bot.reply_to(message, "❌ Invalid number format!")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(commands=['setcost'])
def setcost_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            bot.reply_to(
                message,
                "❌ Invalid format!\nUse: <code>/setcost AMOUNT</code>\nExample: <code>/setcost 2</code>",
                parse_mode="HTML"
            )
            return
        
        global SCAN_COST
        amount = int(parts[1])
        
        if amount < 0:
            bot.reply_to(message, "❌ Amount must be non-negative!")
            return
        
        SCAN_COST = amount
        
        bot.reply_to(
            message,
            f"✅ <b>Scan Cost Updated!</b>\n\n"
            f"💰 New Cost: <code>{amount}</code> credits per scan",
            parse_mode="HTML"
        )
        
    except ValueError:
        bot.reply_to(message, "❌ Amount must be a number!")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)[:100]}")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        bot.reply_to(message, "❌ Unauthorized!")
        return
    
    msg = bot.reply_to(
        message,
        "📢 <b>Broadcast Message</b>\n\n"
        "Send the message you want to broadcast to all users.\n\n"
        "⚠️ <b>Warning:</b> This will send to ALL users!\n\n"
        "Send /cancel to abort.",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, broadcast_handler)

def broadcast_handler(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    
    if message.text.lower() == '/cancel':
        bot.reply_to(message, "❌ Broadcast cancelled.")
        return
    
    users = get_all_users()
    if not users:
        bot.reply_to(message, "❌ No users to broadcast to!")
        return
    
    success = 0
    failed = 0
    
    status_msg = bot.reply_to(message, f"📢 Broadcasting to {len(users)} users...")
    
    for uid, username, balance, status in users:
        try:
            bot.send_message(uid, f"📢 <b>Announcement</b>\n\n{message.text}", parse_mode="HTML")
            success += 1
            time.sleep(0.05)
        except:
            failed += 1
    
    bot.edit_message_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"✅ Sent: <code>{success}</code>\n"
        f"❌ Failed: <code>{failed}</code>",
        chat_id=message.chat.id,
        message_id=status_msg.message_id,
        parse_mode="HTML"
    )

# ==================== CALLBACK HANDLER ====================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data
    
    # ===== CHANNEL CHECK =====
    if data == "check_channel":
        if check_channel_membership(user_id):
            user = get_user(user_id)
            status = "✅" if check_channel_membership(user_id) else "❌"
            is_admin = user_id == ADMIN_ID
            codes_found = user.get('slay_codes_found', 0) if user else 0
            
            bot.edit_message_text(
                main_menu_text(user_id, user['first_name'], get_user_balance(user_id), status, codes_found),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_keyboard(is_admin),
                parse_mode="HTML"
            )
            bot.answer_callback_query(call.id, "✅ Channel joined! Welcome!")
        else:
            bot.answer_callback_query(call.id, "❌ Please join the channel first!", show_alert=True)
        return
    
    # ===== BACK =====
    if data == "back_menu":
        user = get_user(user_id)
        if not user:
            bot.answer_callback_query(call.id, "User not found")
            return
        status = "✅" if check_channel_membership(user_id) else "❌"
        is_admin = user_id == ADMIN_ID
        codes_found = user.get('slay_codes_found', 0)
        
        try:
            bot.edit_message_text(
                main_menu_text(user_id, user['first_name'], user['balance'], status, codes_found),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_keyboard(is_admin),
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return
    
    # ===== REFRESH =====
    if data == "refresh":
        user = get_user(user_id)
        if not user:
            bot.answer_callback_query(call.id, "User not found")
            return
        status = "✅" if check_channel_membership(user_id) else "❌"
        is_admin = user_id == ADMIN_ID
        codes_found = user.get('slay_codes_found', 0)
        
        try:
            bot.edit_message_text(
                main_menu_text(user_id, user['first_name'], user['balance'], status, codes_found),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=main_menu_keyboard(is_admin),
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return
    
    # ===== MY CODES =====
    if data == "my_codes":
        try:
            bot.edit_message_text(
                my_codes_text(user_id),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=my_codes_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return
    
    # ===== SLAY STATUS =====
    if data == "slay_status":
        user = get_user(user_id)
        codes_found = user.get('slay_codes_found', 0)
        bot.answer_callback_query(
            call.id,
            f"🎮 **Slay Stats**\n\n"
            f"💰 Balance: {user['balance']}\n"
            f"🎯 Codes Found: {codes_found}\n"
            f"💳 Scan Cost: {SCAN_COST} credit",
            show_alert=True
        )
        return
    
    # ===== SLAY START =====
    if data == "slay_start":
        if not check_channel_membership(user_id):
            bot.answer_callback_query(call.id, "❌ Please join channel first!", show_alert=True)
            return
        
        user = get_user(user_id)
        if user['balance'] < SCAN_COST:
            bot.answer_callback_query(call.id, f"❌ Need {SCAN_COST} credits! Balance: {user['balance']}", show_alert=True)
            return
        
        user_slay_state[user_id] = "waiting_phone"
        kb = InlineKeyboardMarkup()
        kb.row(InlineKeyboardButton("❌ Cancel", callback_data="slay_abort"))
        kb.row(InlineKeyboardButton("🔙 Back", callback_data="back_menu"))
        
        try:
            bot.edit_message_text(
                "📱 Enter your 10-digit mobile number:\n\nSend /cancel to abort.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=kb
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return
    
    if data == "slay_abort":
        user_slay_state[user_id] = None
        if user_id in slay_otp_data:
            del slay_otp_data[user_id]
        try:
            bot.edit_message_text(
                "❌ Operation cancelled.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_button()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return
    
    # ===== REFERRAL =====
    if data == "referral":
        user = get_user(user_id)
        text = referral_menu_text(user_id, user['balance'], get_referral_count(user_id), get_pending_referral_count(user_id))
        try:
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=referral_menu_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return
    
    if data == "referral_share":
        link = get_referral_link(user_id)
        bot.answer_callback_query(
            call.id,
            "📤 Copy this link and share with friends!\n\n" + link,
            show_alert=True
        )
        return
    
    # ===== ADMIN PANEL =====
    if data == "admin_panel":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        try:
            bot.edit_message_text(
                admin_panel_text(),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=admin_panel_keyboard(),
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return
    
    if data == "admin_stats":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        total_users = get_total_users()
        total_coins = get_total_coins()
        bot.answer_callback_query(
            call.id,
            f"📊 Stats:\n👥 Users: {total_users}\n💰 Coins: {total_coins}",
            show_alert=True
        )
        return
    
    if data == "admin_users":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Unauthorized!")
            return
        users = get_all_users()
        if not users:
            bot.answer_callback_query(call.id, "No users found", show_alert=True)
            return
        
        user_list = "👥 <b>Top Users:</b>\n\n"
        for i, (uid, username, balance, status) in enumerate(users[:10], 1):
            name = username or f"User_{uid}"
            user_list += f"{i}. {name} - 💰 {balance}\n"
        
        try:
            bot.edit_message_text(
                user_list,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="HTML"
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                logger.error(f"Edit error: {e}")
        bot.answer_callback_query(call.id)
        return

# ==================== SLAY MESSAGE HANDLERS ====================
@bot.message_handler(func=lambda message: user_slay_state.get(message.from_user.id) == "waiting_phone")
def slay_phone_handler(message):
    user_id = message.from_user.id
    phone = message.text.strip()
    
    if phone.lower() in ['/cancel', 'cancel']:
        user_slay_state[user_id] = None
        bot.reply_to(message, "❌ Scan cancelled.", reply_markup=back_button())
        return
    
    if not phone.isdigit() or len(phone) != 10:
        bot.reply_to(message, "❌ Please enter exactly 10 digits.\n\nSend /cancel to abort.")
        return
    
    balance = get_user_balance(user_id)
    if balance < SCAN_COST:
        bot.reply_to(message, f"❌ Insufficient credits! Need {SCAN_COST} credits. Balance: {balance}")
        return
    
    user_slay_state[user_id] = "waiting_otp"
    
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("❌ Cancel", callback_data="slay_abort"))
    kb.row(InlineKeyboardButton("🔙 Back", callback_data="back_menu"))
    
    status_msg = bot.reply_to(message, f"📱 Sending OTP to +91{phone}...", reply_markup=kb)
    
    update_user_balance(user_id, -SCAN_COST)
    slay_otp_data[user_id] = {"phone": phone, "cost": SCAN_COST}
    
    def send_otp_thread():
        try:
            if not check_workingslay():
                bot.edit_message_text(
                    "❌ workingslay.py not found!",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
                user_slay_state[user_id] = None
                update_user_balance(user_id, SCAN_COST)
                return
            
            from workingslay import send_otp, make_session, generate_master_key, init_session
            
            master_key = generate_master_key()
            session = make_session(master_key)
            
            user_key, data_key = init_session(session, master_key)
            if not user_key:
                bot.edit_message_text(
                    "❌ Failed to initialize session.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
                user_slay_state[user_id] = None
                update_user_balance(user_id, SCAN_COST)
                return
            
            success = send_otp(session, user_key, data_key, phone)
            
            if success:
                bot.edit_message_text(
                    f"✅ OTP sent to +91{phone}!\n\nEnter the 6-digit OTP code:",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    reply_markup=kb
                )
                slay_otp_data[user_id]["session"] = session
                slay_otp_data[user_id]["user_key"] = user_key
                slay_otp_data[user_id]["data_key"] = data_key
            else:
                bot.edit_message_text(
                    f"❌ Failed to send OTP. Please try again.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
                user_slay_state[user_id] = None
                update_user_balance(user_id, SCAN_COST)
                
        except ImportError as e:
            update_user_balance(user_id, SCAN_COST)
            bot.edit_message_text(
                f"❌ Error: workingslay.py not found.",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            user_slay_state[user_id] = None
        except Exception as e:
            update_user_balance(user_id, SCAN_COST)
            bot.edit_message_text(
                f"❌ Error: {str(e)[:200]}",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            user_slay_state[user_id] = None
    
    threading.Thread(target=send_otp_thread).start()

@bot.message_handler(func=lambda message: user_slay_state.get(message.from_user.id) == "waiting_otp")
def slay_otp_handler(message):
    user_id = message.from_user.id
    otp = message.text.strip()
    
    if otp.lower() in ['/cancel', 'cancel']:
        user_slay_state[user_id] = None
        if user_id in slay_otp_data:
            update_user_balance(user_id, slay_otp_data[user_id]["cost"])
            del slay_otp_data[user_id]
        bot.reply_to(message, "❌ Scan cancelled.", reply_markup=back_button())
        return
    
    if not otp.isdigit() or len(otp) != 6:
        bot.reply_to(message, "❌ Please enter a valid 6-digit OTP.")
        return
    
    if user_id not in slay_otp_data:
        bot.reply_to(message, "❌ Session expired. Please start again.")
        user_slay_state[user_id] = None
        return
    
    data = slay_otp_data[user_id]
    phone = data["phone"]
    cost = data["cost"]
    session = data.get("session")
    user_key = data.get("user_key")
    data_key = data.get("data_key")
    
    status_msg = bot.reply_to(message, "🔄 Verifying OTP...")
    
    def verify_thread():
        try:
            if not check_workingslay():
                bot.edit_message_text(
                    "❌ workingslay.py not found!",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
                user_slay_state[user_id] = None
                return
            
            from workingslay import verify_otp, select_pack, select_vibe, save_global_session
            
            access_token = verify_otp(session, user_key, data_key, otp)
            
            if access_token:
                select_pack(session, user_key, data_key, access_token)
                select_vibe(session, user_key, data_key, access_token)
                save_global_session()
                
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                c = conn.cursor()
                c.execute('UPDATE users SET slay_logged_in = 1 WHERE user_id = ?', (user_id,))
                conn.commit()
                conn.close()
                
                bot.edit_message_text(
                    f"✅ <b>Login Successful!</b>\n\n"
                    f"📱 Mobile: {mask_mobile(phone)}\n"
                    f"💰 Balance: <code>{get_user_balance(user_id)}</code>\n\n"
                    f"🎮 Starting scan now...",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="HTML"
                )
                
                user_slay_state[user_id] = None
                del slay_otp_data[user_id]
                
                start_slay_scan(message, user_id, phone)
                
            else:
                update_user_balance(user_id, cost)
                bot.edit_message_text(
                    f"❌ OTP verification failed.\n\nPlease try again.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
                user_slay_state[user_id] = None
                if user_id in slay_otp_data:
                    del slay_otp_data[user_id]
                
        except Exception as e:
            update_user_balance(user_id, cost)
            bot.edit_message_text(
                f"❌ Error: {str(e)[:200]}",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            user_slay_state[user_id] = None
            if user_id in slay_otp_data:
                del slay_otp_data[user_id]
    
    threading.Thread(target=verify_thread).start()

def start_slay_scan(message, user_id, phone):
    scan_msg = bot.reply_to(
        message,
        f"🔍 **SLAY SCAN STARTED**\n\n"
        f"📱 Mobile: `{mask_mobile(phone)}`\n"
        f"⏳ Scanning for valid codes...\n"
        f"🔄 Auto-stop on valid code\n\n"
        f"_This may take several minutes._",
        parse_mode="Markdown"
    )
    
    scan_engine = SlayScanEngine(bot, message.chat.id, user_id)
    slay_engines[user_id] = scan_engine
    
    def scan_thread():
        result = scan_engine.start_scan(phone, phone)
        if "✅" in result:
            bot.edit_message_text(
                f"✅ **Scan Started!**\n\n"
                f"📱 Mobile: `{mask_mobile(phone)}`\n"
                f"🔍 Scanning in progress...\n\n"
                f"_Live updates will appear here._",
                chat_id=message.chat.id,
                message_id=scan_msg.message_id,
                parse_mode="Markdown"
            )
        else:
            bot.edit_message_text(
                f"❌ **Scan Failed**\n\n{result}",
                chat_id=message.chat.id,
                message_id=scan_msg.message_id,
                parse_mode="Markdown"
            )
    
    threading.Thread(target=scan_thread).start()

# ==================== HELPER FUNCTIONS ====================
def get_referral_link(user_id):
    bot_username = bot.get_me().username
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

# ==================== MAIN ====================
if __name__ == "__main__":
    if not check_workingslay():
        logger.warning("⚠️ workingslay.py NOT FOUND! Scan feature will not work.")
        logger.warning("📁 Please add workingslay.py to the same directory.")
    
    task_thread = threading.Thread(target=run_scheduled_tasks, daemon=True)
    task_thread.start()
    
    logger.info("=" * 50)
    logger.info("🎮 SLAY BOT v3.0 STARTED")
    logger.info("=" * 50)
    logger.info("💰 New Users: 0 credits")
    logger.info("🔗 Referral: +1 credit")
    logger.info("🔍 Scan Cost: 1 credit")
    logger.info("📢 Channel: @{}".format(CHANNEL_USERNAME))
    logger.info("👑 Admin: /addcoins, /removecoins, /setcost, /broadcast")
    logger.info(f"📁 workingslay.py: {'✅ Found' if check_workingslay() else '❌ MISSING'}")
    logger.info("📱 Mobile numbers: Masked for privacy")
    logger.info("=" * 50)
    
    try:
        bot.remove_webhook()
        time.sleep(1)
    except:
        pass
    
    while True:
        try:
            logger.info("🔄 Starting polling...")
            bot.polling(non_stop=False, interval=1, timeout=30)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            logger.info("🔄 Restarting polling in 10 seconds...")
            time.sleep(10)
