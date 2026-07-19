# bot.py - COMPLETE WITH CREDIT EXPIRY HANDLING
import os
import sys
import json
import sqlite3
import logging
import subprocess
import shutil
import tempfile
import time
import threading
import signal
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests
import telebot
from telebot import types

# ==================== CONFIG ====================
BOT_TOKEN = "8841804008:AAFxtLhtimDmarDFj-vPnXSibpwMpfx93to"
ADMIN_USER_ID = 7709767483
CREDIT_PRICE = 99
CREDIT_VALIDITY_DAYS = 15
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
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    screenshot_path TEXT,
                    utr TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    approved_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS credits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    credit_count INTEGER DEFAULT 0,
                    purchase_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expiry_date TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS deployments (
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
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT,
                    file_path TEXT,
                    uploaded_by INTEGER,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                INSERT OR IGNORE INTO settings (key, value) VALUES 
                ('welcome_text', '👋 Welcome to Bot Deployment Platform!'),
                ('welcome_image', ''),
                ('upi_id', 'admin@upi'),
                ('upi_name', 'Admin'),
                ('bank_name', 'Example Bank'),
                ('account_number', '1234567890'),
                ('ifsc_code', 'EXMP0001'),
                ('auto_approve', '1')
            ''')
            
            conn.commit()
    
    # ========== SETTINGS ==========
    def get_setting(self, key: str) -> Optional[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            result = cursor.fetchone()
            return result['value'] if result else None
    
    def update_setting(self, key: str, value: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?', (value, key))
            conn.commit()
    
    # ========== USERS ==========
    def get_or_create_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            user = cursor.fetchone()
            if not user:
                cursor.execute('INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
                               (user_id, username, first_name, last_name))
                conn.commit()
                return True
            return False
    
    def get_user(self, user_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_all_users(self) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users ORDER BY created_at DESC')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ========== PAYMENTS ==========
    def add_payment(self, user_id: int, amount: int, screenshot_path: str, utr: str = ""):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO payments (user_id, amount, screenshot_path, utr) VALUES (?, ?, ?, ?)',
                           (user_id, amount, screenshot_path, utr))
            conn.commit()
            return cursor.lastrowid
    
    def get_payment(self, payment_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_pending_payments(self) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT p.*, u.username, u.first_name, u.last_name 
                FROM payments p
                JOIN users u ON p.user_id = u.user_id
                WHERE p.status = 'pending'
                ORDER BY p.created_at ASC
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def approve_payment(self, payment_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM payments WHERE id = ? AND status = "pending"', (payment_id,))
            payment = cursor.fetchone()
            if not payment:
                return False
            cursor.execute('UPDATE payments SET status = "approved", approved_at = CURRENT_TIMESTAMP WHERE id = ?',
                           (payment_id,))
            expiry_date = datetime.now() + timedelta(days=CREDIT_VALIDITY_DAYS)
            cursor.execute('''
                INSERT INTO credits (user_id, credit_count, purchase_date, expiry_date)
                VALUES (?, 1, CURRENT_TIMESTAMP, ?)
            ''', (payment['user_id'], expiry_date.isoformat()))
            conn.commit()
            return True
    
    def reject_payment(self, payment_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE payments SET status = "rejected" WHERE id = ?', (payment_id,))
            conn.commit()
            return True
    
    # ========== CREDITS ==========
    def add_credits_manual(self, user_id: int, count: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            expiry_date = datetime.now() + timedelta(days=CREDIT_VALIDITY_DAYS)
            for _ in range(count):
                cursor.execute('''
                    INSERT INTO credits (user_id, credit_count, purchase_date, expiry_date)
                    VALUES (?, 1, CURRENT_TIMESTAMP, ?)
                ''', (user_id, expiry_date.isoformat()))
            conn.commit()
            return True
    
    def get_available_credits(self, user_id: int) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) as count FROM credits 
                WHERE user_id = ? AND is_active = 1 AND expiry_date > datetime('now')
            ''', (user_id,))
            result = cursor.fetchone()
            return result['count'] if result else 0
    
    def use_credit(self, user_id: int) -> Optional[int]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id FROM credits 
                WHERE user_id = ? AND is_active = 1 AND expiry_date > datetime('now')
                ORDER BY expiry_date ASC
                LIMIT 1
            ''', (user_id,))
            credit = cursor.fetchone()
            if not credit:
                return None
            cursor.execute('UPDATE credits SET is_active = 0 WHERE id = ?', (credit['id'],))
            conn.commit()
            return credit['id']
    
    def get_credit_expiry(self, user_id: int) -> Optional[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT expiry_date FROM credits 
                WHERE user_id = ? AND is_active = 1 AND expiry_date > datetime('now')
                ORDER BY expiry_date ASC
                LIMIT 1
            ''', (user_id,))
            result = cursor.fetchone()
            return result['expiry_date'] if result else None
    
    def get_all_credits(self) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT c.*, u.username, u.first_name, u.last_name 
                FROM credits c
                JOIN users u ON c.user_id = u.user_id
                ORDER BY c.created_at DESC
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_expired_credits(self) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM credits 
                WHERE is_active = 1 AND expiry_date <= datetime('now')
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def expire_credit(self, credit_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE credits SET is_active = 0 WHERE id = ?', (credit_id,))
            conn.commit()
    
    def get_deployments_by_credit(self, credit_id: int) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM deployments WHERE credit_id = ?', (credit_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ========== DEPLOYMENTS ==========
    def add_deployment(self, user_id: int, bot_token: str, bot_username: str, deployment_path: str, credit_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO deployments (user_id, bot_token, bot_username, deployment_path, credit_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, bot_token, bot_username, deployment_path, credit_id))
            conn.commit()
            return cursor.lastrowid
    
    def update_deployment_status(self, deployment_id: int, status: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE deployments SET status = ?, last_restart = CURRENT_TIMESTAMP WHERE id = ?',
                           (status, deployment_id))
            conn.commit()
    
    def get_user_deployments(self, user_id: int) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM deployments WHERE user_id = ? ORDER BY created_at DESC', (user_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def get_all_deployments(self) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT d.*, u.username, u.first_name, u.last_name 
                FROM deployments d
                JOIN users u ON d.user_id = u.user_id
                ORDER BY d.created_at DESC
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ========== TEMPLATES ==========
    def save_template(self, filename: str, file_path: str, user_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE templates SET is_active = 0 WHERE is_active = 1')
            cursor.execute('INSERT INTO templates (filename, file_path, uploaded_by) VALUES (?, ?, ?)',
                           (filename, file_path, user_id))
            conn.commit()
            return cursor.lastrowid
    
    def get_active_template(self) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM templates WHERE is_active = 1 ORDER BY uploaded_at DESC LIMIT 1')
            row = cursor.fetchone()
            return dict(row) if row else None
    
    # ========== LOGS ==========
    def add_log(self, user_id: int, action: str, details: str = None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO logs (user_id, action, details) VALUES (?, ?, ?)',
                           (user_id, action, details))
            conn.commit()
    
    def get_logs(self, limit: int = 50) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT l.*, u.username, u.first_name, u.last_name 
                FROM logs l
                LEFT JOIN users u ON l.user_id = u.user_id
                ORDER BY l.created_at DESC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

# ==================== DEPLOYMENT MANAGER ====================
class DeploymentManager:
    def __init__(self):
        self.deployments_path = Path(DEPLOYMENTS_PATH)
        self.deployments_path.mkdir(parents=True, exist_ok=True)
        self.db = Database()
        self.running_processes = {}
    
    def validate_bot_token(self, token: str) -> Tuple[bool, Optional[str]]:
        try:
            response = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    return True, data['result']['username']
            return False, None
        except:
            return False, None
    
    def get_template_content(self) -> Optional[str]:
        template = self.db.get_active_template()
        if not template:
            return None
        template_path = Path(template['file_path'])
        if not template_path.exists():
            return None
        with open(template_path, 'r') as f:
            return f.read()
    
    def deploy_bot(self, user_id: int, bot_token: str) -> Tuple[bool, str]:
        template_content = self.get_template_content()
        if not template_content:
            return False, "❌ No template available. Please contact admin."
        
        credits = self.db.get_available_credits(user_id)
        if credits <= 0:
            return False, "❌ No credits available! Please buy credits first."
        
        is_valid, bot_username = self.validate_bot_token(bot_token)
        if not is_valid:
            return False, "❌ Invalid bot token! Please provide a valid token from @BotFather."
        
        deployments = self.db.get_user_deployments(user_id)
        for dep in deployments:
            if dep['bot_token'] == bot_token and dep['status'] == 'running':
                return False, "⚠️ This bot is already deployed and running!"
        
        # Use credit
        credit_id = self.db.use_credit(user_id)
        if not credit_id:
            return False, "❌ Failed to use credit. Please try again."
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        deploy_folder = self.deployments_path / f"{user_id}_{timestamp}"
        deploy_folder.mkdir(parents=True, exist_ok=True)
        
        # Write bot.py
        bot_code = template_content.replace('YOUR_BOT_TOKEN', bot_token)
        bot_file = deploy_folder / 'bot.py'
        with open(bot_file, 'w') as f:
            f.write(bot_code)
        
        # Config
        config = {
            'user_id': user_id,
            'bot_token': bot_token,
            'bot_username': bot_username,
            'deployed_at': datetime.now().isoformat(),
            'status': 'running',
            'credit_id': credit_id
        }
        with open(deploy_folder / 'config.json', 'w') as f:
            json.dump(config, f, indent=2)
        
        log_file = deploy_folder / 'logs.txt'
        log_file.touch()
        
        deployment_id = self.db.add_deployment(user_id, bot_token, bot_username, str(deploy_folder), credit_id)
        
        # Start bot
        success = self.start_bot(deploy_folder, bot_username)
        if success:
            self.db.update_deployment_status(deployment_id, 'running')
            self.db.add_log(user_id, 'deploy', f'✅ Deployed bot @{bot_username}')
            return True, f"✅ Bot @{bot_username} deployed successfully!"
        else:
            self.db.update_deployment_status(deployment_id, 'failed')
            self.db.add_log(user_id, 'deploy_failed', f'Failed to deploy @{bot_username}')
            error_log = ""
            try:
                with open(log_file, 'r') as f:
                    content = f.read()
                    if content:
                        error_log = content[-500:]
            except:
                pass
            if error_log:
                return False, f"❌ Bot failed to start.\n\n📋 Error:\n```\n{error_log}\n```"
            else:
                return False, "❌ Bot failed to start. Check logs for details."
    
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
                    f.write(f"❌ ERROR: bot.py not found at {bot_file}\n")
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
                    preexec_fn=os.setsid if hasattr(os, 'setsid') else None
                )
            
            self.running_processes[str(deploy_path)] = process
            time.sleep(3)
            
            if process.poll() is None:
                logging.info(f"✅ Bot @{bot_username} started successfully at {deploy_path}")
                with open(log_file, 'a') as f:
                    f.write(f"✅ Bot started successfully at {datetime.now()}\n")
                return True
            else:
                logging.error(f"❌ Bot @{bot_username} failed to start at {deploy_path}")
                return False
        except Exception as e:
            logging.error(f"Failed to start bot: {e}")
            with open(deploy_path / 'logs.txt', 'a') as f:
                f.write(f"\n[ERROR] Failed to start bot: {e}\n")
            return False
    
    def stop_bot(self, deploy_path: Path) -> bool:
        try:
            path_str = str(deploy_path)
            if path_str in self.running_processes:
                process = self.running_processes[path_str]
                process.terminate()
                process.wait(timeout=5)
                del self.running_processes[path_str]
                return True
            return False
        except:
            return False

# ==================== BACKGROUND CHECKER ====================
class CreditExpiryChecker:
    def __init__(self, bot_app):
        self.bot_app = bot_app
        self.db = bot_app.db
        self.deployment_manager = bot_app.deployment_manager
        self.bot = bot_app.bot
        self.running = True
    
    def start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        logging.info("🔄 Credit expiry checker started")
    
    def _run(self):
        while self.running:
            try:
                self.check_expired_credits()
            except Exception as e:
                logging.error(f"Credit expiry check error: {e}")
            time.sleep(CHECK_INTERVAL)
    
    def check_expired_credits(self):
        expired = self.db.get_expired_credits()
        if not expired:
            return
        
        for credit in expired:
            credit_id = credit['id']
            user_id = credit['user_id']
            
            # Get deployments using this credit
            deployments = self.db.get_deployments_by_credit(credit_id)
            
            # Mark credit as inactive
            self.db.expire_credit(credit_id)
            
            # Stop each deployment
            for dep in deployments:
                deploy_path = Path(dep['deployment_path'])
                # Stop bot process
                self.deployment_manager.stop_bot(deploy_path)
                # Update status
                self.db.update_deployment_status(dep['id'], 'expired')
                # Log
                self.db.add_log(user_id, 'credit_expired', f'Bot @{dep["bot_username"]} stopped due to credit expiry')
            
            # Notify user
            user = self.db.get_user(user_id)
            if user:
                try:
                    msg = f"⏰ **Credit Expired!**\n\n"
                    msg += f"━━━━━━━━━━━━━━━━━━━\n"
                    msg += f"Your credit has expired on {credit['expiry_date'][:10]}.\n"
                    if deployments:
                        msg += f"\n🤖 **Bots Stopped:**\n"
                        for dep in deployments:
                            msg += f"   • @{dep['bot_username']}\n"
                    msg += f"\n💡 **Need to deploy again?**\n"
                    msg += f"Buy new credits using 💳 Buy Credits button."
                    self.bot.send_message(user_id, msg, parse_mode='Markdown')
                except Exception as e:
                    logging.error(f"Failed to notify user {user_id}: {e}")
            
            # Notify admin
            try:
                admin_msg = f"⏰ **Credit Expired**\n\n"
                admin_msg += f"👤 User: {user['first_name'] if user else 'Unknown'}\n"
                admin_msg += f"🆔 User ID: `{user_id}`\n"
                admin_msg += f"📅 Expiry: {credit['expiry_date']}\n"
                if deployments:
                    admin_msg += f"🤖 Bots stopped: {len(deployments)}"
                self.bot.send_message(ADMIN_USER_ID, admin_msg, parse_mode='Markdown')
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
        # Start credit expiry checker
        self.checker = CreditExpiryChecker(self)
        self.checker.start()
    
    def setup_logging(self):
        log_dir = Path(LOG_PATH)
        log_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / 'bot.log'),
                logging.StreamHandler()
            ]
        )
    
    def create_default_template(self):
        template_path = Path(TEMPLATE_PATH)
        if template_path.exists():
            return
        template_path.parent.mkdir(parents=True, exist_ok=True)
        default_template = '''import os
import sys
import logging
import telebot
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
        with open(template_path, 'w') as f:
            f.write(default_template)
        self.db.save_template('bot.py', str(template_path), self.admin_id)
        logging.info("✅ Default template created")
    
    def setup_handlers(self):
        @self.bot.message_handler(commands=['start'])
        def start_command(message):
            user_id = message.from_user.id
            self.db.get_or_create_user(user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
            welcome_text = self.db.get_setting('welcome_text')
            welcome_image = self.db.get_setting('welcome_image')
            
            if user_id == self.admin_id:
                if welcome_image:
                    try:
                        self.bot.send_photo(message.chat.id, open(welcome_image, 'rb'),
                                           caption=f"{welcome_text}\n\n🛡️ **Admin Panel Ready!**", parse_mode='Markdown')
                    except:
                        self.bot.send_message(message.chat.id, f"{welcome_text}\n\n🛡️ **Admin Panel Ready!**", parse_mode='Markdown')
                else:
                    self.bot.send_message(message.chat.id, f"{welcome_text}\n\n🛡️ **Admin Panel Ready!**", parse_mode='Markdown')
                self.show_admin_menu(message)
            else:
                if welcome_image:
                    try:
                        self.bot.send_photo(message.chat.id, open(welcome_image, 'rb'), caption=welcome_text, parse_mode='Markdown')
                    except:
                        self.bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown')
                else:
                    self.bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown')
                self.show_user_menu(message)
        
        # ========== USER MENU ==========
        @self.bot.message_handler(func=lambda m: m.text == '💳 Buy Credits')
        def buy_credits(message):
            user_id = message.from_user.id
            upi_id = self.db.get_setting('upi_id') or "admin@upi"
            upi_name = self.db.get_setting('upi_name') or "Admin"
            bank_name = self.db.get_setting('bank_name') or "Example Bank"
            account_number = self.db.get_setting('account_number') or "1234567890"
            ifsc_code = self.db.get_setting('ifsc_code') or "EXMP0001"
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton(f"🛒 Buy 1 Credit - ₹{CREDIT_PRICE}", callback_data='buy_credit'),
                types.InlineKeyboardButton("❌ Cancel", callback_data='cancel')
            )
            
            self.bot.send_message(
                message.chat.id,
                f"💳 **Buy Credits**\n\n"
                f"💰 **Price:** ₹{CREDIT_PRICE} per credit\n"
                f"📅 **Validity:** {CREDIT_VALIDITY_DAYS} days\n\n"
                f"🏦 **Payment Details:**\n"
                f"• UPI ID: `{upi_id}`\n"
                f"• Name: {upi_name}\n"
                f"• Bank: {bank_name}\n"
                f"• Account: `{account_number}`\n"
                f"• IFSC: `{ifsc_code}`\n\n"
                f"📸 After payment, click the button below to submit proof.\n"
                f"💬 Send UTR/Transaction ID with screenshot.",
                reply_markup=markup,
                parse_mode='Markdown'
            )
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'buy_credit')
        def buy_credit_callback(call):
            self.bot.edit_message_text(
                "📸 **Upload Payment Screenshot**\n\n"
                "Please send a screenshot of your payment confirmation.\n\n"
                "📝 **Important:** Send your UTR/Transaction ID in the caption.\n"
                "Example: `UTR: 1234567890`\n\n"
                "Send the image directly as a file.",
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )
            self.bot.register_next_step_handler(call.message, process_payment_screenshot)
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'cancel')
        def cancel_callback(call):
            self.bot.edit_message_text("❌ **Action Cancelled**\n\nYou can try again anytime.", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        
        def process_payment_screenshot(message):
            user_id = message.from_user.id
            if not message.photo:
                self.bot.send_message(message.chat.id, "❌ Please send a photo.", parse_mode='Markdown')
                return
            utr = ""
            if message.caption:
                utr_match = re.search(r'(?:UTR|utr|txn|TXN)[\s:]+([A-Za-z0-9]+)', message.caption)
                if utr_match:
                    utr = utr_match.group(1)
                else:
                    number_match = re.search(r'\b([0-9]{8,16})\b', message.caption)
                    if number_match:
                        utr = number_match.group(1)
            try:
                file_info = self.bot.get_file(message.photo[-1].file_id)
                file_path = f"payments/{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                downloaded_file = self.bot.download_file(file_info.file_path)
                with open(file_path, 'wb') as f:
                    f.write(downloaded_file)
                payment_id = self.db.add_payment(user_id, CREDIT_PRICE, file_path, utr)
                self.db.add_log(user_id, 'payment_submitted', f'Payment ID: {payment_id}, UTR: {utr}')
                
                auto_approve = self.db.get_setting('auto_approve') or '1'
                if auto_approve == '1':
                    if self.db.approve_payment(payment_id):
                        expiry = self.db.get_credit_expiry(user_id)
                        credits = self.db.get_available_credits(user_id)
                        approval_msg = (
                            f"✅ **PAYMENT APPROVED!** 🎉\n\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💳 **Payment Details**\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💰 Amount: ₹{CREDIT_PRICE}\n"
                            f"🆔 Payment ID: `{payment_id}`\n"
                            f"📝 UTR: `{utr or 'N/A'}`\n"
                            f"📅 Date: {datetime.now().strftime('%d-%m-%Y %H:%M')}\n\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💎 **Credit Details**\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💳 Credits Added: **1**\n"
                            f"📦 Total Credits: **{credits}**\n"
                            f"📅 Expiry: `{expiry or 'N/A'}`\n\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"🚀 **You can now deploy a bot!**\n"
                            f"Click the button below to start:\n\n"
                            f"⚠️ Your credit is valid for **{CREDIT_VALIDITY_DAYS} days**"
                        )
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        markup.add(types.InlineKeyboardButton("🚀 Deploy Your Bot Now!", callback_data="go_deploy"))
                        self.bot.send_message(user_id, approval_msg, reply_markup=markup, parse_mode='Markdown')
                        self.bot.send_message(message.chat.id, "✅ **Payment Auto-Approved!**\n\n💳 Credit added.", parse_mode='Markdown')
                        if self.admin_id:
                            self.bot.send_message(self.admin_id, f"✅ Payment Auto-Approved\nUser: {message.from_user.first_name}\nID: {user_id}\nAmount: ₹{CREDIT_PRICE}\nUTR: {utr}", parse_mode='Markdown')
                        return
                # Manual
                self.bot.send_message(message.chat.id, f"✅ **Payment Submitted!**\n🆔 ID: `{payment_id}`\n💰 ₹{CREDIT_PRICE}\n📝 UTR: `{utr or 'N/A'}`\n\n⏳ Wait for admin approval.", parse_mode='Markdown')
                if self.admin_id:
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    markup.add(types.InlineKeyboardButton('✅ Approve', callback_data=f'approve_payment_{payment_id}'),
                               types.InlineKeyboardButton('❌ Reject', callback_data=f'reject_payment_{payment_id}'))
                    self.bot.send_photo(self.admin_id, open(file_path, 'rb'),
                                       caption=f"💰 New Payment\nUser: {message.from_user.first_name}\nID: {user_id}\nAmount: ₹{CREDIT_PRICE}\nUTR: {utr}",
                                       reply_markup=markup, parse_mode='Markdown')
            except Exception as e:
                logging.error(f"Payment error: {e}")
                self.bot.send_message(message.chat.id, "❌ Failed to process payment. Try again.", parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'go_deploy')
        def go_deploy_callback(call):
            user_id = call.from_user.id
            credits = self.db.get_available_credits(user_id)
            if credits <= 0:
                self.bot.answer_callback_query(call.id, "❌ No credits!", show_alert=True)
                return
            self.bot.edit_message_text(f"🚀 **Deployment Process**\n\n📝 Step 1: Send bot token.\n\n💳 You have {credits} credit(s).\n\n🤖 Send token from @BotFather:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(call.message, process_bot_token)
        
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('approve_payment_'))
        def approve_payment(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            payment_id = int(call.data.split('_')[2])
            if self.db.approve_payment(payment_id):
                payment = self.db.get_payment(payment_id)
                user_id = payment['user_id']
                utr = payment.get('utr', 'N/A')
                credits = self.db.get_available_credits(user_id)
                expiry = self.db.get_credit_expiry(user_id)
                approval_msg = (
                    f"✅ **PAYMENT APPROVED!** 🎉\n\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💳 Payment Details\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 Amount: ₹{CREDIT_PRICE}\n"
                    f"🆔 Payment ID: `{payment_id}`\n"
                    f"📝 UTR: `{utr}`\n"
                    f"📅 Date: {datetime.now().strftime('%d-%m-%Y %H:%M')}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 Credit Details\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"💳 Credits Added: **1**\n"
                    f"📦 Total Credits: **{credits}**\n"
                    f"📅 Expiry: `{expiry or 'N/A'}`\n\n"
                    f"🚀 Click below to deploy:\n"
                    f"⚠️ Valid for {CREDIT_VALIDITY_DAYS} days"
                )
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(types.InlineKeyboardButton("🚀 Deploy Your Bot Now!", callback_data="go_deploy"))
                self.bot.send_message(user_id, approval_msg, reply_markup=markup, parse_mode='Markdown')
                self.bot.edit_message_caption(f"✅ **Payment Approved!**\n\n{call.message.caption}", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
                self.bot.send_message(call.message.chat.id, "✅ Payment approved! User notified.", parse_mode='Markdown')
                self.db.add_log(user_id, 'payment_approved', f'Payment ID: {payment_id}')
                self.bot.answer_callback_query(call.id, "✅ Approved!")
            else:
                self.bot.answer_callback_query(call.id, "❌ Failed!", show_alert=True)
        
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('reject_payment_'))
        def reject_payment(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            payment_id = int(call.data.split('_')[2])
            if self.db.reject_payment(payment_id):
                payment = self.db.get_payment(payment_id)
                user_id = payment['user_id']
                reject_msg = f"❌ **PAYMENT REJECTED**\n\n💰 Amount: ₹{CREDIT_PRICE}\n🆔 ID: `{payment_id}`\n\n❌ Reason: Verification failed.\n\nTry again with correct payment."
                self.bot.send_message(user_id, reject_msg, parse_mode='Markdown')
                self.bot.edit_message_caption(f"❌ **Payment Rejected!**\n\n{call.message.caption}", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
                self.bot.send_message(call.message.chat.id, "❌ Payment rejected.", parse_mode='Markdown')
                self.db.add_log(user_id, 'payment_rejected', f'Payment ID: {payment_id}')
                self.bot.answer_callback_query(call.id, "❌ Rejected!")
            else:
                self.bot.answer_callback_query(call.id, "❌ Failed!", show_alert=True)
        
        @self.bot.message_handler(func=lambda m: m.text == '🚀 Deploy Bot')
        def deploy_bot(message):
            user_id = message.from_user.id
            credits = self.db.get_available_credits(user_id)
            if credits <= 0:
                self.bot.send_message(message.chat.id,
                    f"❌ **No Credits!**\n\nEach deployment needs 1 credit.\n💰 ₹{CREDIT_PRICE}/credit\n📅 Valid {CREDIT_VALIDITY_DAYS} days\n\nBuy credits now.", parse_mode='Markdown')
                return
            self.bot.send_message(message.chat.id, f"🚀 **Deployment**\n\nStep 1: Send bot token.\n\n💳 You have {credits} credit(s).\n\nSend token from @BotFather:", parse_mode='Markdown')
            self.bot.register_next_step_handler(message, process_bot_token)
        
        def process_bot_token(message):
            user_id = message.from_user.id
            bot_token = message.text.strip()
            msg = self.bot.send_message(message.chat.id, "⏳ Validating and deploying...", parse_mode='Markdown')
            success, result = self.deployment_manager.deploy_bot(user_id, bot_token)
            if success:
                self.bot.edit_message_text(f"✅ {result}", msg.chat.id, msg.message_id, parse_mode='Markdown')
                self.bot.send_message(message.chat.id, "🚀 Bot running!\n📦 Manage from My Deployments.", parse_mode='Markdown')
            else:
                self.bot.edit_message_text(f"❌ {result}", msg.chat.id, msg.message_id, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '📦 My Deployments')
        def my_deployments(message):
            user_id = message.from_user.id
            deployments = self.db.get_user_deployments(user_id)
            if not deployments:
                self.bot.send_message(message.chat.id, "📦 No deployments found.\n🚀 Deploy your first bot!", parse_mode='Markdown')
                return
            response = "📦 **Your Deployments**\n\n"
            for i, dep in enumerate(deployments[:5], 1):
                status_emoji = "🟢" if dep['status'] == 'running' else "🔴" if dep['status'] == 'expired' else "⚪"
                response += f"{i}. {status_emoji} @{dep['bot_username']}\n"
                response += f"   📊 Status: **{dep['status']}**\n"
                response += f"   📅 Deployed: {dep['created_at'][:10]}\n"
                response += f"   🆔 ID: `{dep['id']}`\n\n"
            if len(deployments) > 5:
                response += f"📊 Total: {len(deployments)} deployments\n"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '👤 Profile')
        def profile(message):
            user_id = message.from_user.id
            credits = self.db.get_available_credits(user_id)
            expiry = self.db.get_credit_expiry(user_id)
            deployments = self.db.get_user_deployments(user_id)
            running = len([d for d in deployments if d['status'] == 'running'])
            response = f"👤 **Profile**\n\n📛 Name: {message.from_user.first_name}\n👤 @{message.from_user.username or 'N/A'}\n🆔 `{user_id}`\n\n💳 Credits: {credits}\n📅 Expiry: {expiry or 'No credits'}\n🤖 Running Bots: {running}\n📦 Deployments: {len(deployments)}\n\n💰 Price: ₹{CREDIT_PRICE}/credit\n📅 Validity: {CREDIT_VALIDITY_DAYS} days"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        # ========== ADMIN HANDLERS ==========
        @self.bot.message_handler(func=lambda m: m.text == '📤 Upload Template')
        def upload_template(message):
            if message.from_user.id != self.admin_id:
                return
            self.bot.send_message(message.chat.id, "📤 Send **bot.py** file with `YOUR_BOT_TOKEN` placeholder.", parse_mode='Markdown')
            self.bot.register_next_step_handler(message, process_template_upload)
        
        def process_template_upload(message):
            if message.from_user.id != self.admin_id:
                return
            if not message.document:
                self.bot.send_message(message.chat.id, "❌ Send a file.", parse_mode='Markdown')
                return
            if not message.document.file_name.endswith('.py'):
                self.bot.send_message(message.chat.id, "❌ Must be .py file.", parse_mode='Markdown')
                return
            try:
                file_info = self.bot.get_file(message.document.file_id)
                file_path = os.path.join("templates", message.document.file_name)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                downloaded = self.bot.download_file(file_info.file_path)
                content = downloaded.decode('utf-8')
                if 'YOUR_BOT_TOKEN' not in content:
                    self.bot.send_message(message.chat.id, "❌ Missing `YOUR_BOT_TOKEN`.", parse_mode='Markdown')
                    return
                with open(file_path, 'wb') as f:
                    f.write(downloaded)
                template_id = self.db.save_template(message.document.file_name, file_path, message.from_user.id)
                self.db.add_log(message.from_user.id, 'template_upload', f'Template ID: {template_id}')
                self.bot.send_message(message.chat.id, f"✅ Template uploaded!\nFile: `{message.document.file_name}`\nStatus: Active", parse_mode='Markdown')
            except Exception as e:
                self.bot.send_message(message.chat.id, f"❌ Error: {str(e)}", parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '📄 Current Template')
        def current_template(message):
            if message.from_user.id != self.admin_id:
                return
            template = self.db.get_active_template()
            if not template:
                self.bot.send_message(message.chat.id, "❌ No template active.", parse_mode='Markdown')
                return
            response = f"📄 **Current Template**\n\nFilename: `{template['filename']}`\nUploaded: {template['uploaded_at']}\nStatus: ✅ Active"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
            try:
                with open(template['file_path'], 'rb') as f:
                    self.bot.send_document(message.chat.id, f)
            except:
                pass
        
        @self.bot.message_handler(func=lambda m: m.text == '💳 Payments')
        def payments_admin(message):
            if message.from_user.id != self.admin_id:
                return
            payments = self.db.get_pending_payments()
            if not payments:
                self.bot.send_message(message.chat.id, "💳 No pending payments.", parse_mode='Markdown')
                return
            response = f"💳 **Pending Payments** ({len(payments)})\n\n"
            for p in payments[:10]:
                response += f"🆔 `{p['id']}`\n👤 {p['first_name']} (@{p['username'] or 'N/A'})\n💰 ₹{p['amount']}\n📝 UTR: `{p.get('utr', 'N/A')}`\n📅 {p['created_at']}\n───\n"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '👥 Users')
        def users_admin(message):
            if message.from_user.id != self.admin_id:
                return
            users = self.db.get_all_users()
            response = f"👥 **Users** ({len(users)})\n\n"
            for u in users[:10]:
                credits = self.db.get_available_credits(u['user_id'])
                response += f"🆔 `{u['user_id']}`\n📛 {u['first_name'] or 'N/A'}\n👤 @{u['username'] or 'N/A'}\n💳 {credits} credits\n📅 {u['created_at']}\n───\n"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '💎 Credits')
        def credits_admin(message):
            if message.from_user.id != self.admin_id:
                return
            credits = self.db.get_all_credits()
            response = f"💎 **All Credits** ({len(credits)})\n\n"
            for c in credits[:10]:
                response += f"👤 {c['first_name'] or 'N/A'}\n💳 {c['credit_count']}\n📅 Expiry: {c['expiry_date']}\n📊 {'✅ Active' if c['is_active'] else '❌ Expired'}\n───\n"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '🤖 Deployments')
        def deployments_admin(message):
            if message.from_user.id != self.admin_id:
                return
            deployments = self.db.get_all_deployments()
            response = f"🤖 **All Deployments** ({len(deployments)})\n\n"
            for d in deployments[:10]:
                status_emoji = "🟢" if d['status'] == 'running' else "🔴" if d['status'] == 'expired' else "⚪"
                response += f"👤 {d['first_name'] or 'N/A'}\n🤖 @{d['bot_username']}\n📊 {status_emoji} {d['status']}\n📅 {d['created_at']}\n───\n"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '📋 Logs')
        def logs_admin(message):
            if message.from_user.id != self.admin_id:
                return
            logs = self.db.get_logs(20)
            if not logs:
                self.bot.send_message(message.chat.id, "📋 No logs.", parse_mode='Markdown')
                return
            response = f"📋 **Recent Logs** ({len(logs)})\n\n"
            for log in logs:
                response += f"📅 {log['created_at']}\n👤 @{log.get('username') or 'N/A'}\n📝 {log['action']}\n{log['details'] or ''}\n───\n"
            self.bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '🖼️ Welcome Settings')
        def welcome_settings(message):
            if message.from_user.id != self.admin_id:
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton('📝 Change Welcome Text', callback_data='change_welcome_text'),
                       types.InlineKeyboardButton('🖼️ Change Welcome Image', callback_data='change_welcome_image'),
                       types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            current_text = self.db.get_setting('welcome_text')
            current_image = self.db.get_setting('welcome_image')
            self.bot.send_message(message.chat.id, f"🖼️ **Welcome Settings**\n\n📝 Text: {current_text}\n🖼️ Image: {'✅ Set' if current_image else '❌ Not Set'}", reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '💰 UPI Settings')
        def upi_settings(message):
            if message.from_user.id != self.admin_id:
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton('🏦 UPI ID', callback_data='edit_upi_id'),
                       types.InlineKeyboardButton('📛 UPI Name', callback_data='edit_upi_name'),
                       types.InlineKeyboardButton('🏛️ Bank Name', callback_data='edit_bank_name'),
                       types.InlineKeyboardButton('🔢 Account Number', callback_data='edit_account_number'),
                       types.InlineKeyboardButton('📇 IFSC Code', callback_data='edit_ifsc_code'),
                       types.InlineKeyboardButton('🤖 Auto Approve', callback_data='toggle_auto_approve'),
                       types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            upi_id = self.db.get_setting('upi_id') or "Not Set"
            upi_name = self.db.get_setting('upi_name') or "Not Set"
            bank_name = self.db.get_setting('bank_name') or "Not Set"
            account_number = self.db.get_setting('account_number') or "Not Set"
            ifsc_code = self.db.get_setting('ifsc_code') or "Not Set"
            auto_approve = self.db.get_setting('auto_approve') or '1'
            self.bot.send_message(message.chat.id,
                f"💰 **UPI Settings**\n\n🏦 UPI: `{upi_id}`\n📛 Name: {upi_name}\n🏛️ Bank: {bank_name}\n🔢 Account: `{account_number}`\n📇 IFSC: `{ifsc_code}`\n🤖 Auto Approve: {'✅ ON' if auto_approve == '1' else '❌ OFF'}",
                reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '➕ Add Credits')
        def add_credits_menu(message):
            if message.from_user.id != self.admin_id:
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton('👥 Select User', callback_data='add_credits_select_user'),
                       types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            self.bot.send_message(message.chat.id, "➕ **Add Credits Manually**\n\nSelect a user to add credits.", reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'add_credits_select_user')
        def add_credits_select_user(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            users = self.db.get_all_users()
            if not users:
                self.bot.edit_message_text("❌ No users found.", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            for u in users[:20]:
                name = u.get('first_name', 'Unknown')
                uname = u.get('username', '')
                label = f"{name} (@{uname})" if uname else name
                markup.add(types.InlineKeyboardButton(label, callback_data=f"add_credits_user_{u['user_id']}"))
            markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_admin'))
            self.bot.edit_message_text("👥 **Select User**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('add_credits_user_'))
        def add_credits_user_selected(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            user_id = int(call.data.split('_')[3])
            # Ask for number of credits
            self.bot.edit_message_text(f"👤 **User ID:** `{user_id}`\n\n📝 Enter number of credits to add:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(call.message, process_add_credits_amount, user_id)
        
        def process_add_credits_amount(message, user_id):
            if message.from_user.id != self.admin_id:
                return
            try:
                count = int(message.text.strip())
                if count <= 0:
                    self.bot.send_message(message.chat.id, "❌ Enter a positive number.", parse_mode='Markdown')
                    return
                # Add credits
                self.db.add_credits_manual(user_id, count)
                self.db.add_log(self.admin_id, 'manual_credit_add', f'Added {count} credits to user {user_id}')
                # Notify user
                user = self.db.get_user(user_id)
                if user:
                    try:
                        expiry = self.db.get_credit_expiry(user_id)
                        total = self.db.get_available_credits(user_id)
                        self.bot.send_message(user_id,
                            f"✅ **Credits Added!**\n\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💳 **{count} credit(s)** added by admin.\n"
                            f"📦 Total Credits: **{total}**\n"
                            f"📅 Expiry: `{expiry or 'N/A'}`\n\n"
                            f"🚀 You can now deploy bots!\n"
                            f"Valid for {CREDIT_VALIDITY_DAYS} days.",
                            parse_mode='Markdown')
                    except:
                        pass
                self.bot.send_message(message.chat.id, f"✅ Added **{count}** credit(s) to user `{user_id}`.", parse_mode='Markdown')
                self.show_admin_menu(message)
            except ValueError:
                self.bot.send_message(message.chat.id, "❌ Invalid number. Enter a number.", parse_mode='Markdown')
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'back_admin')
        def back_admin(call):
            if call.from_user.id != self.admin_id:
                return
            self.bot.edit_message_text("🔙 Back", call.message.chat.id, call.message.message_id)
            self.show_admin_menu(call.message)
        
        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('edit_'))
        def edit_upi_field(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            field = call.data.replace('edit_', '')
            self.bot.edit_message_text(f"✏️ Send new value for {field.replace('_', ' ').title()}:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(call.message, process_upi_edit, field)
        
        def process_upi_edit(message, field):
            if message.from_user.id != self.admin_id:
                return
            self.db.update_setting(field, message.text.strip())
            self.db.add_log(self.admin_id, 'settings', f'Updated {field}')
            self.bot.send_message(message.chat.id, f"✅ {field.replace('_', ' ').title()} updated!", parse_mode='Markdown')
            self.show_admin_menu(message)
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'toggle_auto_approve')
        def toggle_auto_approve(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            current = self.db.get_setting('auto_approve') or '1'
            new = '0' if current == '1' else '1'
            self.db.update_setting('auto_approve', new)
            self.db.add_log(self.admin_id, 'settings', f'Auto approve: {new}')
            self.bot.edit_message_text(f"✅ Auto Approve turned {'ON' if new == '1' else 'OFF'}", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            self.bot.answer_callback_query(call.id, f"Auto Approve {'ON' if new == '1' else 'OFF'}")
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'change_welcome_text')
        def change_welcome_text(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            self.bot.edit_message_text("📝 Send new welcome text:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(call.message, process_welcome_text)
        
        def process_welcome_text(message):
            if message.from_user.id != self.admin_id:
                return
            self.db.update_setting('welcome_text', message.text)
            self.db.add_log(self.admin_id, 'settings', 'Welcome text updated')
            self.bot.send_message(message.chat.id, "✅ Welcome text updated!", parse_mode='Markdown')
            self.show_admin_menu(message)
        
        @self.bot.callback_query_handler(func=lambda call: call.data == 'change_welcome_image')
        def change_welcome_image(call):
            if call.from_user.id != self.admin_id:
                self.bot.answer_callback_query(call.id, "⛔ Unauthorized!", show_alert=True)
                return
            self.bot.edit_message_text("🖼️ Send photo for welcome image:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            self.bot.register_next_step_handler(call.message, process_welcome_image)
        
        def process_welcome_image(message):
            if message.from_user.id != self.admin_id:
                return
            if not message.photo:
                self.bot.send_message(message.chat.id, "❌ Send a photo.", parse_mode='Markdown')
                return
            try:
                file_info = self.bot.get_file(message.photo[-1].file_id)
                file_path = "settings/welcome_image.jpg"
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                downloaded = self.bot.download_file(file_info.file_path)
                with open(file_path, 'wb') as f:
                    f.write(downloaded)
                self.db.update_setting('welcome_image', file_path)
                self.db.add_log(self.admin_id, 'settings', 'Welcome image updated')
                self.bot.send_message(message.chat.id, "✅ Welcome image updated!", parse_mode='Markdown')
                self.show_admin_menu(message)
            except Exception as e:
                self.bot.send_message(message.chat.id, f"❌ Error: {e}", parse_mode='Markdown')
        
        @self.bot.message_handler(func=lambda m: m.text == '🔙 Back')
        def back_to_main(message):
            if message.from_user.id == self.admin_id:
                self.show_admin_menu(message)
            else:
                self.show_user_menu(message)
    
    def show_user_menu(self, message):
        markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(types.KeyboardButton('💳 Buy Credits'), types.KeyboardButton('🚀 Deploy Bot'))
        markup.add(types.KeyboardButton('📦 My Deployments'), types.KeyboardButton('👤 Profile'))
        self.bot.send_message(message.chat.id, "🤖 **Bot Deployment Platform**\n\nChoose an option:", reply_markup=markup, parse_mode='Markdown')
    
    def show_admin_menu(self, message):
        markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(types.KeyboardButton('📤 Upload Template'), types.KeyboardButton('📄 Current Template'))
        markup.add(types.KeyboardButton('💳 Payments'), types.KeyboardButton('👥 Users'))
        markup.add(types.KeyboardButton('💎 Credits'), types.KeyboardButton('🤖 Deployments'))
        markup.add(types.KeyboardButton('📋 Logs'), types.KeyboardButton('🖼️ Welcome Settings'))
        markup.add(types.KeyboardButton('💰 UPI Settings'), types.KeyboardButton('➕ Add Credits'))
        markup.add(types.KeyboardButton('🔙 Back'))
        stats = self.db.get_all_users()
        pending = self.db.get_pending_payments()
        self.bot.send_message(message.chat.id,
            f"🛡️ **Admin Panel**\n\n📊 Stats:\n👥 Users: {len(stats)}\n💳 Pending Payments: {len(pending)}\n\nSelect option:",
            reply_markup=markup, parse_mode='Markdown')
    
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