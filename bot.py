import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME')

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

# --- ADMIN COMMANDS ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = message.text.split()

    if len(text) > 1:
        ch_id = int(text[1])
        ch_data = channels_col.find_one({"channel_id": ch_id})
        if ch_data:
            markup = InlineKeyboardMarkup()
            # Plans check karein agar set hain
            if 'plans' in ch_data:
                for p_key, p_val in ch_data['plans'].items():
                    label = "1 Month" if p_key == "30" else ("3 Months" if p_key == "90" else "1 Year")
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_val}", callback_data=f"select_{ch_id}_{p_key}"))
            
            markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
            bot.send_message(chat_id, f"Swaagat hai!\n\nAap *{ch_data['name']}* join karna chahte hain. Kripya apna plan chunein:", reply_markup=markup, parse_mode="Markdown")
            return

    if user_id == ADMIN_ID:
        bot.send_message(chat_id, "Admin Panel Active!\n/channels - Manage Channels\n/add - Add New Channel")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    for ch in channels_col.find({"admin_id": ADMIN_ID}):
        markup.add(InlineKeyboardButton(ch['name'], callback_data=f"manage_{ch['channel_id']}"))
    markup.add(InlineKeyboardButton("➕ Add New Channel", callback_data="add_new"))
    bot.send_message(ADMIN_ID, "Aapke Channels:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def add_new_ch(call):
    msg = bot.send_message(ADMIN_ID, "Channel ka koi bhi message yahan FORWARD karein.")
    bot.register_next_step_handler(msg, process_channel_msg)

def process_channel_msg(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"Channel '{ch_name}' mil gaya!\nAb teeno plans ke price is format me likhein:\n`1MonthPrice,3MonthPrice,1YearPrice` \nExample: `99,250,800`")
        bot.register_next_step_handler(msg, save_channel_plans, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "Error: Message forward nahi kiya gaya.")

def save_channel_plans(message, ch_id, ch_name):
    try:
        prices = message.text.split(',')
        plans = {"30": prices[0], "90": prices[1], "365": prices[2]}
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans, "admin_id": ADMIN_ID}}, upsert=True)
        bot_username = bot.get_me().username
        bot.send_message(ADMIN_ID, f"✅ Plans Set!\nLink: `https://t.me/{bot_username}?start={ch_id}`", parse_mode="Markdown")
    except:
        bot.send_message(ADMIN_ID, "Format galat hai. Example: 99,250,800")

# --- USER SELECTION & PAYMENT ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def plan_selected(call):
    _, ch_id, days = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][days]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR%26tn=Sub_{days}Days"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}_{days}"))
    
    bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"Plan: {days} Din\nAmount: ₹{price}\nUPI: `{UPI_ID}`\n\nScan karke pay karein aur niche button dabayein.", 
                   reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def notify_admin(call):
    _, ch_id, days = call.data.split('_')
    user = call.from_user
    price = channels_col.find_one({"channel_id": int(ch_id)})['plans'][days]
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"apprv_{user.id}_{ch_id}_{days}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    
    bot.send_message(ADMIN_ID, f"🔔 *Payment Notification*\nUser: {user.first_name}\nPlan: {days} Din\nPrice: ₹{price}", reply_markup=markup)
    bot.send_message(call.message.chat.id, "Payment verification ke liye bhej di gayi hai.")

# --- APPROVAL ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('apprv_'))
def approve_user(call):
    _, u_id, ch_id, days = call.data.split('_')
    u_id, ch_id, days = int(u_id), int(ch_id), int(days)
    
    try:
        link_obj = bot.create_chat_invite_link(ch_id, member_limit=1)
        expiry = datetime.now() + timedelta(days=days)
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, {"$set": {"expiry": expiry.timestamp()}}, upsert=True)
        
        bot.send_message(u_id, f"✅ Approved! Aapka {days} din ka subscription shuru ho gaya hai.\nLink: {link_obj.invite_link}")
        bot.edit_message_text(f"Approved for {days} days!", call.message.chat.id, call.message.message_id)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"Error: {e}")

# --- AUTO KICKER ---
def kick_expired():
    now = datetime.now().timestamp()
    for user in users_col.find({"expiry": {"$lte": now}}):
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            bot.send_message(user['user_id'], "Aapka subscription khatam ho gaya hai. Dobara join karne ke liye link par click karein.")
            users_col.delete_one({"_id": user['_id']})
        except: pass

scheduler = BackgroundScheduler()
scheduler.add_job(kick_expired, 'interval', minutes=10)
scheduler.start()

bot.infinity_polling()
