import os
import sys
import logging
import time
import json
import threading
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
import telebot
from telebot import types

# ==================== CONFIG ====================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Apna token daalo
ADMIN_IDS = []
DATABASE_PATH = 'bot_database.db'
PORT = int(os.getenv('PORT', 8080))

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("❌ BOT_TOKEN set karo!")
    sys.exit(1)

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        self.db_path = DATABASE_PATH
        self.init_tables()
    
    def get_conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)
    
    def init_tables(self):
        conn = self.get_conn()
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            subscription_plan_id INTEGER,
            subscription_expiry TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_admin INTEGER DEFAULT 0
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS plans (
            plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            validity_days INTEGER NOT NULL,
            channel_link TEXT,
            description TEXT,
            media_json TEXT DEFAULT '[]',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS payments (
            payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            screenshot_file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        )''')
        
        defaults = [
            ('welcome_image', ''),
            ('welcome_text', 'Welcome to Premium Bot! 🎉\n\nChoose a plan below to get started.'),
            ('bot_name', 'PREMIUM BOT'),
            ('upi_id', ''),
            ('qr_code', ''),
            ('delivery_link', ''),
            ('welcome_video', '')
        ]
        for key, val in defaults:
            c.execute('INSERT OR IGNORE INTO settings (setting_key, setting_value) VALUES (?, ?)', (key, val))
        
        conn.commit()
        conn.close()
        logger.info("✅ Database ready")
    
    def add_user(self, user_id, username='', first_name='', last_name=''):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                 (user_id, username, first_name, last_name))
        conn.commit()
        conn.close()
    
    def get_user(self, user_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        if row:
            cols = [d[0] for d in c.description]
            return dict(zip(cols, row))
        return None
    
    def get_all_users(self):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM users ORDER BY created_at DESC')
        rows = c.fetchall()
        conn.close()
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, row)) for row in rows]
    
    def set_admin(self, user_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('UPDATE users SET is_admin = 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def is_admin(self, user_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        return row and row[0] == 1
    
    def update_subscription(self, user_id, plan_id, days):
        conn = self.get_conn()
        c = conn.cursor()
        expiry = (datetime.now() + timedelta(days=days)).isoformat()
        c.execute('UPDATE users SET subscription_plan_id = ?, subscription_expiry = ? WHERE user_id = ?',
                 (plan_id, expiry, user_id))
        conn.commit()
        conn.close()
    
    def add_plan(self, name, price, days, channel_link, description=''):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT INTO plans (name, price, validity_days, channel_link, description) VALUES (?, ?, ?, ?, ?)',
                 (name, price, days, channel_link, description))
        plan_id = c.lastrowid
        conn.commit()
        conn.close()
        return plan_id
    
    def get_plan(self, plan_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM plans WHERE plan_id = ? AND is_active = 1', (plan_id,))
        row = c.fetchone()
        conn.close()
        if row:
            cols = [d[0] for d in c.description]
            return dict(zip(cols, row))
        return None
    
    def get_all_plans(self):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM plans WHERE is_active = 1 ORDER BY price ASC')
        rows = c.fetchall()
        conn.close()
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, row)) for row in rows]
    
    def update_plan(self, plan_id, **kwargs):
        conn = self.get_conn()
        c = conn.cursor()
        allowed = ['name', 'price', 'validity_days', 'channel_link', 'description', 'media_json']
        updates = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                vals.append(v)
        if updates:
            vals.append(plan_id)
            c.execute(f"UPDATE plans SET {', '.join(updates)} WHERE plan_id = ?", vals)
            conn.commit()
        conn.close()
    
    def delete_plan(self, plan_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('UPDATE plans SET is_active = 0 WHERE plan_id = ?', (plan_id,))
        conn.commit()
        conn.close()
    
    def add_media(self, plan_id, media_type, file_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT media_json FROM plans WHERE plan_id = ?', (plan_id,))
        row = c.fetchone()
        if row:
            media_list = json.loads(row[0]) if row[0] else []
            media_list.append({'type': media_type, 'file_id': file_id, 'added_at': datetime.now().isoformat()})
            c.execute('UPDATE plans SET media_json = ? WHERE plan_id = ?', (json.dumps(media_list), plan_id))
            conn.commit()
        conn.close()
    
    def get_plan_media(self, plan_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT media_json FROM plans WHERE plan_id = ?', (plan_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return json.loads(row[0]) if row[0] else []
        return []
    
    def add_payment(self, user_id, plan_id, amount, file_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT INTO payments (user_id, plan_id, amount, screenshot_file_id) VALUES (?, ?, ?, ?)',
                 (user_id, plan_id, amount, file_id))
        payment_id = c.lastrowid
        conn.commit()
        conn.close()
        return payment_id
    
    def get_payment(self, payment_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT * FROM payments WHERE payment_id = ?', (payment_id,))
        row = c.fetchone()
        conn.close()
        if row:
            cols = [d[0] for d in c.description]
            return dict(zip(cols, row))
        return None
    
    def get_pending_payments(self):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('''
            SELECT p.*, u.username, u.first_name, u.last_name, pl.name as plan_name
            FROM payments p
            JOIN users u ON p.user_id = u.user_id
            JOIN plans pl ON p.plan_id = pl.plan_id
            WHERE p.status = 'pending'
            ORDER BY p.created_at ASC
        ''')
        rows = c.fetchall()
        conn.close()
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, row)) for row in rows]
    
    def approve_payment(self, payment_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('UPDATE payments SET status = "approved" WHERE payment_id = ?', (payment_id,))
        c.execute('SELECT user_id, plan_id FROM payments WHERE payment_id = ?', (payment_id,))
        row = c.fetchone()
        if row:
            user_id, plan_id = row
            plan = self.get_plan(plan_id)
            if plan:
                self.update_subscription(user_id, plan_id, plan['validity_days'])
        conn.commit()
        conn.close()
    
    def reject_payment(self, payment_id):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('UPDATE payments SET status = "rejected" WHERE payment_id = ?', (payment_id,))
        conn.commit()
        conn.close()
    
    def get_setting(self, key):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT setting_value FROM settings WHERE setting_key = ?', (key,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else ''
    
    def set_setting(self, key, value):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO settings (setting_key, setting_value) VALUES (?, ?)', (key, value))
        conn.commit()
        conn.close()
    
    def get_stats(self):
        conn = self.get_conn()
        c = conn.cursor()
        stats = {}
        c.execute('SELECT COUNT(*) FROM users')
        stats['users'] = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM plans WHERE is_active = 1')
        stats['plans'] = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM payments WHERE status = "pending"')
        stats['pending'] = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM payments WHERE status = "approved"')
        stats['approved'] = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM payments WHERE status = "rejected"')
        stats['rejected'] = c.fetchone()[0]
        conn.close()
        return stats

# ==================== BOT INIT ====================
db = Database()
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# Load settings
WELCOME_IMAGE = db.get_setting('welcome_image')
WELCOME_VIDEO = db.get_setting('welcome_video')
WELCOME_TEXT = db.get_setting('welcome_text')
BOT_NAME = db.get_setting('bot_name')
UPI_ID = db.get_setting('upi_id')
QR_CODE = db.get_setting('qr_code')

user_data = {}
bot_running = True

# ==================== HTTP SERVER ====================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health' or self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK' if self.path == '/health' else b'Bot Running')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, *args, **kwargs):
        pass

def run_http():
    try:
        server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
        logger.info(f"🌐 HTTP Server: http://0.0.0.0:{PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"HTTP error: {e}")

# ==================== HELPERS ====================
def is_admin(user_id):
    return user_id in ADMIN_IDS or db.is_admin(user_id)

def safe_edit(chat_id, msg_id, text, **kwargs):
    try:
        bot.edit_message_text(text, chat_id, msg_id, **kwargs)
    except:
        pass

def safe_send(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except:
        return None

def safe_photo(chat_id, photo, caption='', **kwargs):
    try:
        return bot.send_photo(chat_id, photo, caption=caption, **kwargs)
    except:
        return None

def safe_video(chat_id, video, caption='', **kwargs):
    try:
        return bot.send_video(chat_id, video, caption=caption, **kwargs)
    except:
        return None

# ==================== KEYBOARDS ====================

# Main keyboard - Only Plans, How to Use, Report Issue
def main_keyboard(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📋 Plans", callback_data="show_plans"))
    kb.row(
        types.InlineKeyboardButton("📖 How to Use", callback_data="how_to_use"),
        types.InlineKeyboardButton("📞 Report Issue", callback_data="report_issue")
    )
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel"))
    return kb

# Plans keyboard - Clean style like image
def plans_keyboard():
    plans = db.get_all_plans()
    kb = types.InlineKeyboardMarkup(row_width=2)
    for p in plans:
        # Style like image: ₹49 / 30d
        label = f"₹{int(p['price'])} / {p['validity_days']}d"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"view_plan_{p['plan_id']}"))
    return kb

def plan_detail_keyboard(plan_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 Buy Now", callback_data=f"buy_plan_{plan_id}"))
    kb.add(types.InlineKeyboardButton("🔙 Back to Plans", callback_data="show_plans"))
    return kb

def payment_keyboard(plan_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ I Paid", callback_data=f"i_paid_{plan_id}"))
    kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="show_plans"))
    return kb

def admin_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
        types.InlineKeyboardButton("👥 Users", callback_data="admin_users")
    )
    kb.row(
        types.InlineKeyboardButton("📋 Plans", callback_data="admin_plans"),
        types.InlineKeyboardButton("💳 Payments", callback_data="admin_payments")
    )
    kb.row(
        types.InlineKeyboardButton("🖼️ Welcome Image", callback_data="admin_welcome_img"),
        types.InlineKeyboardButton("🎬 Welcome Video", callback_data="admin_welcome_video"),
        types.InlineKeyboardButton("📝 Welcome Text", callback_data="admin_welcome_text")
    )
    kb.row(
        types.InlineKeyboardButton("💰 UPI ID", callback_data="admin_upi"),
        types.InlineKeyboardButton("📱 QR Code", callback_data="admin_qr")
    )
    kb.row(
        types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("🏷️ Bot Name", callback_data="admin_bot_name")
    )
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_main"))
    return kb

def admin_plans_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Add Plan", callback_data="admin_add_plan"),
        types.InlineKeyboardButton("📝 Edit Plan", callback_data="admin_edit_plan_list")
    )
    kb.row(
        types.InlineKeyboardButton("🗑️ Delete Plan", callback_data="admin_delete_plan_list"),
        types.InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
    )
    return kb

def plan_list_keyboard(action):
    plans = db.get_all_plans()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for p in plans:
        kb.add(types.InlineKeyboardButton(f"{p['name']} - ₹{int(p['price'])}", 
                 callback_data=f"{action}_{p['plan_id']}"))
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_plans"))
    return kb

def edit_plan_keyboard(plan_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("✏️ Name", callback_data=f"edit_name_{plan_id}"),
        types.InlineKeyboardButton("💰 Price", callback_data=f"edit_price_{plan_id}"),
        types.InlineKeyboardButton("📅 Validity", callback_data=f"edit_validity_{plan_id}"),
        types.InlineKeyboardButton("🔗 Channel Link", callback_data=f"edit_link_{plan_id}"),
        types.InlineKeyboardButton("📎 Add Media (5 videos/photos)", callback_data=f"edit_media_{plan_id}")
    )
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_plans"))
    return kb

# ==================== START COMMAND ====================
@bot.message_handler(commands=['start'])
def start_cmd(msg):
    user_id = msg.from_user.id
    db.add_user(user_id, msg.from_user.username or '', msg.from_user.first_name or '', msg.from_user.last_name or '')
    
    # Auto admin - first user
    if not ADMIN_IDS:
        ADMIN_IDS.append(user_id)
        db.set_admin(user_id)
        bot.send_message(user_id, "✅ You are the ADMIN! Use /admin for panel.")
    
    if user_id in ADMIN_IDS:
        db.set_admin(user_id)
    
    # Notify admin about new user
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, f"👤 New user started bot!\n\nID: {user_id}\nName: {msg.from_user.first_name}\nUsername: @{msg.from_user.username or 'N/A'}")
        except:
            pass
    
    # Send welcome with image/video
    text = f"<b>{BOT_NAME}</b>\n\n{WELCOME_TEXT}\n\nChoose a plan below:"
    
    # Send welcome media first
    if WELCOME_VIDEO:
        safe_video(user_id, WELCOME_VIDEO, caption=text)
    elif WELCOME_IMAGE:
        safe_photo(user_id, WELCOME_IMAGE, caption=text)
    else:
        safe_send(user_id, text)
    
    # Then send plans
    plans = db.get_all_plans()
    if plans:
        plans_text = "Choose a plan below:"
        safe_send(user_id, plans_text, reply_markup=plans_keyboard())
    else:
        safe_send(user_id, "No plans available yet.", 
                 reply_markup=types.InlineKeyboardMarkup().add(
                 types.InlineKeyboardButton("🔙 Back", callback_data="back_main")))

# ==================== ADMIN COMMAND ====================
@bot.message_handler(commands=['admin'])
def admin_cmd(msg):
    user_id = msg.from_user.id
    if is_admin(user_id):
        text = f"<b>⚙️ Admin Panel</b>\n\nManage your bot settings and content."
        safe_send(user_id, text, reply_markup=admin_keyboard())
    else:
        safe_send(user_id, "❌ Unauthorized access!")

# ==================== CALLBACK HANDLER ====================
@bot.callback_query_handler(func=lambda c: True)
def handle_cb(call):
    user_id = call.from_user.id
    data = call.data
    
    try:
        # ========== BACK ==========
        if data == "back_main":
            text = f"<b>{BOT_NAME}</b>\n\n{WELCOME_TEXT}\n\nChoose a plan below:"
            if WELCOME_VIDEO:
                safe_video(user_id, WELCOME_VIDEO, caption=text)
                bot.delete_message(call.message.chat.id, call.message.message_id)
            elif WELCOME_IMAGE:
                safe_photo(user_id, WELCOME_IMAGE, caption=text)
                bot.delete_message(call.message.chat.id, call.message.message_id)
            else:
                safe_send(user_id, text)
            # Send plans
            plans = db.get_all_plans()
            if plans:
                safe_send(user_id, "Choose a plan below:", reply_markup=plans_keyboard())
            bot.delete_message(call.message.chat.id, call.message.message_id)
        
        # ========== SHOW PLANS ==========
        elif data == "show_plans":
            plans = db.get_all_plans()
            if not plans:
                safe_edit(call.message.chat.id, call.message.message_id, 
                         "❌ No plans available yet.",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Back", callback_data="back_main")))
                return
            
            # If already showing plans, just update
            try:
                safe_edit(call.message.chat.id, call.message.message_id, 
                         "Choose a plan below:", reply_markup=plans_keyboard())
            except:
                safe_send(user_id, "Choose a plan below:", reply_markup=plans_keyboard())
                bot.delete_message(call.message.chat.id, call.message.message_id)
        
        # ========== HOW TO USE ==========
        elif data == "how_to_use":
            text = "📖 <b>How to Use</b>\n\n1️⃣ Choose a plan from the list\n2️⃣ Click Buy Now\n3️⃣ Make payment via UPI/QR\n4️⃣ Send screenshot\n5️⃣ Admin approves\n6️⃣ Get channel access!"
            safe_edit(call.message.chat.id, call.message.message_id, text,
                     reply_markup=types.InlineKeyboardMarkup().add(
                     types.InlineKeyboardButton("🔙 Back", callback_data="back_main")))
        
        # ========== REPORT ISSUE ==========
        elif data == "report_issue":
            text = "📞 <b>Report Issue</b>\n\nContact admin: @admin\n\nOr send a message with your issue."
            safe_edit(call.message.chat.id, call.message.message_id, text,
                     reply_markup=types.InlineKeyboardMarkup().add(
                     types.InlineKeyboardButton("🔙 Back", callback_data="back_main")))
        
        # ========== VIEW PLAN ==========
        elif data.startswith("view_plan_"):
            plan_id = int(data.split("_")[2])
            plan = db.get_plan(plan_id)
            if plan:
                media = db.get_plan_media(plan_id)
                
                # Show media first (5 items)
                for m in media[:5]:
                    try:
                        if m['type'] == 'photo':
                            bot.send_photo(user_id, m['file_id'], caption=f"📎 {plan['name']}")
                        elif m['type'] == 'video':
                            bot.send_video(user_id, m['file_id'], caption=f"📎 {plan['name']}")
                    except:
                        pass
                
                # Show plan details
                text = f"<b>{plan['name']}</b>\n\n"
                text += f"💰 Price: <b>₹{int(plan['price'])}</b>\n"
                text += f"📅 Validity: <b>{plan['validity_days']} days</b>\n"
                text += f"📎 {len(media)} items included"
                
                safe_send(user_id, text, reply_markup=plan_detail_keyboard(plan_id))
                bot.delete_message(call.message.chat.id, call.message.message_id)
            else:
                bot.answer_callback_query(call.id, "Plan not found!")
        
        # ========== BUY PLAN ==========
        elif data.startswith("buy_plan_"):
            plan_id = int(data.split("_")[2])
            plan = db.get_plan(plan_id)
            if plan:
                user_data[user_id] = {'buying_plan': plan_id}
                
                qr = db.get_setting('qr_code')
                upi = db.get_setting('upi_id')
                
                text = f"<b>💳 Payment for {plan['name']}</b>\n\n"
                text += f"💰 Amount: <b>₹{int(plan['price'])}</b>\n"
                text += f"📅 Validity: {plan['validity_days']} days\n\n"
                
                if qr:
                    text += "📱 Scan QR code to pay:"
                    safe_send(user_id, text)
                    safe_photo(user_id, qr, caption="📱 QR Code")
                elif upi:
                    text += f"💰 Send to UPI: <b>{upi}</b>\n"
                    safe_send(user_id, text)
                else:
                    text += "❌ Payment method not configured. Contact admin."
                    safe_send(user_id, text)
                
                text = "After payment, click <b>I Paid</b> and send screenshot."
                safe_send(user_id, text, reply_markup=payment_keyboard(plan_id))
                bot.delete_message(call.message.chat.id, call.message.message_id)
            else:
                bot.answer_callback_query(call.id, "Plan not found!")
        
        # ========== I PAID ==========
        elif data.startswith("i_paid_"):
            plan_id = int(data.split("_")[2])
            plan = db.get_plan(plan_id)
            if plan:
                user_data[user_id] = {'screenshot_plan': plan_id}
                text = f"📤 <b>Upload Payment Screenshot</b>\n\n"
                text += f"Plan: {plan['name']}\n"
                text += f"Amount: ₹{int(plan['price'])}\n\n"
                text += "Send the payment screenshot as a photo."
                
                kb = types.InlineKeyboardMarkup(row_width=1)
                kb.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="show_plans"))
                
                safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
            else:
                bot.answer_callback_query(call.id, "Plan not found!")
        
        # ========== ADMIN PANEL ==========
        elif data == "admin_panel":
            if is_admin(user_id):
                text = f"<b>⚙️ Admin Panel</b>\n\nWelcome {BOT_NAME} admin!"
                safe_edit(call.message.chat.id, call.message.message_id, text, 
                         reply_markup=admin_keyboard())
            else:
                bot.answer_callback_query(call.id, "Unauthorized!")
        
        elif data == "admin_stats":
            if is_admin(user_id):
                s = db.get_stats()
                text = f"<b>📊 Statistics</b>\n\n"
                text += f"👥 Total Users: {s['users']}\n"
                text += f"📋 Active Plans: {s['plans']}\n"
                text += f"🕐 Pending Payments: {s['pending']}\n"
                text += f"✅ Approved Payments: {s['approved']}\n"
                text += f"❌ Rejected Payments: {s['rejected']}"
                safe_edit(call.message.chat.id, call.message.message_id, text,
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Back", callback_data="admin_panel")))
        
        elif data == "admin_users":
            if is_admin(user_id):
                users = db.get_all_users()
                text = f"<b>👥 Users</b> ({len(users)})\n\n"
                for u in users[:20]:
                    name = u.get('first_name', 'Unknown')
                    uname = u.get('username', '')
                    text += f"👤 {name}" + (f" @{uname}" if uname else "") + "\n"
                if len(users) > 20:
                    text += f"\n... and {len(users)-20} more"
                safe_edit(call.message.chat.id, call.message.message_id, text,
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Back", callback_data="admin_panel")))
        
        # ========== ADMIN PLANS ==========
        elif data == "admin_plans":
            if is_admin(user_id):
                text = "📋 <b>Plan Management</b>\n\nManage your subscription plans:"
                safe_edit(call.message.chat.id, call.message.message_id, text, 
                         reply_markup=admin_plans_keyboard())
        
        elif data == "admin_add_plan":
            if is_admin(user_id):
                user_data[user_id] = {'add_plan': True, 'step': 'name'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "➕ <b>Add New Plan</b>\n\nStep 1/5: Enter plan name:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_plans")))
        
        elif data == "admin_edit_plan_list":
            if is_admin(user_id):
                plans = db.get_all_plans()
                if plans:
                    safe_edit(call.message.chat.id, call.message.message_id,
                             "📝 Select plan to edit:",
                             reply_markup=plan_list_keyboard("admin_edit_plan"))
                else:
                    bot.answer_callback_query(call.id, "No plans!")
        
        elif data.startswith("admin_edit_plan_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                plan = db.get_plan(plan_id)
                if plan:
                    text = f"<b>📝 Editing: {plan['name']}</b>\n\n"
                    text += f"💰 Price: ₹{int(plan['price'])}\n"
                    text += f"📅 Validity: {plan['validity_days']} days\n"
                    text += f"🔗 Link: {plan.get('channel_link', 'Not set')}\n"
                    text += f"📎 Media: {len(db.get_plan_media(plan_id))} items"
                    safe_edit(call.message.chat.id, call.message.message_id, text,
                             reply_markup=edit_plan_keyboard(plan_id))
                else:
                    bot.answer_callback_query(call.id, "Plan not found!")
        
        elif data == "admin_delete_plan_list":
            if is_admin(user_id):
                plans = db.get_all_plans()
                if plans:
                    safe_edit(call.message.chat.id, call.message.message_id,
                             "🗑️ Select plan to delete:",
                             reply_markup=plan_list_keyboard("admin_delete_plan"))
                else:
                    bot.answer_callback_query(call.id, "No plans!")
        
        elif data.startswith("admin_delete_plan_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[3])
                db.delete_plan(plan_id)
                bot.answer_callback_query(call.id, "✅ Plan deleted!")
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📋 <b>Plan Management</b>", 
                         reply_markup=admin_plans_keyboard())
        
        # ========== EDIT PLAN FIELDS ==========
        elif data.startswith("edit_name_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[2])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'name'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "✏️ Send new plan name:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}")))
        
        elif data.startswith("edit_price_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[2])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'price'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "💰 Send new price (in ₹):",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}")))
        
        elif data.startswith("edit_validity_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[2])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'validity'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📅 Send new validity (in days):",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}")))
        
        elif data.startswith("edit_link_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[2])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'link'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "🔗 Send channel link:\n\nExample: https://t.me/yourchannel",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}")))
        
        elif data.startswith("edit_media_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[2])
                user_data[user_id] = {'add_media': plan_id, 'media_count': 0}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📎 Send 5 videos or photos for this plan.\n\nSend media one by one:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("✅ Done", callback_data=f"media_done_{plan_id}"),
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"admin_edit_plan_{plan_id}")))
        
        elif data.startswith("media_done_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[2])
                bot.answer_callback_query(call.id, "✅ Media added!")
                plan = db.get_plan(plan_id)
                if plan:
                    text = f"<b>📝 Editing: {plan['name']}</b>\n\n"
                    text += f"💰 Price: ₹{int(plan['price'])}\n"
                    text += f"📅 Validity: {plan['validity_days']} days\n"
                    text += f"📎 Media: {len(db.get_plan_media(plan_id))} items"
                    safe_edit(call.message.chat.id, call.message.message_id, text,
                             reply_markup=edit_plan_keyboard(plan_id))
        
        # ========== ADMIN SETTINGS ==========
        elif data == "admin_welcome_img":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'welcome_image'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "🖼️ Send new welcome image:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")))
        
        elif data == "admin_welcome_video":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'welcome_video'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "🎬 Send new welcome video:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")))
        
        elif data == "admin_welcome_text":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'welcome_text'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📝 Send new welcome text:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")))
        
        elif data == "admin_upi":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'upi_id'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "💰 Send UPI ID:\n\nExample: premium@upi",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")))
        
        elif data == "admin_qr":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'qr_code'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📱 Send QR code image:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")))
        
        elif data == "admin_bot_name":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'bot_name'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "🏷️ Send new bot name:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")))
        
        elif data == "admin_broadcast":
            if is_admin(user_id):
                user_data[user_id] = {'broadcast': True}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📢 <b>Broadcast Message</b>\n\nSend message, photo, video, or document to ALL users:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin_panel")))
        
        # ========== ADMIN PAYMENTS ==========
        elif data == "admin_payments":
            if is_admin(user_id):
                pending = db.get_pending_payments()
                if pending:
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    for p in pending:
                        name = p.get('username') or p.get('first_name', 'Unknown')
                        kb.add(types.InlineKeyboardButton(f"🕐 {name} - ₹{int(p['amount'])}",
                                 callback_data=f"pview_{p['payment_id']}"))
                    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_panel"))
                    safe_edit(call.message.chat.id, call.message.message_id, 
                             f"<b>💳 Pending Payments</b> ({len(pending)})", reply_markup=kb)
                else:
                    safe_edit(call.message.chat.id, call.message.message_id,
                             "✅ No pending payments",
                             reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("🔙 Back", callback_data="admin_panel")))
        
        elif data.startswith("pview_"):
            if is_admin(user_id):
                pid = int(data.split("_")[1])
                payment = db.get_payment(pid)
                if payment:
                    user = db.get_user(payment['user_id'])
                    plan = db.get_plan(payment['plan_id'])
                    text = f"<b>💳 Payment #{pid}</b>\n\n"
                    text += f"👤 User: {user.get('first_name', 'Unknown')}\n"
                    text += f"📋 Plan: {plan['name'] if plan else 'Unknown'}\n"
                    text += f"💰 Amount: ₹{int(payment['amount'])}\n"
                    text += f"📅 Date: {payment['created_at'][:16]}\n"
                    text += f"Status: {payment['status'].title()}"
                    
                    kb = types.InlineKeyboardMarkup(row_width=2)
                    if payment['status'] == 'pending':
                        kb.row(
                            types.InlineKeyboardButton("✅ Approve", callback_data=f"app_{pid}"),
                            types.InlineKeyboardButton("❌ Reject", callback_data=f"rej_{pid}")
                        )
                    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_payments"))
                    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
                    if payment.get('screenshot_file_id'):
                        safe_photo(user_id, payment['screenshot_file_id'], "📱 Payment Screenshot")
        
        elif data.startswith("app_"):
            if is_admin(user_id):
                pid = int(data.split("_")[1])
                db.approve_payment(pid)
                payment = db.get_payment(pid)
                if payment:
                    user = db.get_user(payment['user_id'])
                    plan = db.get_plan(payment['plan_id'])
                    if user and plan:
                        link = plan.get('channel_link', '')
                        text = f"✅ <b>Payment Approved!</b>\n\n"
                        text += f"Your {plan['name']} subscription is active!\n"
                        text += f"Validity: {plan['validity_days']} days\n"
                        if link:
                            text += f"\n🔗 <a href='{link}'>Access Channel</a>"
                        safe_send(payment['user_id'], text, disable_web_page_preview=True)
                bot.answer_callback_query(call.id, "✅ Approved!")
                pending = db.get_pending_payments()
                if pending:
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    for p in pending:
                        name = p.get('username') or p.get('first_name', 'Unknown')
                        kb.add(types.InlineKeyboardButton(f"🕐 {name} - ₹{int(p['amount'])}",
                                 callback_data=f"pview_{p['payment_id']}"))
                    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin_panel"))
                    safe_edit(call.message.chat.id, call.message.message_id, 
                             f"<b>💳 Pending Payments</b> ({len(pending)})", reply_markup=kb)
        
        elif data.startswith("rej_"):
            if is_admin(user_id):
                pid = int(data.split("_")[1])
                user_data[user_id] = {'reject': pid}
                bot.answer_callback_query(call.id, "Send rejection reason")
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "❌ Error!")

# ==================== MESSAGE HANDLERS ====================

@bot.message_handler(content_types=['photo'])
def handle_photo(msg):
    user_id = msg.from_user.id
    file_id = msg.photo[-1].file_id
    
    # Screenshot upload
    if user_id in user_data and 'screenshot_plan' in user_data[user_id]:
        plan_id = user_data[user_id]['screenshot_plan']
        plan = db.get_plan(plan_id)
        if plan:
            pid = db.add_payment(user_id, plan_id, plan['price'], file_id)
            bot.reply_to(msg, "✅ Payment screenshot received!\nAdmin will review shortly.")
            
            payment = db.get_payment(pid)
            if payment:
                user = db.get_user(user_id)
                text = f"<b>💳 New Payment</b>\n\n"
                text += f"User: {user.get('first_name', 'Unknown')}\n"
                text += f"Plan: {plan['name']}\n"
                text += f"Amount: ₹{int(plan['price'])}\n"
                text += f"ID: #{pid}"
                
                kb = types.InlineKeyboardMarkup(row_width=2)
                kb.row(
                    types.InlineKeyboardButton("✅ Approve", callback_data=f"app_{pid}"),
                    types.InlineKeyboardButton("❌ Reject", callback_data=f"rej_{pid}")
                )
                for admin in ADMIN_IDS:
                    try:
                        bot.send_photo(admin, file_id, caption=text, reply_markup=kb)
                    except:
                        pass
            del user_data[user_id]
        return
    
    # Settings - Image
    if user_id in user_data and 'setting' in user_data[user_id]:
        key = user_data[user_id]['setting']
        db.set_setting(key, file_id)
        if key == 'welcome_image':
            global WELCOME_IMAGE
            WELCOME_IMAGE = file_id
        elif key == 'qr_code':
            global QR_CODE
            QR_CODE = file_id
        bot.reply_to(msg, f"✅ {key.replace('_', ' ').title()} updated!")
        del user_data[user_id]
        return
    
    # Add media to plan
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        count = user_data[user_id].get('media_count', 0)
        if count < 5:
            db.add_media(plan_id, 'photo', file_id)
            user_data[user_id]['media_count'] = count + 1
            remaining = 5 - (count + 1)
            if remaining > 0:
                bot.reply_to(msg, f"✅ Photo added! ({count+1}/5)\nSend {remaining} more media or click Done.")
            else:
                bot.reply_to(msg, "✅ All 5 media added! Click Done.")
        else:
            bot.reply_to(msg, "❌ Already 5 media added! Click Done to finish.")
        return
    
    # Broadcast
    if user_id in user_data and user_data[user_id].get('broadcast'):
        users = db.get_all_users()
        sent = 0
        for u in users:
            try:
                bot.send_photo(u['user_id'], file_id, caption=msg.caption or '')
                sent += 1
                time.sleep(0.05)
            except:
                pass
        bot.reply_to(msg, f"✅ Broadcast sent to {sent} users!")
        del user_data[user_id]

@bot.message_handler(content_types=['video'])
def handle_video(msg):
    user_id = msg.from_user.id
    file_id = msg.video.file_id
    
    # Welcome video
    if user_id in user_data and 'setting' in user_data[user_id]:
        key = user_data[user_id]['setting']
        if key == 'welcome_video':
            db.set_setting('welcome_video', file_id)
            global WELCOME_VIDEO
            WELCOME_VIDEO = file_id
            bot.reply_to(msg, "✅ Welcome video updated!")
            del user_data[user_id]
        return
    
    # Add media to plan
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        count = user_data[user_id].get('media_count', 0)
        if count < 5:
            db.add_media(plan_id, 'video', file_id)
            user_data[user_id]['media_count'] = count + 1
            remaining = 5 - (count + 1)
            if remaining > 0:
                bot.reply_to(msg, f"✅ Video added! ({count+1}/5)\nSend {remaining} more media or click Done.")
            else:
                bot.reply_to(msg, "✅ All 5 media added! Click Done.")
        else:
            bot.reply_to(msg, "❌ Already 5 media added! Click Done to finish.")
        return
    
    # Broadcast
    if user_id in user_data and user_data[user_id].get('broadcast'):
        users = db.get_all_users()
        sent = 0
        for u in users:
            try:
                bot.send_video(u['user_id'], file_id, caption=msg.caption or '')
                sent += 1
                time.sleep(0.05)
            except:
                pass
        bot.reply_to(msg, f"✅ Broadcast sent to {sent} users!")
        del user_data[user_id]

@bot.message_handler(content_types=['document', 'audio', 'voice', 'animation', 'sticker'])
def handle_other_media(msg):
    user_id = msg.from_user.id
    
    # Broadcast
    if user_id in user_data and user_data[user_id].get('broadcast'):
        users = db.get_all_users()
        sent = 0
        for u in users:
            try:
                if msg.content_type == 'document':
                    bot.send_document(u['user_id'], msg.document.file_id, caption=msg.caption or '')
                elif msg.content_type == 'audio':
                    bot.send_audio(u['user_id'], msg.audio.file_id, caption=msg.caption or '')
                elif msg.content_type == 'voice':
                    bot.send_voice(u['user_id'], msg.voice.file_id)
                elif msg.content_type == 'animation':
                    bot.send_animation(u['user_id'], msg.animation.file_id, caption=msg.caption or '')
                elif msg.content_type == 'sticker':
                    bot.send_sticker(u['user_id'], msg.sticker.file_id)
                sent += 1
                time.sleep(0.05)
            except:
                pass
        bot.reply_to(msg, f"✅ Broadcast sent to {sent} users!")
        del user_data[user_id]

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(msg):
    user_id = msg.from_user.id
    
    # ===== REJECT PAYMENT REASON =====
    if user_id in user_data and 'reject' in user_data[user_id]:
        pid = user_data[user_id]['reject']
        reason = msg.text
        db.reject_payment(pid)
        payment = db.get_payment(pid)
        if payment:
            user = db.get_user(payment['user_id'])
            plan = db.get_plan(payment['plan_id'])
            if user and plan:
                text = f"❌ <b>Payment Rejected</b>\n\n"
                text += f"Plan: {plan['name']}\n"
                text += f"Reason: {reason}\n\n"
                text += "Please try again with correct payment."
                safe_send(payment['user_id'], text)
        bot.reply_to(msg, "✅ Payment rejected and user notified!")
        del user_data[user_id]
        return
    
    # ===== ADD PLAN =====
    if user_id in user_data and user_data[user_id].get('add_plan'):
        step = user_data[user_id].get('step')
        
        if step == 'name':
            user_data[user_id]['pname'] = msg.text
            user_data[user_id]['step'] = 'price'
            bot.reply_to(msg, "Step 2/5: Enter price (in ₹):")
        
        elif step == 'price':
            try:
                user_data[user_id]['pprice'] = float(msg.text)
                user_data[user_id]['step'] = 'validity'
                bot.reply_to(msg, "Step 3/5: Enter validity (in days):")
            except:
                bot.reply_to(msg, "❌ Invalid price! Enter number:")
        
        elif step == 'validity':
            try:
                user_data[user_id]['pvalidity'] = int(msg.text)
                user_data[user_id]['step'] = 'link'
                bot.reply_to(msg, "Step 4/5: Enter channel link:\n\nExample: https://t.me/yourchannel")
            except:
                bot.reply_to(msg, "❌ Invalid days! Enter number:")
        
        elif step == 'link':
            user_data[user_id]['plink'] = msg.text
            user_data[user_id]['step'] = 'done'
            bot.reply_to(msg, "✅ Plan created!\n\nNow send 5 videos/photos for this plan.\nSend media one by one.")
            
            # Create plan
            plan_id = db.add_plan(
                user_data[user_id]['pname'],
                user_data[user_id]['pprice'],
                user_data[user_id]['pvalidity'],
                user_data[user_id]['plink']
            )
            user_data[user_id]['add_media'] = plan_id
            user_data[user_id]['media_count'] = 0
            del user_data[user_id]['add_plan']
            del user_data[user_id]['step']
        
        return
    
    # ===== EDIT PLAN =====
    if user_id in user_data and 'edit_plan' in user_data[user_id]:
        plan_id = user_data[user_id]['edit_plan']
        field = user_data[user_id]['field']
        
        if field == 'name':
            db.update_plan(plan_id, name=msg.text)
            bot.reply_to(msg, f"✅ Plan name updated to: {msg.text}")
        elif field == 'price':
            try:
                db.update_plan(plan_id, price=float(msg.text))
                bot.reply_to(msg, f"✅ Price updated to: ₹{msg.text}")
            except:
                bot.reply_to(msg, "❌ Invalid price!")
        elif field == 'validity':
            try:
                db.update_plan(plan_id, validity_days=int(msg.text))
                bot.reply_to(msg, f"✅ Validity updated to: {msg.text} days")
            except:
                bot.reply_to(msg, "❌ Invalid days!")
        elif field == 'link':
            db.update_plan(plan_id, channel_link=msg.text)
            bot.reply_to(msg, f"✅ Channel link updated!")
        
        del user_data[user_id]
        return
    
    # ===== SETTINGS =====
    if user_id in user_data and 'setting' in user_data[user_id]:
        key = user_data[user_id]['setting']
        
        if key == 'welcome_text':
            db.set_setting('welcome_text', msg.text)
            global WELCOME_TEXT
            WELCOME_TEXT = msg.text
            bot.reply_to(msg, "✅ Welcome text updated!")
        elif key == 'upi_id':
            db.set_setting('upi_id', msg.text)
            global UPI_ID
            UPI_ID = msg.text
            bot.reply_to(msg, "✅ UPI ID updated!")
        elif key == 'bot_name':
            db.set_setting('bot_name', msg.text)
            global BOT_NAME
            BOT_NAME = msg.text
            bot.reply_to(msg, f"✅ Bot name updated to: {msg.text}")
        
        del user_data[user_id]
        return
    
    # ===== BROADCAST =====
    if user_id in user_data and user_data[user_id].get('broadcast'):
        users = db.get_all_users()
        sent = 0
        for u in users:
            try:
                bot.send_message(u['user_id'], msg.text)
                sent += 1
                time.sleep(0.05)
            except:
                pass
        bot.reply_to(msg, f"✅ Broadcast sent to {sent} users!")
        del user_data[user_id]

# ==================== MAIN ====================
def run_bot():
    while bot_running:
        try:
            logger.info("🤖 Bot polling started...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            if bot_running:
                time.sleep(5)

def main():
    logger.info("🚀 Starting Premium Bot...")
    try:
        bot.get_me()
        logger.info("✅ Bot connected")
        
        http_thread = threading.Thread(target=run_http, daemon=True)
        http_thread.start()
        logger.info(f"🌐 HTTP: http://0.0.0.0:{PORT}")
        
        run_bot()
    except KeyboardInterrupt:
        logger.info("🛑 Stopping...")
        global bot_running
        bot_running = False
    except Exception as e:
        logger.error(f"Fatal: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()