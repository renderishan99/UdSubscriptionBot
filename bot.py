import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from threading import Thread

# --- RENDER KEEP-ALIVE SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running and healthy!"

def run_web():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    Thread(target=run_web).start()

# --- CONFIGURATION (Environment Variables) ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME') # Example: rahul_admin

bot = telebot.TeleBot(BOT_TOKEN)
client = MongoClient(MONGO_URI)
db = client['sub_management']
channels_col = db['channels']
users_col = db['users']

# --- ADMIN LOGIC ---

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    text = message.text.split()

    # User entry via Deep Link (e.g., t.me/bot?start=-100123)
    if len(text) > 1:
        try:
            ch_id = int(text[1])
            ch_data = channels_col.find_one({"channel_id": ch_id})
            if ch_data:
                markup = InlineKeyboardMarkup()
                # Display Dynamic Plans
                for p_time, p_price in ch_data['plans'].items():
                    label = f"{p_time} Min" if int(p_time) < 60 else f"{int(p_time)//1440} Din"
                    markup.add(InlineKeyboardButton(f"💳 {label} - ₹{p_price}", callback_data=f"select_{ch_id}_{p_time}"))
                
                markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
                bot.send_message(message.chat.id, 
                    f"Swaagat hai!\n\nAap *{ch_data['name']}* join karna chahte hain.\n\nNiche se apna subscription plan chunein:", 
                    reply_markup=markup, parse_mode="Markdown")
                return
        except: pass

    # Admin Panel Greeting
    if user_id == ADMIN_ID:
        bot.send_message(message.chat.id, "✅ Admin Panel Active!\n\n/add - Add or Edit Channel/Prices\n/channels - List all channels")
    else:
        bot.send_message(message.chat.id, "Swaagat hai! Join karne ke liye admin ke diye huye link par click karein.")

@bot.message_handler(commands=['add'], func=lambda m: m.from_user.id == ADMIN_ID)
def add_channel_start(message):
    msg = bot.send_message(ADMIN_ID, "Bot ko channel me admin banayein, phir channel ka koi bhi message yahan FORWARD karein.")
    bot.register_next_step_handler(msg, get_plans)

def get_plans(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, 
            f"Channel: *{ch_name}* mil gaya.\n\nAb plans likhein is format mein (Minutes:Price):\n`Min:Price, Min:Price` \n\n"
            "Example (1 min testing aur 1 mahina real):\n`1:5, 43200:199`", parse_mode="Markdown")
        bot.register_next_step_handler(msg, finalize_channel, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "❌ Error: Message forward nahi kiya gaya. Dubara /add karein.")

def finalize_channel(message, ch_id, ch_name):
    try:
        raw_plans = message.text.split(',')
        plans_dict = {}
        for p in raw_plans:
            t, pr = p.strip().split(':')
            plans_dict[t] = pr
        
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "plans": plans_dict, "admin_id": ADMIN_ID}}, upsert=True)
        bot_username = bot.get_me().username
        bot.send_message(ADMIN_ID, f"✅ Setup Successful!\n\nShare this link with users:\n`https://t.me/{bot_username}?start={ch_id}`", parse_mode="Markdown")
    except:
        bot.send_message(ADMIN_ID, "❌ Format galat hai! Use `Min:Price, Min:Price` format. Dubara /add karein.")

# --- USER: PAYMENT FLOW ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def user_pays(call):
    _, ch_id, mins = call.data.split('_')
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={price}%26cu=INR"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid (Verify)", callback_data=f"paid_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    
    bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"Plan: {mins} Minutes\nAmount: ₹{price}\n\nScan QR or Pay to:\n`{UPI_ID}`\n\nPayment ke baad niche 'I Have Paid' dabayein.", 
                   reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def admin_notify(call):
    _, ch_id, mins = call.data.split('_')
    user = call.from_user
    ch_data = channels_col.find_one({"channel_id": int(ch_id)})
    price = ch_data['plans'][mins]
    
    # Notify Admin
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"app_{user.id}_{ch_id}_{mins}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    
    bot.send_message(ADMIN_ID, f"🔔 *Payment Alert!*\n\nUser: {user.first_name} (@{user.username})\nChannel: {ch_data['name']}\nPlan: {mins} Mins\nPrice: ₹{price}", 
                     reply_markup=markup, parse_mode="Markdown")
    
    # Notify User
    u_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
    bot.send_message(call.message.chat.id, "✅ Aapki payment request admin ko bhej di gayi hai. Verification ke baad aapko joining link mil jayega.", reply_markup=u_markup)

# --- APPROVAL & EXPIRY LOGIC ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('app_'))
def approve_now(call):
    _, u_id, ch_id, mins = call.data.split('_')
    u_id, ch_id, mins = int(u_id), int(ch_id), int(mins)
    
    try:
        # Calculate Exact Expiry
        expiry_datetime = datetime.now() + timedelta(minutes=mins)
        expiry_ts = int(expiry_datetime.timestamp())

        # Create Invite Link that expires exactly when the subscription ends
        link = bot.create_chat_invite_link(
            ch_id, 
            member_limit=1, 
            expire_date=expiry_ts
        )
        
        users_col.update_one(
            {"user_id": u_id, "channel_id": ch_id}, 
            {"$set": {"expiry": expiry_datetime.timestamp()}}, 
            upsert=True
        )
        
        # Notify User
        bot.send_message(u_id, f"🥳 *Payment Approved!*\n\nSubscription Time: {mins} Minutes\n\nJoin karne ke liye niche link par click karein:\n{link.invite_link}\n\n⚠️ Note: Ye link aur aapka access {mins} minute baad khatam ho jayega.", parse_mode="Markdown")
        
        bot.edit_message_text(f"✅ User {u_id} approved for {mins} mins.", call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Error: {e}")

# Automate Kicking
def kick_expired_users():
    now = datetime.now().timestamp()
    expired_users = users_col.find({"expiry": {"$lte": now}})
    for user in expired_users:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Dobara Join Karein", url=f"https://t.me/{bot.get_me().username}"))
            
            bot.send_message(user['user_id'], "⚠️ Aapka subscription khatam ho gaya hai. Dobara join karne ke liye bot se naya plan lein.", reply_markup=markup)
            users_col.delete_one({"_id": user['_id']})
        except Exception as e:
            print(f"Kick Error: {e}")

# --- STARTUP ---
if __name__ == '__main__':
    keep_alive() # Starts Flask on Thread
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, 'interval', minutes=1)
    scheduler.start()
    
    bot.remove_webhook()
    print("Bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
