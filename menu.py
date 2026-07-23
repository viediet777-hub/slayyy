# menu.py - Complete Menu Functions for Viediet Bot
# FIXED: Supercoin Fetcher replaced with Slay Your Play

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ==================== COLORED BUTTON HELPER ====================
def colored_button(text, callback_data, style="default", emoji_id=None):
    button = InlineKeyboardButton(text, callback_data=callback_data)
    if style != "default":
        button.style = style
    if emoji_id:
        button.icon_custom_emoji_id = emoji_id
    return button

# ==================== MAIN MENU ====================
def main_menu_text(user_id, first_name, balance, status):
    return f"""
╔══════════════════════════════════════╗
║     🚀 VIEDIET UTILITY BOT          ║
╚══════════════════════════════════════╝

👋 Welcome back, <b>{first_name}</b>!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 <b>Balance:</b> <code>{balance}</code> Credits
👤 <b>User ID:</b> <code>{user_id}</code>
📊 <b>Status:</b> {status}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>⚡ Quick Actions:</b>
• Click modules below to access features
• Check <b>📊 Stats</b> for your usage
• Share <b>🔗 Referral Link</b> to earn credits

<b>💡 Pro Tip:</b> Each module costs credits.
Earn free credits by referring friends!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

def main_menu_keyboard(is_admin=False):
    kb = InlineKeyboardMarkup(row_width=2)

    # Row 1 - Firebase & Temp Mail
    kb.row(
        colored_button("🔥 Firebase Extractor", "module_firebase", "primary"),
        colored_button("📧 Temp Mail", "module_temp", "success")
    )
    
    # Row 2 - Flipkart & Instagram
    kb.row(
        colored_button("🛒 Flipkart Checker", "module_flipkart", "danger"),
        colored_button("📸 Instagram Downloader", "module_instagram", "primary")
    )
    
    # Row 3 - IG Viewer & Music
    kb.row(
        colored_button("👁️ IG Viewer", "module_igviewer", "success"),
        colored_button("🎵 Music Downloader", "module_music", "danger")
    )
    
    # Row 4 - Shopsy & Yoga
    kb.row(
        colored_button("🛍️ Shopsy Mining", "module_shopsy", "primary"),
        colored_button("🧘 Yoga Referral", "module_yoga", "success")
    )
    
    # Row 5 - Referral System & Slay Your Play (REPLACED Supercoin)
    kb.row(
        colored_button("🔗 Referral System", "module_referral", "danger"),
        colored_button("🎮 Slay Your Play", "module_slay", "primary")
    )

    # Row 6 - Admin (if admin)
    if is_admin:
        kb.row(colored_button("👑 Admin Panel", "module_admin", "primary"))

    return kb

# ==================== BACK BUTTON HELPER ====================
def back_button():
    return [colored_button("🔙 Back to Menu", "back_menu", "default")]

# ==================== SLAY YOUR PLAY MENU ====================
def slay_menu_text(user_id, balance, status, cost, has_session=False, codes_found=0):
    session_status = "✅ Active" if has_session else "❌ Not Active"
    return f"""
╔══════════════════════════════════════╗
║     🎮 SLAY YOUR PLAY              ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Slay Your Play Code Tester
<b>💰 Cost:</b> <code>{cost}</code> Credits per scan
<b>💳 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Tests random 12-digit codes on SlayYourPlay
• 🔍 Automated code testing
• 🎯 Finds valid promo codes
• 💰 Auto-submit reward
• 🛑 Auto-stop on valid code

<b>📊 Status:</b> {session_status}
<b>🎯 Codes Found:</b> <code>{codes_found}</code>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📤 How to use:</b>
1. Click <b>🎮 Start Scan</b>
2. Enter 10-digit mobile number
3. Enter OTP received
4. Auto-scan starts
5. Stops when valid code found!

<b>⚡ Features:</b>
• 1 credit = 1 scan
• Auto-stop on valid code
• Proxy support
• Real-time updates

<b>💡 Tip:</b> Make sure you have proxy file!
"""

def slay_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("🎮 Start Scan", "slay_start", "success"),
        colored_button("📊 Status", "slay_status", "primary")
    )
    kb.row(
        colored_button("🔄 Refresh Session", "slay_refresh", "primary"),
        colored_button("🚪 Logout", "slay_logout", "danger")
    )
    kb.row(*back_button())
    return kb

# ==================== FIREBASE MENU ====================
def firebase_menu_text(user_id, balance, status, cost):
    return f"""
╔══════════════════════════════════════╗
║     🔥 FIREBASE EXTRACTOR           ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Firebase Extractor
<b>💰 Cost:</b> <code>{cost}</code> Credits
<b>💳 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Extracts sensitive data from APK files:
• 🔥 Firebase URLs & Database endpoints
• 🔑 API Keys (Google, Firebase, etc.)
• 🔐 Secrets & Tokens
• 📦 Storage Buckets
• 📄 JSON Endpoints

<b>📤 How to use:</b>
1. Click <b>📤 Send APK</b>
2. Upload your APK file
3. Wait for analysis (30-60 sec)
4. Get extracted credentials

<b>⚠️ Warning:</b>
Only use on APKs you own!
"""

def firebase_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("📤 Send APK", "firebase_send", "primary"),
        colored_button("🗑️ Remove APK", "firebase_remove", "danger")
    )
    kb.row(*back_button())
    return kb

# ==================== TEMP MAIL MENU ====================
def temp_menu_text(user_id):
    return f"""
╔══════════════════════════════════════╗
║      📧 TEMPORARY EMAIL             ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Temp Mail Generator
<b>💰 Cost:</b> <code>FREE</code>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Creates disposable email addresses
• 📧 Receive emails instantly
• 🔑 Auto-detect OTP codes
• ⏱️ 10 minutes validity

<b>📤 How to use:</b>
1. Click <b>📧 New Email</b>
2. Copy your temp email
3. Use it for signups
4. Click <b>🔑 Get OTP</b> to auto-detect

<b>💡 Pro Tip:</b>
Perfect for OTP verification without sharing real email!
"""

def temp_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("📧 New Email", "temp_new", "success"),
        colored_button("📥 Check Inbox", "temp_inbox", "primary")
    )
    kb.row(
        colored_button("🔑 Get OTP", "temp_otp", "danger"),
        colored_button("🗑️ Delete Email", "temp_delete", "danger")
    )
    kb.row(*back_button())
    return kb

# ==================== FLIPKART MENU ====================
def flipkart_menu_text(user_id, balance, status, cost):
    return f"""
╔══════════════════════════════════════╗
║     🛒 FLIPKART CHECKER             ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Flipkart Number Checker
<b>💰 Cost:</b> <code>{cost}</code> Credits
<b>💳 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Check if a phone number is registered on Flipkart
• 📱 Enter 10-digit number
• 🔍 Get registration status
• ⚡ Instant results

<b>📤 How to use:</b>
1. Click <b>📱 Check Number</b>
2. Enter 10-digit phone number
3. Get registration status

<b>📊 Status Results:</b>
✅ <b>VERIFIED</b> - Registered user
❌ <b>GUEST</b> - Not registered
⚠️ <b>API Blocked</b> - Try again later

<b>💡 Tip:</b> Use for lead generation!
"""

def flipkart_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(colored_button("📱 Check Number", "flipkart_check", "primary"))
    kb.row(*back_button())
    return kb

# ==================== INSTAGRAM MENU ====================
def instagram_menu_text(user_id, balance, status, cost):
    return f"""
╔══════════════════════════════════════╗
║     📸 INSTAGRAM DOWNLOADER         ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Instagram Reel Downloader
<b>💰 Cost:</b> <code>{cost}</code> Credits per video
<b>💳 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Download Instagram Reels & Videos
• 📹 Single reel download
• 📚 Bulk download (multiple reels)
• 🎬 High quality MP4

<b>📤 How to use:</b>
1. Click <b>📹 Single</b> or <b>📚 Bulk</b>
2. Send Instagram reel URL(s)
3. Get video(s) instantly

<b>📝 Examples:</b>
• Single: https://www.instagram.com/reel/xyz123/
• Bulk: one URL per line
"""

def instagram_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("📹 Single Download", "instagram_single", "primary"),
        colored_button("📚 Bulk Download", "instagram_bulk", "success")
    )
    kb.row(*back_button())
    return kb

# ==================== IG VIEWER MENU ====================
def igviewer_menu_text(user_id, balance, status, cost):
    return f"""
╔══════════════════════════════════════╗
║      👁️ IG VIEWER                  ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Instagram Profile Viewer
<b>💰 Cost:</b> <code>{cost}</code> Credits
<b>💳 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
View Instagram profiles anonymously
• 👤 Profile information
• 📸 Post previews
• 📊 Engagement stats
• 🔍 Story viewer

<b>📤 How to use:</b>
1. Click <b>👤 View Profile</b>
2. Enter username
3. Get full profile data

<b>⚠️ Note:</b>
Works for public profiles only!
"""

def igviewer_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(colored_button("👤 View Profile", "igviewer_view", "primary"))
    kb.row(*back_button())
    return kb

# ==================== MUSIC MENU ====================
def music_menu_text(user_id):
    return f"""
╔══════════════════════════════════════╗
║      🎵 MUSIC DOWNLOADER            ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Music Downloader
<b>💰 Cost:</b> <code>FREE</code>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Download high-quality MP3 songs
• 🎵 320kbps quality
• 🎤 Artist & album info
• 📥 Direct download

<b>📤 How to use:</b>
1. Send song or artist name
2. Select from search results
3. Download MP3 instantly

<b>🎶 Supported:</b>
• Hindi songs
• English songs
• Regional songs
• All genres

<b>💡 Tip:</b> Unlimited free downloads!
"""

def music_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(colored_button("🎵 Search Song", "music_search", "success"))
    kb.row(*back_button())
    return kb

# ==================== SHOPSY MENU ====================
def shopsy_menu_text(user_id, balance, status, shopsy_balance, is_logged_in):
    login_status = "✅ Logged In" if is_logged_in else "❌ Not Logged In"
    return f"""
╔══════════════════════════════════════╗
║      🛍️ SHOPSY MINING              ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Shopsy Auto-Mining
<b>💰 Cost:</b> <code>{get_module_cost('shopsy')}</code> Credits
<b>💳 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Automatically play Shopsy games and earn coins
• 🎮 Auto-play games
• 🪙 Earn coins
• ⭐ Convert to points
• 📊 Track earnings

<b>📤 How to use:</b>
1. Click <b>🚀 Start Mining</b>
2. Enter 10-digit phone number
3. Enter OTP received
4. Auto-mine starts!

<b>📊 Your Stats:</b>
🪙 Shopsy Points: <code>{shopsy_balance}</code>
🔐 Status: {login_status}
⏱️ Mining: 1-2 minutes

<b>💡 Tip:</b> Higher coins = more points!
"""

def shopsy_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("🚀 Start Mining", "shopsy_start", "success"),
        colored_button("📊 Stats", "shopsy_stats", "primary")
    )
    kb.row(
        colored_button("🚪 Logout", "shopsy_logout", "danger")
    )
    kb.row(*back_button())
    return kb

# ==================== YOGA MENU ====================
def yoga_menu_text(user_id, balance, status, yoga_code, reward, cost):
    code_display = f"`{yoga_code}`" if yoga_code else "❌ Not Set"
    return f"""
╔══════════════════════════════════════╗
║      🧘 YOGA REFERRAL              ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Yoga Referral Bot
<b>💰 Cost:</b> <code>{cost}</code> Credits
<b>🎁 Reward:</b> <code>+{reward}</code> Credits
<b>💳 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 What it does:</b>
Auto-register users on Habit.Yoga
• 📱 Phone number registration
• 🔐 OTP auto-verify
• 🎯 Earn referral rewards
• 📊 Track your referrals

<b>📤 How to use:</b>
1. Click <b>🧘 Start Referral</b>
2. Enter 10-digit phone number
3. Enter OTP received
4. Earn <b>+{reward} Credits</b>!

<b>📊 Your Yoga Code:</b>
{code_display}

<b>💡 Tip:</b> Set your code first!
Send link or code to setup.
"""

def yoga_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("🧘 Start Referral", "yoga_start", "success"),
        colored_button("📊 Stats", "yoga_stats", "primary")
    )
    kb.row(
        colored_button("🔑 Set Code", "yoga_setcode", "danger")
    )
    kb.row(*back_button())
    return kb

# ==================== REFERRAL MENU ====================
def referral_menu_text(user_id, balance, referral_count):
    return f"""
╔══════════════════════════════════════╗
║      🔗 REFERRAL SYSTEM             ║
╚══════════════════════════════════════╝

<b>📋 Module:</b> Referral Program
<b>💰 Balance:</b> <code>{balance}</code> Credits

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📌 How it works:</b>
• Share your referral link
• Friends join via your link
• They stay 24 hours
• You earn <b>+{3} Credits</b>!

<b>📊 Your Stats:</b>
👥 <b>Referrals:</b> <code>{referral_count}</code>
⏳ <b>Pending:</b> <code>{get_pending_referral_count(user_id)}</code>
💰 <b>Bonus per referral:</b> <code>+3 Credits</code>

<b>🎁 Friend gets:</b> <b>+5 Credits</b> on joining!

<b>📤 How to use:</b>
1. Click <b>🔗 Get Link</b>
2. Share with friends
3. They join and stay
4. You earn credits!
"""

def referral_menu_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("🔗 Get Link", "referral_get_link", "success"),
        colored_button("📊 Stats", "referral_stats", "primary")
    )
    kb.row(*back_button())
    return kb

# ==================== ADMIN MENU ====================
def admin_panel_text():
    return f"""
╔══════════════════════════════════════╗
║      👑 ADMIN PANEL                ║
╚══════════════════════════════════════╝

<b>📋 Admin Controls</b>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📊 Statistics:</b>
• Total Users
• Total Coins
• Usage Analytics

<b>👥 User Management:</b>
• View all users
• Add/Remove coins
• Ban/Unban users

<b>📢 Broadcasting:</b>
• Send messages to all users

<b>⚙️ Configuration:</b>
• Module costs
• Referral rewards
• System settings

<b>⚠️ Warning:</b>
Admin actions are irreversible!
Use with caution.
"""

def admin_panel_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.row(
        colored_button("📊 Stats", "admin_stats", "primary"),
        colored_button("👥 Users", "admin_users", "primary")
    )
    kb.row(
        colored_button("➕ Add Coins", "admin_add_coins", "success"),
        colored_button("➖ Remove Coins", "admin_remove_coins", "danger")
    )
    kb.row(
        colored_button("📢 Broadcast", "admin_broadcast", "primary"),
        colored_button("⚙️ Costs", "admin_costs", "primary")
    )
    kb.row(*back_button())
    return kb

# ==================== HELP MENU ====================
def help_menu_text():
    return """
╔══════════════════════════════════════╗
║       💡 HELP & INFO               ║
╚══════════════════════════════════════╝

<b>🤖 Bot Commands:</b>

<b>/start</b> - Show main menu
<b>/cancel</b> - Cancel current operation
<b>/addcoins</b> - Admin only
<b>/removecoins</b> - Admin only
<b>/broadcast</b> - Admin only
<b>/setcost</b> - Admin only

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>💰 Earning Credits:</b>
• 🎁 <b>+5</b> Welcome bonus
• 🔗 <b>+3</b> Per referral
• 🧘 <b>+4</b> Per Yoga referral
• 🛍️ Shopsy mining rewards

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📱 Modules:</b>
• Firebase Extractor
• Temp Mail
• Flipkart Checker
• Instagram Downloader
• IG Viewer
• Music Downloader
• Shopsy Mining
• Yoga Referral
• Slay Your Play
• Referral System

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ==================== Helper Functions ====================
def get_pending_referral_count(user_id):
    import sqlite3
    conn = sqlite3.connect("viediet_bot.db", check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM pending_referrals WHERE referrer_id = ?', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_module_cost(module):
    import sqlite3
    conn = sqlite3.connect("viediet_bot.db", check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT value FROM config WHERE key = ?', (f"{module}_cost",))
    row = c.fetchone()
    conn.close()
    if row:
        return int(row[0])
    
    costs = {
        "firebase": 1,
        "flipkart": 1,
        "instagram_single": 1,
        "instagram_bulk": 1,
        "shopsy": 1,
        "yoga": 1,
        "igviewer": 1,
        "slay": 1
    }
    return costs.get(module, 1)
