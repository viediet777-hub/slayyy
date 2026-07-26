import asyncio
import logging
import random
import string
import time
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from config import BOT_TOKEN, ADMIN_ID, DEFAULT_TIME, REFERRAL_BONUS, WEB_PANEL_URL, SUPPORT_CONTACT, GIFT_CARDS
from firebase_manager import FirebaseManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

firebase = FirebaseManager()
user_timers = {}
user_current_numbers = {}


def generate_referral_code() -> str:
    return "REF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def format_time(seconds: int) -> str:
    if seconds <= 0:
        return "00:00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📲 GET NUMBER", callback_data="get_number")],
        [InlineKeyboardButton("🌐 WEB PANEL", callback_data="web_panel")],
        [InlineKeyboardButton("✉️ SEND SMS", callback_data="send_sms")],
        [InlineKeyboardButton("📊 STATUS", callback_data="status")],
        [InlineKeyboardButton("👥 REFER & EARN", callback_data="refer")],
        [InlineKeyboardButton("⏱️ TIME REMAINING", callback_data="time_left")],
        [InlineKeyboardButton("🎁 GIFT CARD", callback_data="gift_card")],
        [InlineKeyboardButton("🔍 SEARCH NUMBER", callback_data="search_number")],
        [InlineKeyboardButton("⚙️ SETTINGS", callback_data="settings")],
        [InlineKeyboardButton("📞 SUPPORT", callback_data="support")],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("🛡️ ADMIN PANEL", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    user_data = await firebase.get_user(user.id)
    if user_data is None:
        referral_code = generate_referral_code()
        now = datetime.utcnow().isoformat() + "Z"
        new_user = {
            "timeLeft": DEFAULT_TIME,
            "referrals": 0,
            "referredBy": None,
            "currentNumber": None,
            "history": [],
            "smsSent": 0,
            "joined": now,
            "status": "active",
            "referralCode": referral_code,
            "notifications": True,
        }
        if args and args[0].startswith("ref_"):
            ref_code = args[0][4:]
            ref_data = await firebase.get_referral(ref_code)
            if ref_data:
                referrer_id = int(ref_data["referrer"])
                referrer_data = await firebase.get_user(referrer_id)
                if referrer_data:
                    new_user["referredBy"] = ref_code
                    await firebase.use_referral(ref_code, user.id)
                    await firebase.update_user(referrer_id, {
                        "timeLeft": referrer_data.get("timeLeft", 0) + REFERRAL_BONUS,
                        "referrals": referrer_data.get("referrals", 0) + 1,
                    })
                    await firebase.add_log("referral_used", referrer_id, {
                        "by": user.id,
                        "code": ref_code,
                    })
        await firebase.create_user(user.id, new_user)
        await firebase.create_referral(referral_code, user.id)
        await firebase.add_log("user_joined", user.id)
    else:
        banned = await firebase.get_banned_users()
        if str(user.id) in banned:
            await update.message.reply_text("⛔ Your account has been banned. Contact support for more information.")
            return

    is_admin = user.id == ADMIN_ID
    keyboard = get_main_menu_keyboard(is_admin)
    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        f"💡 NRTECNO OTP PANEL\n"
        f"Select an option below:",
        reply_markup=keyboard,
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    banned = await firebase.get_banned_users()
    if str(user.id) in banned:
        await query.edit_message_text("⛔ Your account has been banned. Contact support.")
        return

    data = query.data
    user_data = await firebase.get_user(user.id)

    if data == "get_number":
        await handle_get_number(query, context, user, user_data)
    elif data == "web_panel":
        await query.edit_message_text(
            f"🌐 Web Panel\n\nAccess your web dashboard here:\n{WEB_PANEL_URL}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
        )
    elif data == "send_sms":
        await query.edit_message_text(
            "✉️ Send SMS\n\n"
            "Use the command:\n"
            "/sms +1234567890 \"Your OTP is 123456\"\n\n"
            "The number must be your currently assigned number.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
        )
    elif data == "status":
        await handle_status(query, user, user_data)
    elif data == "refer":
        await handle_refer(query, user, user_data)
    elif data == "time_left":
        await handle_time_left(query, user, user_data)
    elif data == "gift_card":
        await handle_gift_card(query, user_data)
    elif data == "search_number":
        context.user_data["expecting_search"] = True
        await query.edit_message_text(
            "🔍 Search Number\n\nPlease enter the phone number you want to search:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
        )
    elif data == "settings":
        await handle_settings(query, user_data)
    elif data == "support":
        await query.edit_message_text(
            f"📞 Support\n\n"
            f"Contact: {SUPPORT_CONTACT}\n"
            f"Response time: 24 hours\n\n"
            f"FAQ:\n"
            f"1. How to get a number? Tap 📲 GET NUMBER\n"
            f"2. How to earn time? Use 👥 REFER & EARN\n"
            f"3. How to redeem gift card? Tap 🎁 GIFT CARD",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
        )
    elif data == "back_menu":
        is_admin = user.id == ADMIN_ID
        keyboard = get_main_menu_keyboard(is_admin)
        await query.edit_message_text(
            f"👋 Welcome back {user.first_name}!\n\nSelect an option below:",
            reply_markup=keyboard,
        )
    elif data == "admin_panel":
        if user.id == ADMIN_ID:
            await handle_admin_panel(query)
        else:
            await query.edit_message_text("⛔ Unauthorized.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]))
    elif data.startswith("gift_redeem_"):
        code = data.replace("gift_redeem_", "")
        await handle_gift_redeem(query, user, user_data, code)
    elif data == "toggle_notifications":
        current = user_data.get("notifications", True)
        await firebase.update_user(user.id, {"notifications": not current})
        await handle_settings(query, user_data)
    elif data == "admin_users":
        await handle_admin_users(query)
    elif data == "admin_stats":
        await handle_admin_stats(query)
    elif data == "admin_numbers":
        await handle_admin_numbers(query)
    elif data == "admin_logs":
        await handle_admin_logs(query)
    elif data == "admin_referral_stats":
        await handle_admin_referral_stats(query)
    elif data == "admin_broadcast":
        context.user_data["expecting_broadcast"] = True
        await query.edit_message_text("Send the message you want to broadcast to all users:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]))


async def handle_get_number(query, context, user, user_data):
    user_id = user.id
    time_left = user_data.get("timeLeft", 0)
    status = user_data.get("status", "active")

    if status == "expired" or time_left <= 0:
        await query.edit_message_text(
            "⚠️ Your time has expired!\n\n"
            "Get more time:\n"
            "👥 Refer a friend: +1:30 hours\n"
            "🎁 Redeem a gift card",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
        )
        return

    available_numbers = await firebase.get_available_numbers()
    if not available_numbers:
        await query.edit_message_text(
            "⚠️ No numbers available at the moment. Please try again later.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
        )
        return

    available_numbers.sort(key=lambda x: random.random())
    number = available_numbers[0]

    await firebase.release_number(number)
    if user_data.get("currentNumber"):
        old_num = user_data["currentNumber"]
        await firebase.release_number(old_num)

    await firebase.assign_number(number, user.id)

    history = user_data.get("history", [])
    if number not in history:
        history.append(number)

    await firebase.update_user(user_id, {
        "currentNumber": number,
        "history": history,
    })
    user_current_numbers[user.id] = number
    await firebase.add_log("number_assigned", user_id, {"number": number})

    await query.edit_message_text(
        f"📲 Number Assigned!\n\n"
        f"Number: {number}\n"
        f"Time Remaining: {format_time(time_left)}\n\n"
        "You will receive SMS notifications here in real-time.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]),
    )


async def handle_status(query, user, user_data):
    time_left = user_data.get("timeLeft", 0)
    status = "Active" if time_left > 0 else "Expired"
    current_number = user_data.get("currentNumber", "None")
    history = user_data.get("history", [])
    referrals = user_data.get("referrals", 0)
    sms_sent = user_data.get("smsSent", 0)
    joined = user_data.get("joined", "Unknown")

    text = (
        f"📊 Your Status\n\n"
        f"👤 User ID: <code>{user.id}</code>\n"
        f"⏱️ Time Remaining: {format_time(time_left)}\n"
        f"📲 Current Number: {current_number}\n"
        f"👥 Referrals: {referrals}\n"
        f"✉️ SMS Sent: {sms_sent}\n"
        f"🟢 Status: {status}\n"
        f"📅 Joined: {joined}\n"
        f"📋 Numbers Used: {len(history)}"
    )
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
    )


async def handle_refer(query, user, user_data):
    code = user_data.get("referralCode", "N/A")
    referrals = user_data.get("referrals", 0)
    bot_username = (await query.get_bot()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"

    text = (
        f"👥 Refer & Earn\n\n"
        f"Your Referral Code: <code>{code}</code>\n"
        f"Total Referrals: {referrals}\n"
        f"Referral Bonus: +{format_time(REFERRAL_BONUS)} each\n\n"
        f"Share your referral link:\n<code>{link}</code>\n\n"
        f"For each friend who joins using your link, you get +{format_time(REFERRAL_BONUS)} added to your time!"
    )
    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
    )


async def handle_time_left(query, user, user_data):
    time_left = user_data.get("timeLeft", 0)
    status = "🟢 Active" if time_left > 0 else "⛔ Expired"
    exp_str = format_time(time_left)

    keyboard = [
        [InlineKeyboardButton("👥 Refer & Earn", callback_data="refer")],
        [InlineKeyboardButton("🎁 Redeem Gift Card", callback_data="gift_card")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
    ]

    await query.edit_message_text(
        f"⏱️ Time Remaining\n\n"
        f"Time Left: <b>{exp_str}</b>\n"
        f"Status: {status}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_gift_card(query, user_data):
    keyboard = []
    for code, bonus in GIFT_CARDS.items():
        keyboard.append([InlineKeyboardButton(f"{code} (+{format_time(bonus)})", callback_data=f"gift_redeem_{code}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_menu")])

    await query.edit_message_text(
        "🎁 Gift Cards\n\n"
        "Available Cards:\n"
        + "\n".join([f"{code} → +{format_time(bonus)}" for code, bonus in GIFT_CARDS.items()]) +
        "\n\nTap a card to redeem it.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_gift_redeem(query, user, user_data, card_code):
    if card_code not in GIFT_CARDS:
        await query.edit_message_text(
            "⚠️ Invalid gift card code.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="gift_card")]]),
        )
        return
    bonus = GIFT_CARDS[card_code]
    current_time = user_data.get("timeLeft", 0)
    await firebase.update_user(user.id, {"timeLeft": current_time + bonus})
    await firebase.add_log("gift_redeemed", user.id, {"card": card_code, "bonus": bonus})
    await query.edit_message_text(
        f"✅ Gift Card Redeemed!\n\n"
        f"Card: {card_code}\n"
        f"Time Added: +{format_time(bonus)}\n"
        f"New Time: {format_time(current_time + bonus)}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_menu")]]),
    )


async def handle_settings(query, user_data):
    notifications = user_data.get("notifications", True)
    notif_status = "🟢 ON" if notifications else "⛔ OFF"
    keyboard = [
        [InlineKeyboardButton(f"🔔 Notifications: {notif_status}", callback_data="toggle_notifications")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_menu")],
    ]
    await query.edit_message_text(
        "⚙️ Settings\n\nManage your preferences:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_sms_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await update.message.reply_text(
            "Usage: /sms +1234567890 \"Your OTP is 123456\"\nExample: /sms +919876543210 \"Your OTP is 123456\""
        )
        return

    to_number = parts[1]
    message = parts[2].strip("\"'")

    user_data = await firebase.get_user(user.id)
    if not user_data:
        await update.message.reply_text("Please use /start first.")
        return

    current_number = user_data.get("currentNumber")
    if not current_number:
        await update.message.reply_text("You don't have a number assigned. Use 📲 GET NUMBER first.")
        return

    sms_data = {
        "from": current_number,
        "to": to_number,
        "message": message,
        "status": "sent",
        "timestamp": time.time(),
    }
    sms_id = await firebase.post(f"/sms/{user.id}", sms_data)
    await firebase.update_user(user.id, {"smsSent": user_data.get("smsSent", 0) + 1})
    await firebase.add_log("sms_sent", user.id, sms_data)

    if sms_id:
        await update.message.reply_text(
            f"✅ SMS Sent Successfully!\n\n"
            f"From: {current_number}\n"
            f"To: {to_number}\n"
            f"Message: {message}",
        )
    else:
        await update.message.reply_text("❌ Failed to send SMS. Please try again.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    if context.user_data.get("expecting_search"):
        context.user_data["expecting_search"] = False
        await handle_search_result(update, context, text)
        return

    if context.user_data.get("expecting_broadcast") and user.id == ADMIN_ID:
        context.user_data["expecting_broadcast"] = False
        await handle_broadcast_send(update, context, text)
        return

    await update.message.reply_text(
        "Use the buttons below to navigate:",
        reply_markup=get_main_menu_keyboard(user.id == ADMIN_ID),
    )


async def handle_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE, number: str):
    assigned = await firebase.get(f"/numbers/assigned/{number}")
    history = await firebase.get(f"/numbers/history/{number}")
    available = await firebase.get(f"/numbers/available/{number}")

    text = f"🔍 Search Result: {number}\n\n"
    if available:
        text += "🟢 Status: Available\n"
    elif assigned:
        text += f"🔴 Status: Assigned\nAssigned to: {assigned.get('user_id', 'Unknown')}\nAssigned at: {datetime.fromtimestamp(assigned.get('assigned_at', 0)).strftime('%Y-%m-%d %H:%M:%S') if assigned.get('assigned_at') else 'Unknown'}\n"
    else:
        text += "⚫ Status: Unknown / Not in system\n"

    if history:
        text += f"\nHistory:\n"
        if isinstance(history, dict):
            text += f"  Last User: {history.get('user', 'N/A')}\n"
            text += f"  Last Assigned: {datetime.fromtimestamp(history.get('assigned', 0)).strftime('%Y-%m-%d %H:%M:%S') if history.get('assigned') else 'N/A'}\n"
    else:
        text += "\nNo history found.\n"

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]),
    )


# ─── ADMIN HANDLERS ──────────────────────────────────────────

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    await handle_admin_panel(update)

async def handle_admin_panel(update_or_query):
    if isinstance(update_or_query, Update):
        msg = await update_or_query.message.reply_text("Loading admin panel...")
        edit_func = msg.edit_text
    else:
        edit_func = update_or_query.edit_message_text

    users = await firebase.get_all_users()
    total_users = len(users)
    active = sum(1 for u in users.values() if isinstance(u, dict) and u.get("timeLeft", 0) > 0)
    expired = total_users - active
    today = datetime.utcnow().strftime("%Y-%m-%d")
    new_today = sum(1 for u in users.values() if isinstance(u, dict) and u.get("joined", "").startswith(today))
    total_sms = sum(u.get("smsSent", 0) for u in users.values() if isinstance(u, dict))

    keyboard = [
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📱 Numbers", callback_data="admin_numbers")],
        [InlineKeyboardButton("📄 Logs", callback_data="admin_logs")],
        [InlineKeyboardButton("👥 Referral Stats", callback_data="admin_referral_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
    ]

    text = (
        f"🛡️ Admin Dashboard\n\n"
        f"Total Users: {total_users}\n"
        f"Active Users: {active}\n"
        f"Expired Users: {expired}\n"
        f"New Today: {new_today}\n"
        f"Total SMS Sent: {total_sms}"
    )

    await edit_func(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_admin_users(update_or_query):
    if isinstance(update_or_query, Update):
        edit_func = update_or_query.message.reply_text
    else:
        edit_func = update_or_query.edit_message_text

    users = await firebase.get_all_users()
    text = "👥 Users List\n\n"
    for uid, data in users.items():
        if not isinstance(data, dict):
            continue
        name = data.get("name", "N/A")
        tl = data.get("timeLeft", 0)
        refs = data.get("referrals", 0)
        sta = "Active" if tl > 0 else "Expired"
        text += f"ID: {uid} | Time: {format_time(tl)} | Ref: {refs} | {sta}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"

    await edit_func(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]))


async def handle_admin_stats(update_or_query):
    if isinstance(update_or_query, Update):
        edit_func = update_or_query.message.reply_text
    else:
        edit_func = update_or_query.edit_message_text

    users = await firebase.get_all_users()
    now = datetime.utcnow()
    daily = weekly = monthly = 0
    for u in users.values():
        if isinstance(u, dict):
            joined = u.get("joined", "")
            if joined and len(joined) > 10:
                try:
                    jd = datetime.fromisoformat(joined.replace("Z", "+00:00"))
                    if jd.date() == now.date():
                        daily += 1
                    if jd > now - timedelta(days=7):
                        weekly += 1
                    if jd > now - timedelta(days=30):
                        monthly += 1
                except:
                    pass

    bans = await firebase.get_banned_users()
    total_sms = sum(u.get("smsSent", 0) for u in users.values() if isinstance(u, dict))
    active = sum(1 for u in users.values() if isinstance(u, dict) and u.get("timeLeft", 0) > 0)

    text = (
        f"📊 System Statistics\n\n"
        f"Total Users: {len(users)}\n"
        f"Active Users: {active}\n"
        f"Banned Users: {len(bans)}\n"
        f"New Today: {daily}\n"
        f"New This Week: {weekly}\n"
        f"New This Month: {monthly}\n"
        f"Total SMS Sent: {total_sms}\n"
    )

    await edit_func(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]))


async def handle_admin_numbers(update_or_query):
    if isinstance(update_or_query, Update):
        edit_func = update_or_query.message.reply_text
    else:
        edit_func = update_or_query.edit_message_text

    available = await firebase.get_available_numbers()
    assigned_data = await firebase.get("/numbers/assigned")
    assigned = list(assigned_data.keys()) if isinstance(assigned_data, dict) else []

    text = (
        f"📱 Number Pool\n\n"
        f"Available: {len(available)}\n"
        f"Assigned: {len(assigned)}\n\n"
    )
    if available:
        text += "Available:\n" + "\n".join(available[:20]) + "\n"
    if assigned:
        text += "\nAssigned:\n"
        for num in assigned[:20]:
            uid = assigned_data[num].get("user_id", "?") if isinstance(assigned_data.get(num), dict) else "?"
            text += f"{num} -> {uid}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"

    await edit_func(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]))


async def handle_admin_logs(update_or_query):
    if isinstance(update_or_query, Update):
        edit_func = update_or_query.message.reply_text
    else:
        edit_func = update_or_query.edit_message_text

    logs = await firebase.get("/logs")
    text = "📄 System Logs (recent)\n\n"
    if isinstance(logs, dict):
        sorted_keys = sorted(logs.keys(), reverse=True)[:30]
        for key in sorted_keys:
            entry = logs[key]
            if isinstance(entry, dict):
                ts = datetime.fromtimestamp(entry.get("timestamp", 0)).strftime("%H:%M:%S") if entry.get("timestamp") else "?"
                act = entry.get("action", "?")
                uid = entry.get("user", "?")
                text += f"[{ts}] {act} by {uid}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"

    await edit_func(text or "No logs found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]))


async def handle_admin_referral_stats(update_or_query):
    if isinstance(update_or_query, Update):
        edit_func = update_or_query.message.reply_text
    else:
        edit_func = update_or_query.edit_message_text

    users = await firebase.get_all_users()
    referrals = await firebase.get("/referrals")
    total_refs = 0
    top_referrers = []
    for uid, data in users.items():
        if isinstance(data, dict):
            ref_count = data.get("referrals", 0)
            total_refs += ref_count
            if ref_count > 0:
                top_referrers.append((uid, ref_count))
    top_referrers.sort(key=lambda x: x[1], reverse=True)

    text = (
        f"👥 Referral Statistics\n\n"
        f"Total Referrals Given: {total_refs}\n"
        f"Total Referral Codes: {len(referrals) if isinstance(referrals, dict) else 0}\n\n"
        f"Top Referrers:\n"
    )
    if top_referrers:
        for i, (uid, cnt) in enumerate(top_referrers[:10], 1):
            text += f"{i}. User {uid}: {cnt} referrals\n"
    else:
        text += "No referrals yet.\n"

    await edit_func(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]))


async def handle_broadcast_send(update, context, message_text):
    users = await firebase.get_all_users()
    success = 0
    failed = 0
    for uid in users:
        try:
            await context.bot.send_message(int(uid), f"📢 Broadcast from Admin:\n\n{message_text}")
            success += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {uid}: {e}")
            failed += 1
    await update.message.reply_text(
        f"📢 Broadcast Complete\n\n"
        f"Sent: {success}\n"
        f"Failed: {failed}\n"
        f"Total: {success + failed}",
    )


async def admin_addtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    try:
        target_id = int(context.args[0])
        hours = float(context.args[1])
        seconds = int(hours * 3600)
        user_data = await firebase.get_user(target_id)
        if not user_data:
            await update.message.reply_text(f"User {target_id} not found.")
            return
        current = user_data.get("timeLeft", 0)
        await firebase.update_user(target_id, {"timeLeft": current + seconds})
        await firebase.add_log("admin_addtime", target_id, {"added_by": ADMIN_ID, "seconds": seconds})
        await update.message.reply_text(f"Added {hours}h to user {target_id}. New time: {format_time(current + seconds)}")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /admin addtime <user_id> <hours>")


async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    try:
        target_id = int(context.args[0])
        await firebase.ban_user(target_id)
        await firebase.add_log("admin_ban", target_id, {"banned_by": ADMIN_ID})
        await update.message.reply_text(f"User {target_id} has been banned.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /admin ban <user_id>")


async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    try:
        target_id = int(context.args[0])
        await firebase.unban_user(target_id)
        await firebase.add_log("admin_unban", target_id, {"unbanned_by": ADMIN_ID})
        await update.message.reply_text(f"User {target_id} has been unbanned.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /admin unban <user_id>")


async def admin_addnumber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    try:
        number = context.args[0]
        await firebase.add_number(number)
        await firebase.add_log("admin_addnumber", ADMIN_ID, {"number": number})
        await update.message.reply_text(f"Number {number} added to available pool.")
    except IndexError:
        await update.message.reply_text("Usage: /admin addnumber <number>")


async def admin_removenumber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    try:
        number = context.args[0]
        await firebase.remove_number(number)
        await firebase.add_log("admin_removenumber", ADMIN_ID, {"number": number})
        await update.message.reply_text(f"Number {number} removed from pool.")
    except IndexError:
        await update.message.reply_text("Usage: /admin removenumber <number>")


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /admin broadcast <message>")
        return
    message = " ".join(context.args)
    users = await firebase.get_all_users()
    success = 0
    failed = 0
    for uid in users:
        try:
            await context.bot.send_message(int(uid), f"📢 Broadcast from Admin:\n\n{message}")
            success += 1
        except:
            failed += 1
    await update.message.reply_text(f"Broadcast sent. Success: {success}, Failed: {failed}")


async def admin_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Available admin commands:\n"
            "/admin users\n/admin addtime <id> <hours>\n/admin ban <id>\n/admin unban <id>\n"
            "/admin stats\n/admin broadcast <msg>\n/admin numbers\n/admin addnumber <num>\n"
            "/admin removenumber <num>\n/admin referral stats\n/admin logs")
        return

    subcommand = context.args[0].lower()
    if subcommand == "users":
        await handle_admin_users(update)
    elif subcommand == "stats":
        await handle_admin_stats(update)
    elif subcommand == "numbers":
        await handle_admin_numbers(update)
    elif subcommand == "logs":
        await handle_admin_logs(update)
    elif subcommand == "referral" and len(context.args) > 1 and context.args[1] == "stats":
        await handle_admin_referral_stats(update)
    elif subcommand == "addtime" and len(context.args) >= 3:
        await admin_addtime(update, context)
    elif subcommand == "ban" and len(context.args) >= 2:
        await admin_ban(update, context)
    elif subcommand == "unban" and len(context.args) >= 2:
        await admin_unban(update, context)
    elif subcommand == "addnumber" and len(context.args) >= 2:
        await admin_addnumber(update, context)
    elif subcommand == "removenumber" and len(context.args) >= 2:
        await admin_removenumber(update, context)
    elif subcommand == "broadcast" and len(context.args) >= 2:
        await admin_broadcast(update, context)
    else:
        await update.message.reply_text("Unknown admin command or missing arguments.")


# ─── TIME DECAY ──────────────────────────────────────────────

async def time_decay_loop():
    while True:
        try:
            users = await firebase.get_all_users()
            if not isinstance(users, dict):
                await asyncio.sleep(1)
                continue
            for uid, data in users.items():
                if not isinstance(data, dict):
                    continue
                time_left = data.get("timeLeft", 0)
                if time_left > 0:
                    new_time = max(0, time_left - 1)
                    await firebase.update_user(int(uid), {"timeLeft": new_time})
                    if new_time == 0:
                        await firebase.update_user(int(uid), {"status": "expired"})
                        num = data.get("currentNumber")
                        if num:
                            await firebase.release_number(num)
                            await firebase.update_user(int(uid), {"currentNumber": None})
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Time decay error: {e}")
            await asyncio.sleep(5)


# ─── MAIN ────────────────────────────────────────────────────

async def post_init(application: Application):
    connected = await firebase.connect()
    if not connected:
        logger.critical("Failed to connect to any Firebase database. Exiting.")
        raise SystemExit(1)
    logger.info(f"Connected to Firebase: {firebase.db_name}")
    asyncio.create_task(time_decay_loop())
    bot = await application.bot.get_me()
    logger.info(f"Bot started: @{bot.username}")


async def post_shutdown(application: Application):
    await firebase.close()


def main():
    # REMOVED nest_asyncio - FIXES THE ERROR
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("sms", handle_sms_command))
    application.add_handler(CommandHandler("panel", admin_panel_command))
    application.add_handler(CommandHandler("admin", admin_command_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()