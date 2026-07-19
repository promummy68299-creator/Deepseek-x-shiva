import os
import sys
import json
import sqlite3
import logging
import subprocess
import time
import threading
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests
import telebot
from telebot import types
import qrcode
from io import BytesIO

# ==================== CONFIG ====================
BOT_TOKEN = "8841804008:AAGlMIF7ZLlIwMOjKcxBDyA1WpDE_Hjdn8A"
ADMIN_USER_ID = 7709767483
CHECK_INTERVAL = 3600  # 1 hour

# ==================== PATHS ====================
BASE_DIR = os.getcwd()
DATABASE_PATH = os.path.join(BASE_DIR, "data", "bot_deployment.db")
TEMPLATE_PATH = os.path.join(BASE_DIR, "templates", "bot.py")
DEPLOYMENTS_PATH = os.path.join(BASE_DIR, "deployments")
LOG_PATH = os.path.join(BASE_DIR, "logs")

for path in [os.path.dirname(DATABASE_PATH), "templates", DEPLOYMENTS_PATH, LOG_PATH, "payments", "settings"]:
    os.makedirs(path, exist_ok=True)

# ==================== DATABASE ====================
class Database:
    def __init__(self):
        self.db_path = DATABASE_PATH
        self.init_tables()
    
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_tables(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                plan_json TEXT,
                screenshot_path TEXT,
                utr TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                credit_count INTEGER DEFAULT 0,
                plan_price INTEGER,
                plan_validity INTEGER,
                purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expiry_date TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS deployments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                bot_token TEXT,
                bot_username TEXT,
                deployment_path TEXT,
                credit_id INTEGER,
                status TEXT DEFAULT 'running',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_restart TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (credit_id) REFERENCES credits (id)
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                file_path TEXT,
                uploaded_by INTEGER,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            default_plans = [
                {"id": 1, "price": 99, "validity": 15, "label": "15 days 99"},
                {"id": 2, "price": 199, "validity": 30, "label": "30 days 199"}
            ]
            c.execute('''INSERT OR IGNORE INTO settings (key, value) VALUES 
                ('welcome_text', '👋 Welcome to Bot Deployment Platform!'),
                ('welcome_image', ''),
                ('upi_id', 'admin@upi'),
                ('upi_name', 'Admin'),
                ('auto_approve', '1'),
                ('plans', ?)
            ''', (json.dumps(default_plans),))
            conn.commit()
    
    def get_setting(self, key: str) -> Optional[str]:
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT value FROM settings WHERE key = ?', (key,))
            r = c.fetchone()
            return r['value'] if r else None
    
    def update_setting(self, key: str, value: str):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?', (value, key))
            conn.commit()
    
    def get_plans(self) -> List[Dict]:
        try:
            return json.loads(self.get_setting('plans') or '[]')
        except:
            return []
    
    def update_plans(self, plans: List[Dict]):
        self.update_setting('plans', json.dumps(plans))
    
    def get_or_create_user(self, user_id, username=None, first_name=None, last_name=None):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            if not c.fetchone():
                c.execute('INSERT INTO users (user_id, username, first_name, last_name) VALUES (?,?,?,?)',
                          (user_id, username, first_name, last_name))
                conn.commit()
                return True
            return False
    
    def get_user(self, user_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            r = c.fetchone()
            return dict(r) if r else None
    
    def get_all_users(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users ORDER BY created_at DESC')
            return [dict(row) for row in c.fetchall()]
    
    def add_payment(self, user_id, amount, plan_json, screenshot_path, utr=""):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO payments (user_id, amount, plan_json, screenshot_path, utr)
                         VALUES (?,?,?,?,?)''', (user_id, amount, plan_json, screenshot_path, utr))
            conn.commit()
            return c.lastrowid
    
    def get_payment(self, payment_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
            r = c.fetchone()
            return dict(r) if r else None
    
    def get_pending_payments(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT p.*, u.username, u.first_name, u.last_name 
                         FROM payments p JOIN users u ON p.user_id = u.user_id
                         WHERE p.status = 'pending' ORDER BY p.created_at ASC''')
            return [dict(row) for row in c.fetchall()]
    
    def approve_payment(self, payment_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT user_id, plan_json FROM payments WHERE id = ? AND status = "pending"', (payment_id,))
            payment = c.fetchone()
            if not payment:
                return False
            c.execute('UPDATE payments SET status = "approved", approved_at = CURRENT_TIMESTAMP WHERE id = ?',
                      (payment_id,))
            plan = json.loads(payment['plan_json'])
            validity = plan.get('validity', 15)
            expiry = (datetime.now() + timedelta(days=validity)).isoformat()
            c.execute('''INSERT INTO credits (user_id, credit_count, plan_price, plan_validity, purchase_date, expiry_date)
                         VALUES (?, 1, ?, ?, CURRENT_TIMESTAMP, ?)''',
                      (payment['user_id'], plan.get('price', 99), validity, expiry))
            conn.commit()
            return True
    
    def reject_payment(self, payment_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('UPDATE payments SET status = "rejected" WHERE id = ?', (payment_id,))
            conn.commit()
            return True
    
    def add_credits_manual(self, user_id, count, plan_price=0, plan_validity=15):
        with self.get_connection() as conn:
            c = conn.cursor()
            expiry = (datetime.now() + timedelta(days=plan_validity)).isoformat()
            for _ in range(count):
                c.execute('''INSERT INTO credits (user_id, credit_count, plan_price, plan_validity, purchase_date, expiry_date)
                             VALUES (?, 1, ?, ?, CURRENT_TIMESTAMP, ?)''',
                          (user_id, plan_price, plan_validity, expiry))
            conn.commit()
            return True
    
    def get_available_credits(self, user_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT COUNT(*) as count FROM credits 
                         WHERE user_id = ? AND is_active = 1 AND expiry_date > datetime('now')''', (user_id,))
            r = c.fetchone()
            return r['count'] if r else 0
    
    def use_credit(self, user_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT id FROM credits 
                         WHERE user_id = ? AND is_active = 1 AND expiry_date > datetime('now')
                         ORDER BY expiry_date ASC LIMIT 1''', (user_id,))
            credit = c.fetchone()
            if not credit:
                return None
            c.execute('UPDATE credits SET is_active = 0 WHERE id = ?', (credit['id'],))
            conn.commit()
            return credit['id']
    
    def get_credit_expiry(self, user_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT expiry_date FROM credits 
                         WHERE user_id = ? AND is_active = 1 AND expiry_date > datetime('now')
                         ORDER BY expiry_date ASC LIMIT 1''', (user_id,))
            r = c.fetchone()
            return r['expiry_date'] if r else None
    
    def get_credit_details(self, user_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT plan_price, plan_validity, expiry_date FROM credits 
                         WHERE user_id = ? AND is_active = 1 AND expiry_date > datetime('now')
                         ORDER BY expiry_date ASC LIMIT 1''', (user_id,))
            r = c.fetchone()
            return dict(r) if r else None
    
    def get_all_credits(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT c.*, u.username, u.first_name, u.last_name 
                         FROM credits c JOIN users u ON c.user_id = u.user_id
                         ORDER BY c.created_at DESC''')
            return [dict(row) for row in c.fetchall()]
    
    def get_expired_credits(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT * FROM credits 
                         WHERE is_active = 1 AND expiry_date <= datetime('now')''')
            return [dict(row) for row in c.fetchall()]
    
    def expire_credit(self, credit_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('UPDATE credits SET is_active = 0 WHERE id = ?', (credit_id,))
            conn.commit()
    
    def get_deployments_by_credit(self, credit_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM deployments WHERE credit_id = ?', (credit_id,))
            return [dict(row) for row in c.fetchall()]
    
    def add_deployment(self, user_id, bot_token, bot_username, deployment_path, credit_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO deployments (user_id, bot_token, bot_username, deployment_path, credit_id)
                         VALUES (?,?,?,?,?)''', (user_id, bot_token, bot_username, deployment_path, credit_id))
            conn.commit()
            return c.lastrowid
    
    def update_deployment_status(self, deployment_id, status):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('UPDATE deployments SET status = ?, last_restart = CURRENT_TIMESTAMP WHERE id = ?',
                      (status, deployment_id))
            conn.commit()
    
    def get_user_deployments(self, user_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM deployments WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
            return [dict(row) for row in c.fetchall()]
    
    def get_all_deployments(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT d.*, u.username, u.first_name, u.last_name 
                         FROM deployments d JOIN users u ON d.user_id = u.user_id
                         ORDER BY d.created_at DESC''')
            return [dict(row) for row in c.fetchall()]
    
    def save_template(self, filename, file_path, user_id):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('UPDATE templates SET is_active = 0 WHERE is_active = 1')
            c.execute('INSERT INTO templates (filename, file_path, uploaded_by) VALUES (?,?,?)',
                      (filename, file_path, user_id))
            conn.commit()
            return c.lastrowid
    
    def get_active_template(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM templates WHERE is_active = 1 ORDER BY uploaded_at DESC LIMIT 1')
            r = c.fetchone()
            return dict(r) if r else None
    
    def add_log(self, user_id, action, details=None):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('INSERT INTO logs (user_id, action, details) VALUES (?,?,?)',
                      (user_id, action, details))
            conn.commit()
    
    def get_logs(self, limit=50):
        with self.get_connection() as conn:
            c = conn.cursor()
            c.execute('''SELECT l.*, u.username, u.first_name, u.last_name 
                         FROM logs l LEFT JOIN users u ON l.user_id = u.user_id
                         ORDER BY l.created_at DESC LIMIT ?''', (limit,))
            return [dict(row) for row in c.fetchall()]

# ==================== QR GENERATOR ====================
class QRGenerator:
    @staticmethod
    def generate(upi_id: str, amount: int, name: str = "") -> BytesIO:
        upi_url = f"upi://pay?pa={upi_id}&am={amount}&cu=INR"
        if name:
            upi_url += f"&pn={name}"
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(upi_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_io = BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        return img_io

# ==================== DEPLOYMENT MANAGER ====================
class DeploymentManager:
    def __init__(self):
        self.deployments_path = Path(DEPLOYMENTS_PATH)
        self.deployments_path.mkdir(parents=True, exist_ok=True)
        self.db = Database()
        self.running_processes = {}
    
    def validate_bot_token(self, token: str) -> Tuple[bool, Optional[str]]:
        token = token.strip()
        try:
            r = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=5)
            if r.status_code == 200 and r.json().get('ok'):
                return True, r.json()['result']['username']
            return False, None
        except:
            return False, None
    
    def get_template_content(self) -> Optional[str]:
        t = self.db.get_active_template()
        if not t:
            return None
        p = Path(t['file_path'])
        if not p.exists():
            return None
        with open(p, 'r') as f:
            return f.read()
    
    def deploy_bot(self, user_id: int, bot_token: str) -> Tuple[bool, str]:
        bot_token = bot_token.strip()
        valid, username = self.validate_bot_token(bot_token)
        if not valid:
            return False, "❌ Invalid or expired bot token."
        credits = self.db.get_available_credits(user_id)
        if credits <= 0:
            return False, "❌ No credits available. Buy credits first."
        # prevent duplicate
        deps = self.db.get_user_deployments(user_id)
        for d in deps:
            if d['bot_token'] == bot_token and d['status'] == 'running':
                return False, "⚠️ This bot is already running."
        credit_id = self.db.use_credit(user_id)
        if not credit_id:
            return False, "❌ Could not use a credit."
        template = self.get_template_content()
        if not template:
            return False, "❌ No template available. Contact admin."
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        deploy_folder = self.deployments_path / f"{user_id}_{timestamp}"
        deploy_folder.mkdir(parents=True, exist_ok=True)
        bot_code = template.replace('YOUR_BOT_TOKEN', bot_token)
        bot_file = deploy_folder / 'bot.py'
        with open(bot_file, 'w') as f:
            f.write(bot_code)
        config = {
            'user_id': user_id,
            'bot_token': bot_token,
            'bot_username': username,
            'deployed_at': datetime.now().isoformat(),
            'status': 'running',
            'credit_id': credit_id
        }
        with open(deploy_folder / 'config.json', 'w') as f:
            json.dump(config, f, indent=2)
        log_file = deploy_folder / 'logs.txt'
        log_file.touch()
        deployment_id = self.db.add_deployment(user_id, bot_token, username, str(deploy_folder), credit_id)
        success = self.start_bot(deploy_folder, username)
        if success:
            self.db.update_deployment_status(deployment_id, 'running')
            self.db.add_log(user_id, 'deploy', f'✅ Deployed @{username}')
            # notify admin
            try:
                admin_msg = f"🚀 **New Bot Deployed**\n\nUser: {self.db.get_user(user_id)['first_name']}\nID: `{user_id}`\nBot: @{username}"
                telebot.TeleBot(BOT_TOKEN).send_message(ADMIN_USER_ID, admin_msg, parse_mode='Markdown')
            except:
                pass
            return True, f"✅ Bot @{username} deployed successfully!"
        else:
            self.db.update_deployment_status(deployment_id, 'failed')
            self.db.add_log(user_id, 'deploy_failed', f'Failed @{username}')
            error = ""
            try:
                with open(log_file, 'r') as f:
                    error = f.read()[-500:]
            except:
                pass
            return False, f"❌ Bot failed to start.\n\n📋 Error:\n```\n{error}\n```"
    
    def start_bot(self, deploy_path: Path, bot_username: str) -> bool:
        try:
            bot_file = deploy_path / 'bot.py'
            log_file = deploy_path / 'logs.txt'
            with open(log_file, 'a') as f:
                f.write(f"\n=== Bot Deployment Started at {datetime.now()} ===\n")
                f.write(f"Bot: @{bot_username}\n")
                f.write(f"Python: {sys.executable}\n")
                f.write(f"Working Dir: {deploy_path}\n")
                f.write(f"Bot File: {bot_file}\n\n")
            if not bot_file.exists():
                with open(log_file, 'a') as f:
                    f.write(f"❌ ERROR: bot.py not found\n")
                return False
            if sys.platform == 'win32':
                process = subprocess.Popen(
                    [sys.executable, str(bot_file)],
                    stdout=open(log_file, 'a'),
                    stderr=open(log_file, 'a'),
                    cwd=str(deploy_path),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    stdin=subprocess.DEVNULL
                )
            else:
                process = subprocess.Popen(
                    [sys.executable, str(bot_file)],
                    stdout=open(log_file, 'a'),
                    stderr=open(log_file, 'a'),
                    cwd=str(deploy_path),
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
            self.running_processes[str(deploy_path)] = process
            time.sleep(3)
            if process.poll() is None:
                with open(log_file, 'a') as f:
                    f.write(f"✅ Bot started successfully at {datetime.now()}\n")
                logging.info(f"✅ Bot @{bot_username} started")
                return True
            else:
                logging.error(f"❌ Bot @{bot_username} failed to start")
                return False
        except Exception as e:
            logging.error(f"Start error: {e}")
            with open(deploy_path / 'logs.txt', 'a') as f:
                f.write(f"\n[ERROR] {e}\n")
            return False
    
    def stop_bot(self, deploy_path: Path) -> bool:
        try:
            p = str(deploy_path)
            if p in self.running_processes:
                self.running_processes[p].terminate()
                self.running_processes[p].wait(timeout=5)
                del self.running_processes[p]
                return True
            return False
        except:
            return False

# ==================== BACKGROUND CREDIT CHECKER ====================
class CreditExpiryChecker:
    def __init__(self, bot_app):
        self.bot_app = bot_app
        self.db = bot_app.db
        self.deployment_manager = bot_app.deployment_manager
        self.bot = bot_app.bot
        self.running = True
    
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        logging.info("🔄 Credit expiry checker started")
    
    def _run(self):
        while self.running:
            try:
                self.check()
            except:
                pass
            time.sleep(CHECK_INTERVAL)
    
    def check(self):
        expired = self.db.get_expired_credits()
        for credit in expired:
            credit_id = credit['id']
            user_id = credit['user_id']
            deps = self.db.get_deployments_by_credit(credit_id)
            self.db.expire_credit(credit_id)
            for d in deps:
                p = Path(d['deployment_path'])
                self.deployment_manager.stop_bot(p)
                self.db.update_deployment_status(d['id'], 'expired')
                self.db.add_log(user_id, 'credit_expired', f'Stopped @{d["bot_username"]}')
            user = self.db.get_user(user_id)
            if user:
                try:
                    msg = f"⏰ **Credit Expired**\n\nYour credit expired on {credit['expiry_date'][:10]}.\n"
                    if deps:
                        msg += "\n🤖 **Bots Stopped:**\n" + "\n".join(f"• @{d['bot_username']}" for d in deps)
                    msg += "\n\n💡 Buy new credits to deploy again."
                    self.bot.send_message(user_id, msg, parse_mode='Markdown')
                except:
                    pass
            try:
                self.bot.send_message(ADMIN_USER_ID, f"⏰ Credit expired\nUser: {user['first_name'] if user else 'Unknown'}\nID: {user_id}\nBots stopped: {len(deps)}", parse_mode='Markdown')
            except:
                pass

# ==================== MAIN BOT ====================
class BotApplication:
    def __init__(self):
        self.setup_logging()
        self.bot = telebot.TeleBot(BOT_TOKEN)
        self.db = Database()
        self.deployment_manager = DeploymentManager()
        self.admin_id = ADMIN_USER_ID
        self.setup_handlers()
        self.create_default_template()
        self.checker = CreditExpiryChecker(self)
        self.checker.start()
    
    def setup_logging(self):
        log_dir = Path(LOG_PATH)
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[logging.FileHandler(log_dir / 'bot.log'), logging.StreamHandler()]
        )
    
    def create_default_template(self):
        p = Path(TEMPLATE_PATH)
        if p.exists():
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        t = '''import os, sys, logging, telebot
from telebot import types
BOT_TOKEN = "YOUR_BOT_TOKEN"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler('logs.txt'), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)
bot = telebot.TeleBot(BOT_TOKEN)
@bot.message_handler(commands=['start'])
def start(m):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(types.KeyboardButton('👋 Hello'), types.KeyboardButton('📖 Help'))
    bot.reply_to(m, "👋 Welcome! Send /help", reply_markup=markup)
@bot.message_handler(commands=['help'])
def h(m):
    bot.reply_to(m, "📖 Commands: /start /help /ping /info")
@bot.message_handler(commands=['ping'])
def p(m):
    bot.reply_to(m, "🏓 Pong!")
@bot.message_handler(commands=['info'])
def i(m):
    bot.reply_to(m, f"🤖 @{bot.get_me().username}\\nStatus: Running")
@bot.message_handler(func=lambda m: True)
def e(m):
    bot.reply_to(m, f"You said: {m.text}")
if __name__ == '__main__':
    logger.info("🚀 Starting bot...")
    try:
        bot.infinity_polling()
    except Exception as e:
        logger.error(f"❌ {e}")
        sys.exit(1)
'''
        with open(p, 'w') as f:
            f.write(t)
        self.db.save_template('bot.py', str(p), self.admin_id)
        logging.info("✅ Default template created")
    
    def setup_handlers(self):
        @self.bot.message_handler(commands=['start'])
        def start_cmd(m):
            uid = m.from_user.id
            self.db.get_or_create_user(uid, m.from_user.username, m.from_user.first_name, m.from_user.last_name)
            welcome = self.db.get_setting('welcome_text')
            img = self.db.get_setting('welcome_image')
            if uid == self.admin_id:
                if img:
                    try:
                        self.bot.send_photo(m.chat.id, open(img, 'rb'), caption=f"{welcome}\n\n🛡️ Admin Ready!", parse_mode='Markdown')
                    except:
                        self.bot.send_message(m.chat.id, f"{welcome}\n\n🛡️ Admin Ready!", parse_mode='Markdown')
                else:
                    self.bot.send_message(m.chat.id, f"{welcome}\n\n🛡️ Admin Ready!", parse_mode='Markdown')
                self.show_admin_menu(m)
            else:
                if img:
                    try:
                        self.bot.send_photo(m.chat.id, open(img, 'rb'), caption=welcome, parse_mode='Markdown')
                    except:
                        self.bot.send_message(m.chat.id, welcome, parse_mode='Markdown')
                else:
                    self.bot.send_message(m.chat.id, welcome, parse_mode='Markdown')
                self.show_user_menu(m)
        
        # ========== USER MENU ==========
        @self.bot.message_handler(func=lambda m: m.text == '💳 Buy Credits')
        def buy_credits(m):
            plans = self.db.get_plans()
            if not plans:
                self.bot.send_message(m.chat.id, "❌ No plans available.", parse_mode='Markdown')
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            for p in plans:
                markup.add(types.InlineKeyboardButton(f"🛒 {p['label']} - ₹{p['price']}", callback_data=f"buy_plan_{p['id']}"))
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data='cancel'))
            self.bot.send_message(m.chat.id, "💳 **Buy Credits**\n\nSelect plan:", reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith('buy_plan_'))
        def buy_plan_cb(c):
            plan_id = int(c.data.split('_')[2])
            plans = self.db.get_plans()
            plan = next((p for p in plans if p['id'] == plan_id), None)
            if not plan:
                self.bot.answer_callback_query(c.id, "❌ Plan not found!", show_alert=True)
                return
            upi_id = self.db.get_setting('upi_id') or "admin@upi"
            upi_name = self.db.get_setting('upi_name') or "Admin"
            qr = QRGenerator.generate(upi_id, plan['price'], upi_name)
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("📸 I have paid", callback_data=f"paid_{plan_id}"))
            markup.add(types.InlineKeyboardButton("❌ Cancel", callback_data='cancel'))
            self.bot.send_photo(c.message.chat.id, qr,
                caption=f"💳 **Payment**\n\n📦 Plan: {plan['label']}\n💰 ₹{plan['price']}\n📅 {plan['validity']} days\n\n🏦 **UPI:** `{upi_id}`\n👤 {upi_name}\n\nScan QR or UPI, then click **I have paid**.",
                reply_markup=markup, parse_mode='Markdown')
            self.bot.delete_message(c.message.chat.id, c.message.message_id)
            self.bot.answer_callback_query(c.id, f"✅ Plan selected")
        
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith('paid_'))
        def paid_cb(c):
            plan_id = int(c.data.split('_')[1])
            plans = self.db.get_plans()
            plan = next((p for p in plans if p['id'] == plan_id), None)
            if not plan:
                self.bot.answer_callback_query(c.id, "❌ Plan not found!", show_alert=True)
                return
            self.bot.edit_message_caption(
                f"📸 **Upload Screenshot**\n\nPlan: {plan['label']}\nAmount: ₹{plan['price']}\n\nSend screenshot with UTR in caption.\nExample: `UTR: 1234567890`",
                c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(c.message, process_payment_screenshot, plan)
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'cancel')
        def cancel_cb(c):
            self.bot.edit_message_text("❌ Cancelled.", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
        
        def process_payment_screenshot(m, plan):
            uid = m.from_user.id
            if not m.photo:
                self.bot.send_message(m.chat.id, "❌ Send a photo.", parse_mode='Markdown')
                return
            utr = ""
            if m.caption:
                utr_match = re.search(r'(?:UTR|utr|txn|TXN)[\s:]+([A-Za-z0-9]+)', m.caption)
                if utr_match:
                    utr = utr_match.group(1)
                else:
                    num_match = re.search(r'\b([0-9]{8,16})\b', m.caption)
                    if num_match:
                        utr = num_match.group(1)
            try:
                fi = self.bot.get_file(m.photo[-1].file_id)
                file_path = f"payments/{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                dl = self.bot.download_file(fi.file_path)
                with open(file_path, 'wb') as f:
                    f.write(dl)
                plan_json = json.dumps(plan)
                pid = self.db.add_payment(uid, plan['price'], plan_json, file_path, utr)
                self.db.add_log(uid, 'payment_submitted', f'ID: {pid}, UTR: {utr}')
                auto_approve = self.db.get_setting('auto_approve') or '1'
                if auto_approve == '1':
                    if self.db.approve_payment(pid):
                        expiry = self.db.get_credit_expiry(uid)
                        credits = self.db.get_available_credits(uid)
                        msg = f"✅ **PAYMENT APPROVED!** 🎉\n\n━━━━━━━━━━━━━━━━━━━\n💳 Plan: {plan['label']}\n💰 ₹{plan['price']}\n🆔 ID: `{pid}`\n📝 UTR: `{utr or 'N/A'}`\n📅 {datetime.now().strftime('%d-%m-%Y %H:%M')}\n\n━━━━━━━━━━━━━━━━━━━\n💎 Credits Added: 1\n📦 Total: {credits}\n📅 Expiry: `{expiry or 'N/A'}`\n\n🚀 Click below to deploy:"
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        markup.add(types.InlineKeyboardButton("🚀 Deploy Now", callback_data="go_deploy"))
                        self.bot.send_message(uid, msg, reply_markup=markup, parse_mode='Markdown')
                        self.bot.send_message(m.chat.id, "✅ Auto-approved! Credit added.", parse_mode='Markdown')
                        self.bot.send_message(self.admin_id, f"✅ Auto approved\nUser: {m.from_user.first_name}\nID: {uid}\nPlan: {plan['label']}\nUTR: {utr}", parse_mode='Markdown')
                        return
                # manual
                self.bot.send_message(m.chat.id, f"✅ Payment submitted! ID: `{pid}`\n₹{plan['price']}\nUTR: `{utr or 'N/A'}`\n\n⏳ Waiting for admin.", parse_mode='Markdown')
                if self.admin_id:
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    markup.add(types.InlineKeyboardButton('✅ Approve', callback_data=f'approve_payment_{pid}'),
                               types.InlineKeyboardButton('❌ Reject', callback_data=f'reject_payment_{pid}'))
                    self.bot.send_photo(self.admin_id, open(file_path, 'rb'),
                                       caption=f"💰 New Payment\nUser: {m.from_user.first_name}\nID: {uid}\nPlan: {plan['label']}\nAmount: ₹{plan['price']}\nUTR: {utr}",
                                       reply_markup=markup, parse_mode='Markdown')
            except Exception as e:
                logging.error(f"Payment error: {e}")
                self.bot.send_message(m.chat.id, "❌ Failed. Try again.", parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'go_deploy')
        def go_deploy_cb(c):
            uid = c.from_user.id
            credits = self.db.get_available_credits(uid)
            if credits <= 0:
                self.bot.answer_callback_query(c.id, "❌ No credits!", show_alert=True)
                return
            self.bot.edit_message_text(f"🚀 **Deployment**\n\nStep 1: Send bot token.\n\n💳 You have {credits} credit(s).\n\nSend token from @BotFather (no spaces):",
                                       c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(c.message, process_bot_token)
        
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith('approve_payment_'))
        def approve_payment_cb(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            pid = int(c.data.split('_')[2])
            if self.db.approve_payment(pid):
                payment = self.db.get_payment(pid)
                if payment:
                    plan = json.loads(payment['plan_json'])
                    uid = payment['user_id']
                    utr = payment.get('utr', 'N/A')
                    credits = self.db.get_available_credits(uid)
                    expiry = self.db.get_credit_expiry(uid)
                    msg = f"✅ **PAYMENT APPROVED!** 🎉\n\n━━━━━━━━━━━━━━━━━━━\n💳 Plan: {plan['label']}\n💰 ₹{plan['price']}\n🆔 ID: `{pid}`\n📝 UTR: `{utr}`\n📅 {datetime.now().strftime('%d-%m-%Y %H:%M')}\n\n━━━━━━━━━━━━━━━━━━━\n💎 Credits Added: 1\n📦 Total: {credits}\n📅 Expiry: `{expiry or 'N/A'}`\n\n🚀 Click to deploy:"
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    markup.add(types.InlineKeyboardButton("🚀 Deploy Now", callback_data="go_deploy"))
                    self.bot.send_message(uid, msg, reply_markup=markup, parse_mode='Markdown')
                    self.bot.edit_message_caption(f"✅ Approved!\n\n{c.message.caption}", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
                    self.bot.send_message(c.message.chat.id, "✅ Approved, user notified.", parse_mode='Markdown')
                    self.db.add_log(uid, 'payment_approved', f'ID: {pid}')
                    self.bot.answer_callback_query(c.id, "✅ Approved")
                else:
                    self.bot.answer_callback_query(c.id, "❌ Payment not found", show_alert=True)
            else:
                self.bot.answer_callback_query(c.id, "❌ Failed", show_alert=True)
        
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith('reject_payment_'))
        def reject_payment_cb(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            pid = int(c.data.split('_')[2])
            if self.db.reject_payment(pid):
                payment = self.db.get_payment(pid)
                if payment:
                    uid = payment['user_id']
                    self.bot.send_message(uid, f"❌ Payment rejected.\nID: `{pid}`\nTry again.", parse_mode='Markdown')
                    self.bot.edit_message_caption(f"❌ Rejected!\n\n{c.message.caption}", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
                    self.bot.send_message(c.message.chat.id, "❌ Rejected.", parse_mode='Markdown')
                    self.db.add_log(uid, 'payment_rejected', f'ID: {pid}')
                    self.bot.answer_callback_query(c.id, "❌ Rejected")
                else:
                    self.bot.answer_callback_query(c.id, "❌ Not found", show_alert=True)
            else:
                self.bot.answer_callback_query(c.id, "❌ Failed", show_alert=True)
        
        @self.bot.message_handler(func=lambda m: m.text == '🚀 Deploy Bot')
        def deploy_bot(m):
            uid = m.from_user.id
            credits = self.db.get_available_credits(uid)
            if credits <= 0:
                self.bot.send_message(m.chat.id, "❌ No credits. Buy credits first.", parse_mode='Markdown')
                return
            self.bot.send_message(m.chat.id, f"🚀 **Deployment**\n\nStep 1: Send bot token.\n💳 You have {credits} credit(s).\n\nSend token from @BotFather (no spaces):", parse_mode='Markdown')
            self.bot.register_next_step_handler(m, process_bot_token)
        
        def process_bot_token(m):
            uid = m.from_user.id
            token = m.text.strip()
            if not token or ' ' in token:
                self.bot.send_message(m.chat.id, "❌ Token contains spaces. Send without spaces.", parse_mode='Markdown')
                return
            msg = self.bot.send_message(m.chat.id, "⏳ Validating token and deploying...", parse_mode='Markdown')
            success, result = self.deployment_manager.deploy_bot(uid, token)
            if success:
                self.bot.edit_message_text(f"✅ {result}", msg.chat.id, msg.message_id, parse_mode='Markdown')
                self.bot.send_message(m.chat.id, "🚀 Bot is now running!\n📦 Manage from My Deployments.", parse_mode='Markdown')
            else:
                self.bot.edit_message_text(f"❌ {result}", msg.chat.id, msg.message_id, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '📦 My Deployments')
        def my_deployments(m):
            uid = m.from_user.id
            deps = self.db.get_user_deployments(uid)
            if not deps:
                self.bot.send_message(m.chat.id, "📦 No deployments.", parse_mode='Markdown')
                return
            resp = "📦 **Your Deployments**\n\n"
            for i, d in enumerate(deps[:5], 1):
                emoji = "🟢" if d['status'] == 'running' else ("🔴" if d['status'] == 'expired' else "⚪")
                resp += f"{i}. {emoji} @{d['bot_username']}\n   Status: **{d['status']}**\n   Deployed: {d['created_at'][:10]}\n   ID: `{d['id']}`\n\n"
            if len(deps) > 5:
                resp += f"📊 Total: {len(deps)}"
            self.bot.send_message(m.chat.id, resp, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '👤 Profile')
        def profile(m):
            uid = m.from_user.id
            credits = self.db.get_available_credits(uid)
            expiry = self.db.get_credit_expiry(uid)
            detail = self.db.get_credit_details(uid)
            deps = self.db.get_user_deployments(uid)
            running = len([d for d in deps if d['status'] == 'running'])
            resp = f"👤 **Profile**\n\n📛 {m.from_user.first_name}\n👤 @{m.from_user.username or 'N/A'}\n🆔 `{uid}`\n\n💳 Credits: {credits}\n📅 Expiry: {expiry or 'No credits'}"
            if detail:
                resp += f"\n💰 Plan: ₹{detail['plan_price']} - {detail['plan_validity']} days"
            resp += f"\n🤖 Running: {running}\n📦 Total: {len(deps)}"
            self.bot.send_message(m.chat.id, resp, parse_mode='Markdown')
        
        # ========== ADMIN ==========
        @self.bot.message_handler(func=lambda m: m.text == '📤 Upload Template')
        def upload_template(m):
            if m.from_user.id != self.admin_id: return
            self.bot.send_message(m.chat.id, "📤 Send bot.py with `YOUR_BOT_TOKEN`", parse_mode='Markdown')
            self.bot.register_next_step_handler(m, process_template_upload)
        
        def process_template_upload(m):
            if m.from_user.id != self.admin_id: return
            if not m.document or not m.document.file_name.endswith('.py'):
                self.bot.send_message(m.chat.id, "❌ Send .py file.", parse_mode='Markdown')
                return
            try:
                fi = self.bot.get_file(m.document.file_id)
                file_path = os.path.join("templates", m.document.file_name)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                dl = self.bot.download_file(fi.file_path)
                content = dl.decode('utf-8')
                if 'YOUR_BOT_TOKEN' not in content:
                    self.bot.send_message(m.chat.id, "❌ Missing YOUR_BOT_TOKEN", parse_mode='Markdown')
                    return
                with open(file_path, 'wb') as f:
                    f.write(dl)
                tid = self.db.save_template(m.document.file_name, file_path, m.from_user.id)
                self.db.add_log(m.from_user.id, 'template_upload', f'ID: {tid}')
                self.bot.send_message(m.chat.id, f"✅ Template uploaded: {m.document.file_name}", parse_mode='Markdown')
            except Exception as e:
                self.bot.send_message(m.chat.id, f"❌ Error: {e}", parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '📄 Current Template')
        def current_template(m):
            if m.from_user.id != self.admin_id: return
            t = self.db.get_active_template()
            if not t:
                self.bot.send_message(m.chat.id, "❌ No template.", parse_mode='Markdown')
                return
            self.bot.send_message(m.chat.id, f"📄 **Current Template**\nFile: `{t['filename']}`\nUploaded: {t['uploaded_at']}", parse_mode='Markdown')
            try:
                with open(t['file_path'], 'rb') as f:
                    self.bot.send_document(m.chat.id, f)
            except:
                pass
        
        @self.bot.message_handler(func=lambda m: m.text == '💳 Payments')
        def payments_admin(m):
            if m.from_user.id != self.admin_id: return
            payments = self.db.get_pending_payments()
            if not payments:
                self.bot.send_message(m.chat.id, "💳 No pending payments.", parse_mode='Markdown')
                return
            resp = f"💳 **Pending** ({len(payments)})\n\n"
            for p in payments[:10]:
                plan = json.loads(p.get('plan_json', '{}'))
                label = plan.get('label', 'N/A')
                resp += f"🆔 `{p['id']}`\n👤 {p['first_name']} (@{p['username'] or 'N/A'})\n📦 {label}\n💰 ₹{p['amount']}\n📝 UTR: `{p.get('utr', 'N/A')}`\n📅 {p['created_at']}\n───\n"
            self.bot.send_message(m.chat.id, resp, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '👥 Users')
        def users_admin(m):
            if m.from_user.id != self.admin_id: return
            users = self.db.get_all_users()
            resp = f"👥 **Users** ({len(users)})\n\n"
            for u in users[:10]:
                credits = self.db.get_available_credits(u['user_id'])
                resp += f"🆔 `{u['user_id']}`\n📛 {u['first_name'] or 'N/A'}\n👤 @{u['username'] or 'N/A'}\n💳 {credits} credits\n📅 {u['created_at']}\n───\n"
            self.bot.send_message(m.chat.id, resp, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '💎 Credits')
        def credits_admin(m):
            if m.from_user.id != self.admin_id: return
            credits = self.db.get_all_credits()
            resp = f"💎 **All Credits** ({len(credits)})\n\n"
            for c in credits[:10]:
                resp += f"👤 {c['first_name'] or 'N/A'}\n💳 1 credit\n💰 ₹{c['plan_price']}\n📅 Expiry: {c['expiry_date']}\n📊 {'✅ Active' if c['is_active'] else '❌ Expired'}\n───\n"
            self.bot.send_message(m.chat.id, resp, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '🤖 Deployments')
        def deployments_admin(m):
            if m.from_user.id != self.admin_id: return
            deps = self.db.get_all_deployments()
            resp = f"🤖 **All Deployments** ({len(deps)})\n\n"
            for d in deps[:10]:
                emoji = "🟢" if d['status'] == 'running' else ("🔴" if d['status'] == 'expired' else "⚪")
                resp += f"👤 {d['first_name'] or 'N/A'}\n🤖 @{d['bot_username']}\n📊 {emoji} {d['status']}\n📅 {d['created_at']}\n───\n"
            self.bot.send_message(m.chat.id, resp, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '📋 Logs')
        def logs_admin(m):
            if m.from_user.id != self.admin_id: return
            logs = self.db.get_logs(20)
            if not logs:
                self.bot.send_message(m.chat.id, "📋 No logs.", parse_mode='Markdown')
                return
            resp = f"📋 **Recent Logs** ({len(logs)})\n\n"
            for log in logs:
                resp += f"📅 {log['created_at']}\n👤 @{log.get('username') or 'N/A'}\n📝 {log['action']}\n{log['details'] or ''}\n───\n"
            self.bot.send_message(m.chat.id, resp, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '🖼️ Welcome Settings')
        def welcome_settings(m):
            if m.from_user.id != self.admin_id: return
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton('📝 Change Text', callback_data='change_welcome_text'),
                       types.InlineKeyboardButton('🖼️ Change Image', callback_data='change_welcome_image'),
                       types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            text = self.db.get_setting('welcome_text')
            img = self.db.get_setting('welcome_image')
            self.bot.send_message(m.chat.id, f"🖼️ **Welcome Settings**\n\n📝 Text: {text}\n🖼️ Image: {'✅ Set' if img else '❌ Not Set'}", reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '💰 UPI Settings')
        def upi_settings(m):
            if m.from_user.id != self.admin_id: return
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton('🏦 UPI ID', callback_data='edit_upi_id'),
                       types.InlineKeyboardButton('📛 UPI Name', callback_data='edit_upi_name'),
                       types.InlineKeyboardButton('🤖 Auto Approve', callback_data='toggle_auto_approve'),
                       types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            upi_id = self.db.get_setting('upi_id') or "Not Set"
            upi_name = self.db.get_setting('upi_name') or "Not Set"
            auto = self.db.get_setting('auto_approve') or '1'
            self.bot.send_message(m.chat.id, f"💰 **UPI Settings**\n\n🏦 UPI ID: `{upi_id}`\n📛 UPI Name: {upi_name}\n🤖 Auto Approve: {'✅ ON' if auto == '1' else '❌ OFF'}", reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '➕ Add Credits')
        def add_credits_menu(m):
            if m.from_user.id != self.admin_id: return
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton('👥 Select User', callback_data='add_credits_select_user'),
                       types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            self.bot.send_message(m.chat.id, "➕ **Add Credits**\nSelect user:", reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'add_credits_select_user')
        def add_credits_select_user(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            users = self.db.get_all_users()
            if not users:
                self.bot.edit_message_text("❌ No users.", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            for u in users[:20]:
                name = u.get('first_name', 'Unknown')
                uname = u.get('username', '')
                label = f"{name} (@{uname})" if uname else name
                markup.add(types.InlineKeyboardButton(label, callback_data=f"add_credits_user_{u['user_id']}"))
            markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            self.bot.edit_message_text("👥 Select User", c.message.chat.id, c.message.message_id, reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith('add_credits_user_'))
        def add_credits_user_selected(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            uid = int(c.data.split('_')[3])
            self.bot.edit_message_text(f"👤 User ID: `{uid}`\n\n📝 Enter number of credits to add:", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(c.message, process_add_credits_amount, uid)
        
        def process_add_credits_amount(m, uid):
            if m.from_user.id != self.admin_id: return
            try:
                count = int(m.text.strip())
                if count <= 0:
                    self.bot.send_message(m.chat.id, "❌ Positive number only.", parse_mode='Markdown')
                    return
                self.db.add_credits_manual(uid, count, 0, 15)
                self.db.add_log(self.admin_id, 'manual_credit_add', f'Added {count} to {uid}')
                user = self.db.get_user(uid)
                if user:
                    total = self.db.get_available_credits(uid)
                    expiry = self.db.get_credit_expiry(uid)
                    self.bot.send_message(uid, f"✅ **Credits Added!**\n\n💳 {count} credit(s) added.\n📦 Total: {total}\n📅 Expiry: {expiry or 'N/A'}\n\n🚀 Deploy now!", parse_mode='Markdown')
                self.bot.send_message(m.chat.id, f"✅ Added {count} credit(s) to `{uid}`.", parse_mode='Markdown')
                self.show_admin_menu(m)
            except:
                self.bot.send_message(m.chat.id, "❌ Invalid number.", parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '📋 Plans')
        def plans_admin(m):
            if m.from_user.id != self.admin_id: return
            plans = self.db.get_plans()
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton('➕ Add Plan', callback_data='add_plan'),
                       types.InlineKeyboardButton('🗑️ Delete Plan', callback_data='delete_plan_list'),
                       types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            resp = "📋 **Plans**\n\n"
            if plans:
                for p in plans:
                    resp += f"🆔 {p['id']} - {p['label']} - ₹{p['price']} - {p['validity']} days\n"
            else:
                resp += "No plans.\n"
            self.bot.send_message(m.chat.id, resp, reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'add_plan')
        def add_plan(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            self.bot.edit_message_text("➕ **Add Plan**\n\nSend format:\n`label, price, validity`\nExample: `15 days 99, 99, 15`", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(c.message, process_add_plan)
        
        def process_add_plan(m):
            if m.from_user.id != self.admin_id: return
            try:
                parts = m.text.split(',')
                if len(parts) != 3:
                    self.bot.send_message(m.chat.id, "❌ Use: label, price, validity", parse_mode='Markdown')
                    return
                label = parts[0].strip()
                price = int(parts[1].strip())
                validity = int(parts[2].strip())
                if price <= 0 or validity <= 0:
                    self.bot.send_message(m.chat.id, "❌ Must be positive.", parse_mode='Markdown')
                    return
                plans = self.db.get_plans()
                new_id = max([p['id'] for p in plans], default=0) + 1
                plans.append({"id": new_id, "label": label, "price": price, "validity": validity})
                self.db.update_plans(plans)
                self.db.add_log(self.admin_id, 'plan_added', f'Plan: {label}')
                self.bot.send_message(m.chat.id, f"✅ Plan added!\n{label} - ₹{price} - {validity} days", parse_mode='Markdown')
                self.show_admin_menu(m)
            except:
                self.bot.send_message(m.chat.id, "❌ Invalid format.", parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'delete_plan_list')
        def delete_plan_list(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            plans = self.db.get_plans()
            if not plans:
                self.bot.edit_message_text("❌ No plans.", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            for p in plans:
                markup.add(types.InlineKeyboardButton(f"{p['label']} - ₹{p['price']}", callback_data=f"delete_plan_{p['id']}"))
            markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            self.bot.edit_message_text("🗑️ Select plan to delete:", c.message.chat.id, c.message.message_id, reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith('delete_plan_'))
        def delete_plan(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            pid = int(c.data.split('_')[2])
            plans = self.db.get_plans()
            plan = next((p for p in plans if p['id'] == pid), None)
            if not plan:
                self.bot.answer_callback_query(c.id, "❌ Not found", show_alert=True)
                return
            plans = [p for p in plans if p['id'] != pid]
            self.db.update_plans(plans)
            self.db.add_log(self.admin_id, 'plan_deleted', f'Plan: {plan["label"]}')
            self.bot.edit_message_text(f"✅ Deleted: {plan['label']}", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.answer_callback_query(c.id, f"✅ Deleted")
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'back_admin')
        def back_admin(c):
            if c.from_user.id != self.admin_id: return
            self.bot.edit_message_text("🔙 Back", c.message.chat.id, c.message.message_id)
            self.show_admin_menu(c.message)
        
        @self.bot.callback_query_handler(func=lambda c: c.data.startswith('edit_'))
        def edit_field(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            field = c.data.replace('edit_', '')
            self.bot.edit_message_text(f"✏️ Send new {field.replace('_', ' ').title()}:", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(c.message, process_edit, field)
        
        def process_edit(m, field):
            if m.from_user.id != self.admin_id: return
            self.db.update_setting(field, m.text.strip())
            self.db.add_log(self.admin_id, 'settings', f'Updated {field}')
            self.bot.send_message(m.chat.id, f"✅ {field.replace('_', ' ').title()} updated!", parse_mode='Markdown')
            self.show_admin_menu(m)
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'toggle_auto_approve')
        def toggle_auto_approve(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            cur = self.db.get_setting('auto_approve') or '1'
            new = '0' if cur == '1' else '1'
            self.db.update_setting('auto_approve', new)
            self.db.add_log(self.admin_id, 'settings', f'Auto approve: {new}')
            self.bot.edit_message_text(f"✅ Auto Approve {'ON' if new == '1' else 'OFF'}", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.answer_callback_query(c.id, f"Auto Approve {'ON' if new == '1' else 'OFF'}")
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'change_welcome_text')
        def change_welcome_text(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            self.bot.edit_message_text("📝 Send new welcome text:", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(c.message, process_welcome_text)
        
        def process_welcome_text(m):
            if m.from_user.id != self.admin_id: return
            self.db.update_setting('welcome_text', m.text)
            self.db.add_log(self.admin_id, 'settings', 'Welcome text updated')
            self.bot.send_message(m.chat.id, "✅ Welcome text updated!", parse_mode='Markdown')
            self.show_admin_menu(m)
        
        @self.bot.callback_query_handler(func=lambda c: c.data == 'change_welcome_image')
        def change_welcome_image(c):
            if c.from_user.id != self.admin_id:
                self.bot.answer_callback_query(c.id, "⛔ Unauthorized!", show_alert=True)
                return
            self.bot.edit_message_text("🖼️ Send photo:", c.message.chat.id, c.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(c.message, process_welcome_image)
        
        def process_welcome_image(m):
            if m.from_user.id != self.admin_id: return
            if not m.photo:
                self.bot.send_message(m.chat.id, "❌ Send photo.", parse_mode='Markdown')
                return
            try:
                fi = self.bot.get_file(m.photo[-1].file_id)
                file_path = "settings/welcome_image.jpg"
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                dl = self.bot.download_file(fi.file_path)
                with open(file_path, 'wb') as f:
                    f.write(dl)
                self.db.update_setting('welcome_image', file_path)
                self.db.add_log(self.admin_id, 'settings', 'Welcome image updated')
                self.bot.send_message(m.chat.id, "✅ Welcome image updated!", parse_mode='Markdown')
                self.show_admin_menu(m)
            except:
                self.bot.send_message(m.chat.id, "❌ Error.", parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '🔙 Back')
        def back_to_main(m):
            if m.from_user.id == self.admin_id:
                self.show_admin_menu(m)
            else:
                self.show_user_menu(m)
    
    def show_user_menu(self, m):
        markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(types.KeyboardButton('💳 Buy Credits'), types.KeyboardButton('🚀 Deploy Bot'))
        markup.add(types.KeyboardButton('📦 My Deployments'), types.KeyboardButton('👤 Profile'))
        self.bot.send_message(m.chat.id, "🤖 **Bot Deployment Platform**\n\nChoose:", reply_markup=markup, parse_mode='Markdown')
    
    def show_admin_menu(self, m):
        markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(types.KeyboardButton('📤 Upload Template'), types.KeyboardButton('📄 Current Template'))
        markup.add(types.KeyboardButton('💳 Payments'), types.KeyboardButton('👥 Users'))
        markup.add(types.KeyboardButton('💎 Credits'), types.KeyboardButton('🤖 Deployments'))
        markup.add(types.KeyboardButton('📋 Logs'), types.KeyboardButton('🖼️ Welcome Settings'))
        markup.add(types.KeyboardButton('💰 UPI Settings'), types.KeyboardButton('➕ Add Credits'))
        markup.add(types.KeyboardButton('📋 Plans'), types.KeyboardButton('🔙 Back'))
        users = self.db.get_all_users()
        pending = self.db.get_pending_payments()
        self.bot.send_message(m.chat.id, f"🛡️ **Admin Panel**\n\n👥 Users: {len(users)}\n💳 Pending: {len(pending)}\n\nSelect:", reply_markup=markup, parse_mode='Markdown')
    
    def run(self):
        logging.info("🚀 Starting bot...")
        try:
            self.bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            logging.error(f"❌ Bot stopped: {e}")
            raise

if __name__ == '__main__':
    app = BotApplication()
    app.run()