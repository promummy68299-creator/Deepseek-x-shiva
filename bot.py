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

# ==================== CONFIG (Direct Set) ====================
BOT_TOKEN = "8887238154:AAHJj8vnrAuikrMisnSKBgKQxdmZUeWa3C4"  # <--- Yahan apna token daalo
ADMIN_IDS = []  # Auto-detect ho jayega
DATABASE_PATH = 'bot_database.db'
PORT = 8080

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("ERROR: BOT_TOKEN set karo!")
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
            description TEXT,
            price REAL NOT NULL,
            validity_days INTEGER NOT NULL,
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
            ('welcome_text', 'Welcome to Premium Bot! 🎉\n\nChoose a plan to get started.'),
            ('welcome_image', ''),
            ('qr_code', ''),
            ('upi_id', ''),
            ('delivery_link', '')
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
    
    def add_plan(self, name, desc, price, days):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('INSERT INTO plans (name, description, price, validity_days) VALUES (?, ?, ?, ?)',
                 (name, desc, price, days))
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
        allowed = ['name', 'description', 'price', 'validity_days', 'media_json']
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
    
    def delete_media(self, plan_id, index):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT media_json FROM plans WHERE plan_id = ?', (plan_id,))
        row = c.fetchone()
        if row:
            media_list = json.loads(row[0]) if row[0] else []
            if 0 <= index < len(media_list):
                del media_list[index]
                c.execute('UPDATE plans SET media_json = ? WHERE plan_id = ?', (json.dumps(media_list), plan_id))
                conn.commit()
        conn.close()
    
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
    
    def backup(self):
        conn = self.get_conn()
        with open('backup.db', 'w') as f:
            for line in conn.iterdump():
                f.write(f'{line}\n')
        conn.close()
        with open('backup.db', 'rb') as f:
            data = f.read()
        os.remove('backup.db')
        return data
    
    def restore(self, data):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute('SELECT name FROM sqlite_master WHERE type="table"')
        for table in c.fetchall():
            c.execute(f'DROP TABLE IF EXISTS {table[0]}')
        conn.commit()
        conn.close()
        conn = self.get_conn()
        c = conn.cursor()
        c.executescript(data.decode('utf-8'))
        conn.commit()
        conn.close()

# ==================== BOT ====================
db = Database()
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# Load settings
WELCOME_IMAGE = db.get_setting('welcome_image')
WELCOME_TEXT = db.get_setting('welcome_text')
QR_CODE = db.get_setting('qr_code')
UPI_ID = db.get_setting('upi_id')
DELIVERY_LINK = db.get_setting('delivery_link')

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

def format_plan(p):
    text = f"<b>{p['name']}</b>\n\n{p['description']}\n\n"
    text += f"💰 Price: <b>₹{p['price']}</b>\n"
    text += f"📅 Validity: <b>{p['validity_days']} days</b>\n\n"
    media = json.loads(p['media_json']) if p['media_json'] else []
    if media:
        text += "📎 Content:\n"
        counts = {}
        for m in media:
            counts[m['type']] = counts.get(m['type'], 0) + 1
        for t, c in counts.items():
            text += f"  • {c} {t.title()}\n"
    return text

def main_keyboard(user_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📋 Plans", callback_data="plans"),
        types.InlineKeyboardButton("ℹ️ My Sub", callback_data="sub")
    )
    if is_admin(user_id):
        kb.add(types.InlineKeyboardButton("⚙️ Admin", callback_data="admin"))
    return kb

def plans_keyboard(plans):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for p in plans:
        kb.add(types.InlineKeyboardButton(f"{p['name']} - ₹{p['price']}", 
                 callback_data=f"view_{p['plan_id']}"))
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back"))
    return kb

def plan_detail_kb(plan_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛒 Buy", callback_data=f"buy_{plan_id}"))
    plan = db.get_plan(plan_id)
    if plan:
        media = json.loads(plan['media_json']) if plan['media_json'] else []
        if media:
            kb.add(types.InlineKeyboardButton("📺 Preview", callback_data=f"preview_{plan_id}"))
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="plans"))
    return kb

def payment_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("💳 QR", callback_data="qr"),
        types.InlineKeyboardButton("💰 UPI", callback_data="upi"),
        types.InlineKeyboardButton("🔙 Cancel", callback_data="plans")
    )
    return kb

def admin_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("📊 Stats", callback_data="stats"),
        types.InlineKeyboardButton("👥 Users", callback_data="users")
    )
    kb.row(
        types.InlineKeyboardButton("📋 Plans", callback_data="a_plans"),
        types.InlineKeyboardButton("💳 Payments", callback_data="a_payments")
    )
    kb.row(
        types.InlineKeyboardButton("⚙️ Settings", callback_data="a_settings"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")
    )
    kb.row(
        types.InlineKeyboardButton("💾 Backup", callback_data="backup"),
        types.InlineKeyboardButton("📥 Restore", callback_data="restore")
    )
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back"))
    return kb

def plan_mgmt_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    plans = db.get_all_plans()
    for p in plans:
        kb.add(types.InlineKeyboardButton(f"📝 {p['name']}", callback_data=f"edit_{p['plan_id']}"))
    kb.row(
        types.InlineKeyboardButton("➕ Add", callback_data="add_plan"),
        types.InlineKeyboardButton("🗑️ Delete", callback_data="del_plan")
    )
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin"))
    return kb

def settings_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🖼️ Welcome Image", callback_data="set_img"),
        types.InlineKeyboardButton("📝 Welcome Text", callback_data="set_text"),
        types.InlineKeyboardButton("📱 QR Code", callback_data="set_qr"),
        types.InlineKeyboardButton("💰 UPI ID", callback_data="set_upi"),
        types.InlineKeyboardButton("🔗 Delivery Link", callback_data="set_link")
    )
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin"))
    return kb

# ==================== COMMANDS ====================
@bot.message_handler(commands=['start'])
def start_cmd(msg):
    user_id = msg.from_user.id
    db.add_user(user_id, msg.from_user.username or '', msg.from_user.first_name or '', msg.from_user.last_name or '')
    
    # Auto admin - first user becomes admin
    if not ADMIN_IDS:
        ADMIN_IDS.append(user_id)
        db.set_admin(user_id)
        bot.send_message(user_id, "✅ You are the ADMIN!")
    
    if user_id in ADMIN_IDS:
        db.set_admin(user_id)
    
    if WELCOME_IMAGE:
        safe_photo(user_id, WELCOME_IMAGE, WELCOME_TEXT, reply_markup=main_keyboard(user_id))
    else:
        safe_send(user_id, WELCOME_TEXT, reply_markup=main_keyboard(user_id))

# ==================== CALLBACKS ====================
@bot.callback_query_handler(func=lambda c: True)
def handle_cb(call):
    user_id = call.from_user.id
    data = call.data
    
    try:
        # Back
        if data == "back":
            if WELCOME_IMAGE:
                safe_edit(call.message.chat.id, call.message.message_id, WELCOME_TEXT, 
                         reply_markup=main_keyboard(user_id))
                safe_photo(user_id, WELCOME_IMAGE, WELCOME_TEXT, reply_markup=main_keyboard(user_id))
            else:
                safe_edit(call.message.chat.id, call.message.message_id, WELCOME_TEXT, 
                         reply_markup=main_keyboard(user_id))
        
        # Plans
        elif data == "plans":
            plans = db.get_all_plans()
            if not plans:
                safe_edit(call.message.chat.id, call.message.message_id, "❌ No plans!",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Back", callback_data="back")))
                return
            text = "📋 <b>Plans</b>\n\n"
            for p in plans:
                text += f"<b>{p['name']}</b> - ₹{p['price']} ({p['validity_days']} days)\n"
            safe_edit(call.message.chat.id, call.message.message_id, text, 
                     reply_markup=plans_keyboard(plans))
        
        # View Plan
        elif data.startswith("view_"):
            plan_id = int(data.split("_")[1])
            plan = db.get_plan(plan_id)
            if plan:
                safe_edit(call.message.chat.id, call.message.message_id, format_plan(plan),
                         reply_markup=plan_detail_kb(plan_id))
            else:
                bot.answer_callback_query(call.id, "Not found!")
        
        # Preview
        elif data.startswith("preview_"):
            plan_id = int(data.split("_")[1])
            plan = db.get_plan(plan_id)
            if plan:
                media = json.loads(plan['media_json']) if plan['media_json'] else []
                sent = 0
                for m in media[:5]:
                    try:
                        if m['type'] == 'photo':
                            bot.send_photo(user_id, m['file_id'], caption=f"Preview: {plan['name']}")
                        elif m['type'] == 'video':
                            bot.send_video(user_id, m['file_id'], caption=f"Preview: {plan['name']}")
                        elif m['type'] == 'document':
                            bot.send_document(user_id, m['file_id'], caption=f"Preview: {plan['name']}")
                        elif m['type'] == 'audio':
                            bot.send_audio(user_id, m['file_id'], caption=f"Preview: {plan['name']}")
                        elif m['type'] == 'voice':
                            bot.send_voice(user_id, m['file_id'])
                        elif m['type'] == 'animation':
                            bot.send_animation(user_id, m['file_id'])
                        elif m['type'] == 'sticker':
                            bot.send_sticker(user_id, m['file_id'])
                        sent += 1
                    except:
                        pass
                bot.answer_callback_query(call.id, f"Showing {sent} items")
        
        # Buy
        elif data.startswith("buy_"):
            plan_id = int(data.split("_")[1])
            plan = db.get_plan(plan_id)
            if plan:
                user_data[user_id] = {'plan': plan_id}
                text = f"🛒 <b>Buy {plan['name']}</b>\n\n💰 Amount: ₹{plan['price']}\n📅 {plan['validity_days']} days\n\nChoose payment:"
                safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=payment_kb())
            else:
                bot.answer_callback_query(call.id, "Not found!")
        
        # QR Payment
        elif data == "qr":
            plan_id = user_data.get(user_id, {}).get('plan')
            if plan_id:
                plan = db.get_plan(plan_id)
                qr = db.get_setting('qr_code') or QR_CODE
                if qr and plan:
                    text = f"📱 Pay with QR\nPlan: {plan['name']}\nAmount: ₹{plan['price']}\n\nSend screenshot after payment:"
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    kb.add(types.InlineKeyboardButton("📤 Upload Screenshot", callback_data=f"upload_{plan_id}"))
                    kb.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="plans"))
                    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
                    safe_photo(user_id, qr, "📱 Scan to pay")
                else:
                    bot.answer_callback_query(call.id, "QR not configured!")
        
        # UPI Payment
        elif data == "upi":
            plan_id = user_data.get(user_id, {}).get('plan')
            if plan_id:
                plan = db.get_plan(plan_id)
                upi = db.get_setting('upi_id') or UPI_ID
                if upi and plan:
                    text = f"💰 Pay with UPI\nPlan: {plan['name']}\nAmount: ₹{plan['price']}\n\nSend to: <b>{upi}</b>\n\nSend screenshot after payment:"
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    kb.add(types.InlineKeyboardButton("📤 Upload Screenshot", callback_data=f"upload_{plan_id}"))
                    kb.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="plans"))
                    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
                else:
                    bot.answer_callback_query(call.id, "UPI not configured!")
        
        # Upload Screenshot
        elif data.startswith("upload_"):
            plan_id = int(data.split("_")[1])
            plan = db.get_plan(plan_id)
            if plan:
                user_data[user_id] = {'screenshot': plan_id}
                text = f"📤 Send payment screenshot for:\n\nPlan: {plan['name']}\nAmount: ₹{plan['price']}"
                kb = types.InlineKeyboardMarkup(row_width=1)
                kb.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="plans"))
                safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
        
        # Subscription
        elif data == "sub":
            user = db.get_user(user_id)
            if user and user.get('subscription_plan_id'):
                plan = db.get_plan(user['subscription_plan_id'])
                expiry = user.get('subscription_expiry', '')
                if plan:
                    text = f"ℹ️ <b>Your Subscription</b>\n\n📋 {plan['name']}\n📅 Expires: {expiry[:10] if expiry else 'N/A'}\n"
                    if expiry and datetime.fromisoformat(expiry) > datetime.now():
                        text += "✅ Active\n\n"
                        if DELIVERY_LINK:
                            text += f"🔗 <a href='{DELIVERY_LINK}'>Access</a>"
                    else:
                        text += "❌ Expired\n\nRenew now!"
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    if not (expiry and datetime.fromisoformat(expiry) > datetime.now()):
                        kb.add(types.InlineKeyboardButton("🔄 Renew", callback_data="plans"))
                    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back"))
                    safe_edit(call.message.chat.id, call.message.message_id, text, 
                             reply_markup=kb, disable_web_page_preview=True)
                    return
            text = "ℹ️ No active subscription.\nChoose a plan to get started!"
            safe_edit(call.message.chat.id, call.message.message_id, text,
                     reply_markup=types.InlineKeyboardMarkup().add(
                     types.InlineKeyboardButton("📋 Plans", callback_data="plans"),
                     types.InlineKeyboardButton("🔙 Back", callback_data="back")))
        
        # ============ ADMIN ============
        elif data == "admin":
            if is_admin(user_id):
                safe_edit(call.message.chat.id, call.message.message_id, "⚙️ <b>Admin Panel</b>", 
                         reply_markup=admin_kb())
            else:
                bot.answer_callback_query(call.id, "Unauthorized!")
        
        elif data == "stats":
            if is_admin(user_id):
                s = db.get_stats()
                text = f"📊 <b>Stats</b>\n\n👥 Users: {s['users']}\n📋 Plans: {s['plans']}\n🕐 Pending: {s['pending']}\n✅ Approved: {s['approved']}\n❌ Rejected: {s['rejected']}"
                safe_edit(call.message.chat.id, call.message.message_id, text,
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Back", callback_data="admin")))
        
        elif data == "users":
            if is_admin(user_id):
                users = db.get_all_users()
                text = f"👥 <b>Users</b> ({len(users)})\n\n"
                for u in users[:15]:
                    name = u.get('first_name', 'Unknown')
                    uname = u.get('username', '')
                    text += f"👤 {name}" + (f" @{uname}" if uname else "") + "\n"
                if len(users) > 15:
                    text += f"\n... and {len(users)-15} more"
                safe_edit(call.message.chat.id, call.message.message_id, text,
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Back", callback_data="admin")))
        
        elif data == "a_plans":
            if is_admin(user_id):
                safe_edit(call.message.chat.id, call.message.message_id, "📋 <b>Plan Management</b>",
                         reply_markup=plan_mgmt_kb())
        
        elif data.startswith("edit_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                plan = db.get_plan(plan_id)
                if plan:
                    text = f"📝 <b>Edit: {plan['name']}</b>\n\n{format_plan(plan)}"
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    kb.add(
                        types.InlineKeyboardButton("✏️ Name", callback_data=f"ename_{plan_id}"),
                        types.InlineKeyboardButton("✏️ Description", callback_data=f"edesc_{plan_id}"),
                        types.InlineKeyboardButton("💰 Price", callback_data=f"eprice_{plan_id}"),
                        types.InlineKeyboardButton("📅 Validity", callback_data=f"edays_{plan_id}"),
                        types.InlineKeyboardButton("📎 Add Media", callback_data=f"addmedia_{plan_id}"),
                        types.InlineKeyboardButton("🗑️ Delete Media", callback_data=f"delmedia_{plan_id}"),
                        types.InlineKeyboardButton("🔙 Back", callback_data="a_plans")
                    )
                    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
                else:
                    bot.answer_callback_query(call.id, "Not found!")
        
        elif data == "add_plan":
            if is_admin(user_id):
                user_data[user_id] = {'add_plan': True, 'step': 'name'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "➕ <b>Add Plan</b>\n\nStep 1/4: Enter name:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="a_plans")))
        
        elif data == "del_plan":
            if is_admin(user_id):
                plans = db.get_all_plans()
                if plans:
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    for p in plans:
                        kb.add(types.InlineKeyboardButton(f"❌ {p['name']}", 
                                 callback_data=f"dconfirm_{p['plan_id']}"))
                    kb.add(types.InlineKeyboardButton("🔙 Cancel", callback_data="a_plans"))
                    safe_edit(call.message.chat.id, call.message.message_id, "🗑️ Select plan to delete:",
                             reply_markup=kb)
                else:
                    bot.answer_callback_query(call.id, "No plans!")
        
        elif data.startswith("dconfirm_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                plan = db.get_plan(plan_id)
                if plan:
                    kb = types.InlineKeyboardMarkup(row_width=2)
                    kb.row(
                        types.InlineKeyboardButton("✅ Yes", callback_data=f"deld_{plan_id}"),
                        types.InlineKeyboardButton("❌ No", callback_data="a_plans")
                    )
                    safe_edit(call.message.chat.id, call.message.message_id,
                             f"🗑️ Delete <b>{plan['name']}</b>?\n⚠️ This cannot be undone!",
                             reply_markup=kb)
        
        elif data.startswith("deld_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                db.delete_plan(plan_id)
                bot.answer_callback_query(call.id, "Deleted!")
                safe_edit(call.message.chat.id, call.message.message_id, "📋 <b>Plan Management</b>",
                         reply_markup=plan_mgmt_kb())
        
        # Edit Plan Fields
        elif data.startswith("ename_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'name'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "✏️ Send new name:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"edit_{plan_id}")))
        
        elif data.startswith("edesc_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'desc'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "✏️ Send new description:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"edit_{plan_id}")))
        
        elif data.startswith("eprice_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'price'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "💰 Send new price (in ₹):",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"edit_{plan_id}")))
        
        elif data.startswith("edays_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                user_data[user_id] = {'edit_plan': plan_id, 'field': 'days'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📅 Send new validity (days):",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"edit_{plan_id}")))
        
        elif data.startswith("addmedia_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                user_data[user_id] = {'add_media': plan_id}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📎 Send any media (photo/video/document/audio/voice/animation/sticker):",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data=f"edit_{plan_id}")))
        
        elif data.startswith("delmedia_"):
            if is_admin(user_id):
                plan_id = int(data.split("_")[1])
                plan = db.get_plan(plan_id)
                if plan:
                    media = json.loads(plan['media_json']) if plan['media_json'] else []
                    if media:
                        kb = types.InlineKeyboardMarkup(row_width=1)
                        for i, m in enumerate(media):
                            kb.add(types.InlineKeyboardButton(f"❌ {m['type'].title()}", 
                                     callback_data=f"dmconfirm_{plan_id}_{i}"))
                        kb.add(types.InlineKeyboardButton("🔙 Cancel", callback_data=f"edit_{plan_id}"))
                        safe_edit(call.message.chat.id, call.message.message_id, "🗑️ Select media to delete:",
                                 reply_markup=kb)
                    else:
                        bot.answer_callback_query(call.id, "No media!")
        
        elif data.startswith("dmconfirm_"):
            if is_admin(user_id):
                parts = data.split("_")
                plan_id = int(parts[1])
                idx = int(parts[2])
                db.delete_media(plan_id, idx)
                bot.answer_callback_query(call.id, "Media deleted!")
                plan = db.get_plan(plan_id)
                if plan:
                    text = f"📝 <b>Edit: {plan['name']}</b>\n\n{format_plan(plan)}"
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    kb.add(
                        types.InlineKeyboardButton("✏️ Name", callback_data=f"ename_{plan_id}"),
                        types.InlineKeyboardButton("✏️ Description", callback_data=f"edesc_{plan_id}"),
                        types.InlineKeyboardButton("💰 Price", callback_data=f"eprice_{plan_id}"),
                        types.InlineKeyboardButton("📅 Validity", callback_data=f"edays_{plan_id}"),
                        types.InlineKeyboardButton("📎 Add Media", callback_data=f"addmedia_{plan_id}"),
                        types.InlineKeyboardButton("🗑️ Delete Media", callback_data=f"delmedia_{plan_id}"),
                        types.InlineKeyboardButton("🔙 Back", callback_data="a_plans")
                    )
                    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
        
        # Payments
        elif data == "a_payments":
            if is_admin(user_id):
                pending = db.get_pending_payments()
                if pending:
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    for p in pending:
                        name = p.get('username') or p.get('first_name', 'Unknown')
                        kb.add(types.InlineKeyboardButton(f"🕐 {name} - ₹{p['amount']}",
                                 callback_data=f"pview_{p['payment_id']}"))
                    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin"))
                    safe_edit(call.message.chat.id, call.message.message_id, 
                             f"💳 Pending: {len(pending)}", reply_markup=kb)
                else:
                    safe_edit(call.message.chat.id, call.message.message_id,
                             "✅ No pending payments",
                             reply_markup=types.InlineKeyboardMarkup().add(
                             types.InlineKeyboardButton("🔙 Back", callback_data="admin")))
        
        elif data.startswith("pview_"):
            if is_admin(user_id):
                pid = int(data.split("_")[1])
                payment = db.get_payment(pid)
                if payment:
                    user = db.get_user(payment['user_id'])
                    plan = db.get_plan(payment['plan_id'])
                    text = f"💳 Payment #{pid}\nUser: {user.get('first_name', 'Unknown')}\nPlan: {plan['name'] if plan else 'Unknown'}\nAmount: ₹{payment['amount']}\nStatus: {payment['status']}"
                    kb = types.InlineKeyboardMarkup(row_width=2)
                    if payment['status'] == 'pending':
                        kb.row(
                            types.InlineKeyboardButton("✅ Approve", callback_data=f"app_{pid}"),
                            types.InlineKeyboardButton("❌ Reject", callback_data=f"rej_{pid}")
                        )
                    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="a_payments"))
                    safe_edit(call.message.chat.id, call.message.message_id, text, reply_markup=kb)
                    if payment.get('screenshot_file_id'):
                        safe_photo(user_id, payment['screenshot_file_id'], "📱 Screenshot")
        
        elif data.startswith("app_"):
            if is_admin(user_id):
                pid = int(data.split("_")[1])
                db.approve_payment(pid)
                payment = db.get_payment(pid)
                if payment:
                    user = db.get_user(payment['user_id'])
                    plan = db.get_plan(payment['plan_id'])
                    if user and plan:
                        link = db.get_setting('delivery_link') or DELIVERY_LINK
                        text = f"✅ Payment Approved!\n\nYour {plan['name']} subscription is active."
                        if link:
                            text += f"\n\n🔗 <a href='{link}'>Access Content</a>"
                        safe_send(payment['user_id'], text, disable_web_page_preview=True)
                bot.answer_callback_query(call.id, "Approved!")
                pending = db.get_pending_payments()
                if pending:
                    kb = types.InlineKeyboardMarkup(row_width=1)
                    for p in pending:
                        name = p.get('username') or p.get('first_name', 'Unknown')
                        kb.add(types.InlineKeyboardButton(f"🕐 {name} - ₹{p['amount']}",
                                 callback_data=f"pview_{p['payment_id']}"))
                    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="admin"))
                    safe_edit(call.message.chat.id, call.message.message_id, 
                             f"💳 Pending: {len(pending)}", reply_markup=kb)
        
        elif data.startswith("rej_"):
            if is_admin(user_id):
                pid = int(data.split("_")[1])
                user_data[user_id] = {'reject': pid}
                bot.answer_callback_query(call.id, "Send rejection reason")
        
        # Settings
        elif data == "a_settings":
            if is_admin(user_id):
                text = f"⚙️ <b>Settings</b>\n\n🖼️ Image: {'✅' if WELCOME_IMAGE else '❌'}\n📝 Text: {len(WELCOME_TEXT)} chars\n📱 QR: {'✅' if QR_CODE else '❌'}\n💰 UPI: {UPI_ID if UPI_ID else '❌'}\n🔗 Link: {DELIVERY_LINK if DELIVERY_LINK else '❌'}"
                safe_edit(call.message.chat.id, call.message.message_id, text,
                         reply_markup=settings_kb())
        
        elif data == "set_img":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'welcome_image'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "🖼️ Send new welcome image:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="a_settings")))
        
        elif data == "set_text":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'welcome_text'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📝 Send new welcome text:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="a_settings")))
        
        elif data == "set_qr":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'qr_code'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📱 Send new QR image:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="a_settings")))
        
        elif data == "set_upi":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'upi_id'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "💰 Send new UPI ID:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="a_settings")))
        
        elif data == "set_link":
            if is_admin(user_id):
                user_data[user_id] = {'setting': 'delivery_link'}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "🔗 Send new delivery link:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="a_settings")))
        
        # Broadcast
        elif data == "broadcast":
            if is_admin(user_id):
                user_data[user_id] = {'broadcast': True}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📢 Send message/photo/video to ALL users:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin")))
        
        # Backup
        elif data == "backup":
            if is_admin(user_id):
                try:
                    data = db.backup()
                    bot.send_document(user_id, ('backup.db', data), caption="💾 Database Backup")
                    bot.answer_callback_query(call.id, "Backup done!")
                except:
                    bot.answer_callback_query(call.id, "Backup failed!")
        
        # Restore
        elif data == "restore":
            if is_admin(user_id):
                user_data[user_id] = {'restore': True}
                safe_edit(call.message.chat.id, call.message.message_id,
                         "📥 Send backup file as document:",
                         reply_markup=types.InlineKeyboardMarkup().add(
                         types.InlineKeyboardButton("🔙 Cancel", callback_data="admin")))
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "Error!")

# ==================== MESSAGE HANDLERS ====================
@bot.message_handler(content_types=['photo'])
def handle_photo(msg):
    user_id = msg.from_user.id
    file_id = msg.photo[-1].file_id
    
    # Screenshot upload
    if user_id in user_data and 'screenshot' in user_data[user_id]:
        plan_id = user_data[user_id]['screenshot']
        plan = db.get_plan(plan_id)
        if plan:
            pid = db.add_payment(user_id, plan_id, plan['price'], file_id)
            bot.reply_to(msg, "✅ Screenshot received! Admin will review.")
            payment = db.get_payment(pid)
            if payment:
                user = db.get_user(user_id)
                text = f"💳 New Payment\nUser: {user.get('first_name', 'Unknown')}\nPlan: {plan['name']}\nAmount: ₹{plan['price']}"
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

@bot.message_handler(content_types=['document'])
def handle_doc(msg):
    user_id = msg.from_user.id
    
    # Restore
    if user_id in user_data and user_data[user_id].get('restore'):
        try:
            file_info = bot.get_file(msg.document.file_id)
            data = bot.download_file(file_info.file_path)
            db.restore(data)
            bot.reply_to(msg, "✅ Database restored!")
            del user_data[user_id]
        except:
            bot.reply_to(msg, "❌ Restore failed!")
        return
    
    # Add media to plan - Document
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        db.add_media(plan_id, 'document', msg.document.file_id)
        bot.reply_to(msg, "✅ Document added!")
        del user_data[user_id]

@bot.message_handler(content_types=['video'])
def handle_video(msg):
    user_id = msg.from_user.id
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        db.add_media(plan_id, 'video', msg.video.file_id)
        bot.reply_to(msg, "✅ Video added!")
        del user_data[user_id]

@bot.message_handler(content_types=['audio'])
def handle_audio(msg):
    user_id = msg.from_user.id
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        db.add_media(plan_id, 'audio', msg.audio.file_id)
        bot.reply_to(msg, "✅ Audio added!")
        del user_data[user_id]

@bot.message_handler(content_types=['voice'])
def handle_voice(msg):
    user_id = msg.from_user.id
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        db.add_media(plan_id, 'voice', msg.voice.file_id)
        bot.reply_to(msg, "✅ Voice added!")
        del user_data[user_id]

@bot.message_handler(content_types=['animation'])
def handle_animation(msg):
    user_id = msg.from_user.id
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        db.add_media(plan_id, 'animation', msg.animation.file_id)
        bot.reply_to(msg, "✅ Animation added!")
        del user_data[user_id]

@bot.message_handler(content_types=['sticker'])
def handle_sticker(msg):
    user_id = msg.from_user.id
    if user_id in user_data and 'add_media' in user_data[user_id]:
        plan_id = user_data[user_id]['add_media']
        db.add_media(plan_id, 'sticker', msg.sticker.file_id)
        bot.reply_to(msg, "✅ Sticker added!")
        del user_data[user_id]

@bot.message_handler(func=lambda m: True, content_types=['text'])
def handle_text(msg):
    user_id = msg.from_user.id
    
    # Reject payment reason
    if user_id in user_data and 'reject' in user_data[user_id]:
        pid = user_data[user_id]['reject']
        db.reject_payment(pid)
        payment = db.get_payment(pid)
        if payment:
            user = db.get_user(payment['user_id'])
            plan = db.get_plan(payment['plan_id'])
            if user and plan:
                safe_send(payment['user_id'], f"❌ Payment Rejected\nReason: {msg.text}\nPlease try again.")
        bot.reply_to(msg, "✅ Payment rejected!")
        del user_data[user_id]
        return
    
    # Add Plan
    if user_id in user_data and user_data[user_id].get('add_plan'):
        step = user_data[user_id].get('step')
        if step == 'name':
            user_data[user_id]['pname'] = msg.text
            user_data[user_id]['step'] = 'desc'
            bot.reply_to(msg, "Step 2/4: Enter description:")
        elif step == 'desc':
            user_data[user_id]['pdesc'] = msg.text
            user_data[user_id]['step'] = 'price'
            bot.reply_to(msg, "Step 3/4: Enter price (in ₹):")
        elif step == 'price':
            try:
                user_data[user_id]['pprice'] = float(msg.text)
                user_data[user_id]['step'] = 'days'
                bot.reply_to(msg, "Step 4/4: Enter validity (days):")
            except:
                bot.reply_to(msg, "❌ Invalid price!")
        elif step == 'days':
            try:
                days = int(msg.text)
                plan_id = db.add_plan(
                    user_data[user_id]['pname'],
                    user_data[user_id]['pdesc'],
                    user_data[user_id]['pprice'],
                    days
                )
                bot.reply_to(msg, f"✅ Plan created! ID: {plan_id}")
                del user_data[user_id]
            except:
                bot.reply_to(msg, "❌ Invalid days!")
        return
    
    # Edit Plan
    if user_id in user_data and 'edit_plan' in user_data[user_id]:
        plan_id = user_data[user_id]['edit_plan']
        field = user_data[user_id]['field']
        if field == 'name':
            db.update_plan(plan_id, name=msg.text)
            bot.reply_to(msg, f"✅ Name: {msg.text}")
        elif field == 'desc':
            db.update_plan(plan_id, description=msg.text)
            bot.reply_to(msg, "✅ Description updated!")
        elif field == 'price':
            try:
                db.update_plan(plan_id, price=float(msg.text))
                bot.reply_to(msg, f"✅ Price: ₹{msg.text}")
            except:
                bot.reply_to(msg, "❌ Invalid price!")
        elif field == 'days':
            try:
                db.update_plan(plan_id, validity_days=int(msg.text))
                bot.reply_to(msg, f"✅ Validity: {msg.text} days")
            except:
                bot.reply_to(msg, "❌ Invalid days!")
        del user_data[user_id]
        return
    
    # Settings - Text
    if user_id in user_data and 'setting' in user_data[user_id]:
        key = user_data[user_id]['setting']
        if key in ['welcome_text', 'upi_id', 'delivery_link']:
            db.set_setting(key, msg.text)
            if key == 'welcome_text':
                global WELCOME_TEXT
                WELCOME_TEXT = msg.text
            elif key == 'upi_id':
                global UPI_ID
                UPI_ID = msg.text
            elif key == 'delivery_link':
                global DELIVERY_LINK
                DELIVERY_LINK = msg.text
            bot.reply_to(msg, f"✅ {key.replace('_', ' ').title()} updated!")
            del user_data[user_id]
        return
    
    # Broadcast - Text
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
    logger.info("🚀 Starting bot...")
    try:
        bot.get_me()
        logger.info("✅ Bot connected")
        
        # HTTP server
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