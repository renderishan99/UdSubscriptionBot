import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

# --- CONFIGURATION (Render ke Environment Variables se lega) ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
UPI_ID = os.getenv('UPI_ID')
CONTACT_USERNAME = os.getenv('CONTACT_USERNAME') # Bina @ ke (e.g. Rahul_Admin)

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

    # Agar User kisi deep link se aaya hai (e.g. /start -100123)
    if len(text) > 1:
        ch_id = int(text[1])
        ch_data = channels_col.find_one({"channel_id": ch_id})
        
        if ch_data:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(f"💳 Pay Now to Get Link (₹{ch_data['price']})", callback_data=f"pay_{ch_id}"))
            markup.add(InlineKeyboardButton("📞 Contact Admin", url=f"https://t.me/{CONTACT_USERNAME}"))
            
            bot.send_message(chat_id, 
                f"Swaagat hai!\n\nAap *{ch_data['name']}* join karna chahte hain.\n\n"
                f"Niche diye gaye button par click karke payment karein aur joining link payein.", 
                reply_markup=markup, parse_mode="Markdown")
            return

    # Admin Panel (Sirf Admin ke liye)
    if user_id == ADMIN_ID:
        bot.send_message(chat_id, "Welcome Admin! Channel manage karne ke liye /channels likhein.")
    else:
        bot.send_message(chat_id, "Swaagat hai! Join karne ke liye admin ke link par click karein.")

@bot.message_handler(commands=['channels'], func=lambda m: m.from_user.id == ADMIN_ID)
def list_channels(message):
    markup = InlineKeyboardMarkup()
    saved_channels = channels_col.find({"admin_id": ADMIN_ID})
    for ch in saved_channels:
        markup.add(InlineKeyboardButton(ch['name'], callback_data=f"info_{ch['channel_id']}"))
    markup.add(InlineKeyboardButton("➕ Add New Channel", callback_data="add_new"))
    bot.send_message(ADMIN_ID, "Aapke Channels:", reply_markup=markup)

# --- CHANNEL ADDING LOGIC ---

@bot.callback_query_handler(func=lambda call: call.data == "add_new")
def add_new_ch(call):
    msg = bot.send_message(ADMIN_ID, "Bot ko apne channel mein Admin banayein, phir channel ka koi bhi message yahan FORWARD karein.")
    bot.register_next_step_handler(msg, process_channel_msg)

def process_channel_msg(message):
    if message.forward_from_chat:
        ch_id = message.forward_from_chat.id
        ch_name = message.forward_from_chat.title
        msg = bot.send_message(ADMIN_ID, f"Channel '{ch_name}' mil gaya!\nAb iska Price (₹) likhein:")
        bot.register_next_step_handler(msg, save_channel, ch_id, ch_name)
    else:
        bot.send_message(ADMIN_ID, "Error: Message forward nahi kiya gaya. Dubara koshish karein.")

def save_channel(message, ch_id, ch_name):
    try:
        price = message.text
        channels_col.update_one({"channel_id": ch_id}, {"$set": {"name": ch_name, "price": price, "admin_id": ADMIN_ID}}, upsert=True)
        bot_username = bot.get_me().username
        deep_link = f"https://t.me/{bot_username}?start={ch_id}"
        bot.send_message(ADMIN_ID, f"✅ Setup Done!\n\nUser Join Link:\n`{deep_link}`", parse_mode="Markdown")
    except:
        bot.send_message(ADMIN_ID, "Price galat hai.")

# --- PAYMENT FLOW ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_'))
def show_payment(call):
    ch_id = int(call.data.split('_')[1])
    ch_data = channels_col.find_one({"channel_id": ch_id})
    
    # QR Code API (UPI)
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa={UPI_ID}%26am={ch_data['price']}%26cu=INR%26tn=Subscription"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ I Have Paid", callback_data=f"paid_{ch_id}"))
    
    bot.send_photo(call.message.chat.id, qr_url, 
                   caption=f"Plan: {ch_data['name']}\nAmount: ₹{ch_data['price']}\n\n"
                           f"UPI ID: `{UPI_ID}`\n\n"
                           "Upar QR scan karein ya UPI ID par pay karein. Payment ke baad button dabayein.", 
                   reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paid_'))
def notify_admin(call):
    ch_id = int(call.data.split('_')[1])
    user = call.from_user
    ch_data = channels_col.find_one({"channel_id": ch_id})
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"apprv_{user.id}_{ch_id}"))
    markup.add(InlineKeyboardButton("❌ Reject", callback_data=f"rej_{user.id}"))
    
    bot.send_message(ADMIN_ID, f"🔔 *Payment Alert!*\n\nUser: {user.first_name} (@{user.username})\n"
                               f"Channel: {ch_data['name']}\nPrice: ₹{ch_data['price']}", 
                     reply_markup=markup, parse_mode="Markdown")
    bot.answer_callback_query(call.id, "Admin ko notify kar diya gaya hai.")
    bot.send_message(call.message.chat.id, "Wait karein, admin payment check karke link bhej raha hai...")

# --- APPROVAL & AUTO-KICK ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('apprv_'))
def approve_user(call):
    data = call.data.split('_')
    u_id, ch_id = int(data[1]), int(data[2])
    
    try:
        # Create Single-use Invite Link
        link_obj = bot.create_chat_invite_link(ch_id, member_limit=1)
        
        # Save to DB (30 days expiry)
        expiry = datetime.now() + timedelta(days=30)
        users_col.update_one({"user_id": u_id, "channel_id": ch_id}, 
                             {"$set": {"expiry": expiry.timestamp()}}, upsert=True)
        
        bot.send_message(u_id, f"✅ Payment Approved! Aapka joining link ye raha:\n{link_obj.invite_link}\n\n"
                               "Note: Ye link sirf 1 baar kaam karega.")
        bot.edit_message_text("Approve kar diya!", call.message.chat.id, call.message.message_id)
    except Exception as e:
        bot.send_message(ADMIN_ID, f"Error: Bot channel me admin nahi hai ya permission nahi hai.\n{e}")

# Kicking Logic (Background Task)
def kick_expired():
    now = datetime.now().timestamp()
    expired = users_col.find({"expiry": {"$lte": now}})
    for user in expired:
        try:
            bot.ban_chat_member(user['channel_id'], user['user_id'])
            bot.unban_chat_member(user['channel_id'], user['user_id'])
            bot.send_message(user['user_id'], "Aapka subscription khatam ho gaya hai. Dobara join karne ke liye /start karein.")
            users_col.delete_one({"_id": user['_id']})
        except:
            pass

scheduler = BackgroundScheduler()
scheduler.add_job(kick_expired, 'interval', minutes=10)
scheduler.start()

print("Bot is running...")
bot.infinity_polling()
