import os
import sqlite3
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import Image as RLImage
from PIL import Image as PILImage, ImageDraw, ImageFont
import io

# ============ বাংলা টেক্সট রেন্ডারিং (PDF রিপোর্টে সঠিক বাংলা লেখার জন্য) ============
# reportlab নিজে জটিল স্ক্রিপ্ট (বাংলা conjunct/matra reordering) শেপ করতে পারে না,
# তাই Pillow-এর raqm লেআউট ইঞ্জিন দিয়ে সঠিকভাবে শেপ করা টেক্সট ছবি হিসেবে PDF-এ বসানো হয়
_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
_BN_REGULAR = os.path.join(_FONT_DIR, 'HindSiliguri-Regular.ttf')
_BN_BOLD = os.path.join(_FONT_DIR, 'HindSiliguri-Medium.ttf')
_BN_FONTS_OK = os.path.exists(_BN_REGULAR) and os.path.exists(_BN_BOLD)

if _BN_FONTS_OK:
    print(f"✅ বাংলা ফন্ট পাওয়া গেছে: {_FONT_DIR}")
else:
    print(f"⚠️⚠️⚠️ বাংলা ফন্ট পাওয়া যায়নি! পাথ চেক করা হয়েছে: {_FONT_DIR}")
    print(f"⚠️ PDF রিপোর্টে বাংলা টেক্সট বক্স/■ দেখাবে। fonts/HindSiliguri-Regular.ttf ও fonts/HindSiliguri-Medium.ttf রিপোতে আছে কিনা যাচাই করুন।")

def bn_text(text, size=11, bold=False, color=(0, 0, 0)):
    """বাংলা/মিশ্র টেক্সটকে সঠিকভাবে শেপ করে reportlab Image flowable হিসেবে রিটার্ন করে।"""
    if not text:
        text = " "
    if not _BN_FONTS_OK:
        # ফন্ট না পাওয়া গেলে সাধারণ Paragraph এ fallback
        styles = getSampleStyleSheet()
        st = ParagraphStyle('fallback', parent=styles['Normal'], fontSize=size,
                             fontName='Helvetica-Bold' if bold else 'Helvetica')
        return Paragraph(text, st)
    
    scale = 4
    px_size = size * scale
    font_path = _BN_BOLD if bold else _BN_REGULAR
    font = ImageFont.truetype(font_path, px_size, layout_engine=ImageFont.Layout.RAQM)
    
    tmp = PILImage.new("RGBA", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp)
    bbox = tmp_draw.textbbox((0, 0), text, font=font)
    w = max(bbox[2] - bbox[0] + 8, 1)
    h = max(bbox[3] - bbox[1] + 8, 1)
    
    img = PILImage.new("RGBA", (w, h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.text((-bbox[0] + 4, -bbox[1] + 4), text, font=font, fill=color + (255,))
    
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    png_buf.seek(0)
    
    disp_h = size * 1.15
    disp_w = disp_h * (w / h)
    return RLImage(png_buf, width=disp_w, height=disp_h)

# ============ ডেটাবেস পাথ ============
DB_PATH = '/app/data/mess.db' if os.path.exists('/app/data') else 'mess.db'

# ============ ডেটাবেস ফাংশন ============
def init_db():
    os.makedirs(os.path.dirname(DB_PATH) if '/' in DB_PATH else '.', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("PRAGMA table_info(mess_settings)")
    columns = [col[1] for col in c.fetchall()]
    
    if not columns:
        c.execute('''CREATE TABLE mess_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            start_date TEXT,
            end_date TEXT,
            month_name TEXT
        )''')
        columns = ['key', 'value', 'start_date', 'end_date', 'month_name']
    
    if 'start_date' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN start_date TEXT")
    if 'end_date' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN end_date TEXT")
    if 'month_name' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN month_name TEXT")
    if 'mess_type' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN mess_type TEXT")
    if 'default_mill_count' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN default_mill_count REAL")
    if 'creator_user_id' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN creator_user_id INTEGER")
    if 'type_ordinal' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN type_ordinal INTEGER")
    if 'default_breakfast' not in columns:
        c.execute("ALTER TABLE mess_settings ADD COLUMN default_breakfast REAL")
    conn.commit()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        full_name TEXT,
        mess_id INTEGER,
        added_date TEXT
    )''')
    
    c.execute("PRAGMA table_info(users)")
    user_columns = [col[1] for col in c.fetchall()]
    if 'user_id' not in user_columns:
        c.execute("ALTER TABLE users ADD COLUMN user_id INTEGER")
    
    c.execute('''CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        amount REAL,
        date TEXT,
        note TEXT,
        mess_id INTEGER
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        description TEXT,
        amount REAL,
        date TEXT,
        added_by TEXT,
        mess_id INTEGER
    )''')
    
    c.execute("PRAGMA table_info(admins)")
    admin_columns = [col[1] for col in c.fetchall()]
    if admin_columns and 'mess_id' not in admin_columns:
        # আগের (গ্লোবাল) এডমিন টেবিল থেকে নতুন per-mess গঠনে মাইগ্রেট
        c.execute("ALTER TABLE admins RENAME TO admins_old")
        c.execute('''CREATE TABLE admins (
            user_id INTEGER,
            mess_id INTEGER,
            username TEXT,
            added_date TEXT,
            PRIMARY KEY (user_id, mess_id)
        )''')
        c.execute("DROP TABLE admins_old")
    else:
        c.execute('''CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER,
            mess_id INTEGER,
            username TEXT,
            added_date TEXT,
            PRIMARY KEY (user_id, mess_id)
        )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_state (
        user_id INTEGER PRIMARY KEY,
        current_mess_id INTEGER
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS mills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mess_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        mill_date TEXT NOT NULL,
        mill_count REAL NOT NULL DEFAULT 1,
        UNIQUE(mess_id, username, mill_date)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS meal_presets (
        mess_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        breakfast REAL NOT NULL DEFAULT 1,
        lunch REAL NOT NULL DEFAULT 1,
        dinner REAL NOT NULL DEFAULT 1,
        PRIMARY KEY (mess_id, username)
    )''')
    conn.commit()
    
    # পুরনো মেস (creator_user_id/type_ordinal ছাড়া) একবারই ব্যাকফিল করা হয়
    c.execute("SELECT key FROM mess_settings WHERE key LIKE 'mess_%_info' AND (creator_user_id IS NULL OR type_ordinal IS NULL)")
    legacy_keys = [r[0] for r in c.fetchall()]
    if legacy_keys:
        for key in legacy_keys:
            mess_id_legacy = int(key.split('_')[1])
            c.execute("SELECT user_id FROM admins WHERE mess_id = ? ORDER BY user_id LIMIT 1", (mess_id_legacy,))
            admin_row = c.fetchone()
            creator = admin_row[0] if admin_row else 0
            c.execute("UPDATE mess_settings SET creator_user_id = COALESCE(creator_user_id, ?) WHERE key = ?", (creator, key))
        conn.commit()
        
        c.execute("""SELECT key, creator_user_id, mess_type FROM mess_settings
                     WHERE key LIKE 'mess_%_info'
                     ORDER BY CAST(SUBSTR(key, 6, LENGTH(key)-10) AS INTEGER)""")
        all_rows = c.fetchall()
        counters = {}
        for key, creator, mtype in all_rows:
            mtype = mtype or 'simple'
            counters[(creator, mtype)] = counters.get((creator, mtype), 0) + 1
            c.execute("UPDATE mess_settings SET type_ordinal = COALESCE(type_ordinal, ?) WHERE key = ?", (counters[(creator, mtype)], key))
        conn.commit()
    
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM mess_settings WHERE key = ?", (key,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO mess_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_current_mess_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT current_mess_id FROM user_state WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def set_current_mess_id(user_id, mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_state (user_id, current_mess_id) VALUES (?, ?)", (user_id, mess_id))
    conn.commit()
    conn.close()

def get_mess_info(mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("SELECT start_date, end_date, month_name, mess_type, default_mill_count, creator_user_id, type_ordinal, default_breakfast FROM mess_settings WHERE key = ?", (f'mess_{mess_id}_info',))
        result = c.fetchone()
        conn.close()
        if result:
            return {
                'start_date': result[0] or 'অজানা',
                'end_date': result[1] or 'চলমান',
                'month_name': result[2] or 'অজানা',
                'mess_type': result[3] or 'simple',
                'default_mill_count': result[4] if result[4] is not None else 1.0,
                'creator_user_id': result[5],
                'type_ordinal': result[6],
                'default_breakfast': result[7] if result[7] is not None else 1.0
            }
        return None
    except:
        conn.close()
        return None

def save_mess_info(mess_id, start_date, end_date, month_name, mess_type='simple', default_mill_count=1.0, creator_user_id=None, type_ordinal=None, default_breakfast=1.0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO mess_settings
                 (key, value, start_date, end_date, month_name, mess_type, default_mill_count, creator_user_id, type_ordinal, default_breakfast)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (f'mess_{mess_id}_info', f'{start_date}|{end_date}|{month_name}', start_date, end_date, month_name,
               mess_type, default_mill_count, creator_user_id, type_ordinal, default_breakfast))
    conn.commit()
    conn.close()

def get_next_type_ordinal(creator_user_id, mess_type):
    """এই ইউজার এর আগে যে কয়টা এই ধরনের (type) মেস শুরু করেছে, তার পরের নম্বর"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM mess_settings WHERE key LIKE 'mess_%_info' AND creator_user_id = ? AND mess_type = ?",
              (creator_user_id, mess_type))
    count = c.fetchone()[0]
    conn.close()
    return count + 1

MESS_TYPE_SHORT = {'simple': 'Normal', 'student': 'Student', 'personal': 'Personal'}

def mess_display_label(mess_info):
    """ইউজারকে দেখানোর জন্য - যেমন 'Student মেস #1' - যাতে প্রতিটা ইউজার নিজের মতো ১ থেকে শুরু করা মেস নম্বর দেখে"""
    mess_type = mess_info.get('mess_type', 'simple') if mess_info else 'simple'
    ordinal = mess_info.get('type_ordinal') if mess_info else None
    short = MESS_TYPE_SHORT.get(mess_type, mess_type)
    if ordinal:
        return f"{short} মেস #{ordinal}"
    return f"{short} মেস"

def get_all_messes():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, start_date, end_date, month_name, mess_type, type_ordinal FROM mess_settings WHERE key LIKE 'mess_%_info'")
    results = c.fetchall()
    conn.close()
    
    messes = []
    for key, start_date, end_date, month_name, mess_type, type_ordinal in results:
        mess_id = key.split('_')[1]
        messes.append({
            'id': int(mess_id),
            'start_date': start_date or 'অজানা',
            'end_date': end_date or 'চলমান',
            'month_name': month_name or 'অজানা',
            'mess_type': mess_type or 'simple',
            'type_ordinal': type_ordinal
        })
    return sorted(messes, key=lambda x: x['id'], reverse=True)

def get_user_messes(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT DISTINCT mess_id FROM admins WHERE user_id = ?
                 UNION
                 SELECT DISTINCT mess_id FROM users WHERE user_id = ?""", (user_id, user_id))
    mess_ids = [row[0] for row in c.fetchall()]
    conn.close()
    
    messes = []
    for mid in mess_ids:
        info = get_mess_info(mid)
        if info:
            info['id'] = mid
            messes.append(info)
    return sorted(messes, key=lambda x: x['id'], reverse=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, full_name FROM users WHERE mess_id = ?", (mess_id,))
    users = c.fetchall()
    conn.close()
    return users

def get_users(mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, full_name FROM users WHERE mess_id = ?", (mess_id,))
    users = c.fetchall()
    conn.close()
    return users

def get_users_with_ids(mess_id):
    """(username, user_id) - শুধু যাদের telegram user_id লিংক করা আছে (তাদেরই DM পাঠানো সম্ভব)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, user_id FROM users WHERE mess_id = ? AND user_id IS NOT NULL", (mess_id,))
    users = c.fetchall()
    conn.close()
    return users

def add_user(username, mess_id, full_name=None, user_id=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, full_name, mess_id, added_date, user_id) VALUES (?, ?, ?, ?, ?)", 
                 (username, full_name or username, mess_id, datetime.now().strftime("%Y-%m-%d %H:%M"), user_id))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def link_user_id(username, user_id):
    # কোনো এডমিন @username দিয়ে যোগ করা ইউজার এখন প্রথমবার /start দিলে
    # তার আসল টেলিগ্রাম আইডি users টেবিলে যুক্ত করে দেয়
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET user_id = ? WHERE username = ? AND user_id IS NULL", (user_id, username))
    conn.commit()
    conn.close()

def remove_user(username, mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username = ? AND mess_id = ?", (username, mess_id))
    conn.commit()
    conn.close()

# ============ এডমিন ফাংশন (per-mess) ============
def is_admin(user_id, mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM admins WHERE user_id = ? AND mess_id = ?", (user_id, mess_id))
    result = c.fetchone()
    conn.close()
    return result is not None

def has_any_admin(mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM admins WHERE mess_id = ?", (mess_id,))
    count = c.fetchone()[0]
    conn.close()
    return count > 0

def add_admin(user_id, mess_id, username=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT OR REPLACE INTO admins (user_id, mess_id, username, added_date) VALUES (?, ?, ?, ?)",
                  (user_id, mess_id, username, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def remove_admin(user_id, mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id = ? AND mess_id = ?", (user_id, mess_id))
    conn.commit()
    conn.close()

def get_admins(mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username FROM admins WHERE mess_id = ?", (mess_id,))
    result = c.fetchall()
    conn.close()
    return result

def is_member_or_admin(user_id, mess_id):
    if is_admin(user_id, mess_id):
        return True
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM users WHERE user_id = ? AND mess_id = ?", (user_id, mess_id))
    result = c.fetchone()
    conn.close()
    return result is not None

def add_deposit(username, amount, mess_id, note=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute("INSERT INTO deposits (username, amount, date, note, mess_id) VALUES (?, ?, ?, ?, ?)", 
             (username, amount, date, note, mess_id))
    conn.commit()
    conn.close()

def add_expense(description, amount, mess_id, added_by="System"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute("INSERT INTO expenses (description, amount, date, added_by, mess_id) VALUES (?, ?, ?, ?, ?)", 
             (description, amount, date, added_by, mess_id))
    conn.commit()
    conn.close()

def get_total_deposits(mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT SUM(amount) FROM deposits WHERE mess_id = ?", (mess_id,))
    total = c.fetchone()[0] or 0
    conn.close()
    return total

def get_total_expenses(mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT SUM(amount) FROM expenses WHERE mess_id = ?", (mess_id,))
    total = c.fetchone()[0] or 0
    conn.close()
    return total

def get_balance(mess_id):
    return get_total_deposits(mess_id) - get_total_expenses(mess_id)

def get_user_deposits(username, mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT SUM(amount) FROM deposits WHERE username = ? AND mess_id = ?", (username, mess_id))
    total = c.fetchone()[0] or 0
    conn.close()
    return total

def get_user_deposits_with_date(username, mess_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT SUM(amount) FROM deposits 
                 WHERE username = ? AND mess_id = ? 
                 AND date BETWEEN ? AND ?""", 
              (username, mess_id, start_date, end_date))
    total = c.fetchone()[0] or 0
    conn.close()
    return total

def get_expenses_with_date(mess_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT description, amount, date, added_by FROM expenses 
                 WHERE mess_id = ? AND date BETWEEN ? AND ?
                 ORDER BY date""", 
              (mess_id, start_date, end_date))
    data = c.fetchall()
    conn.close()
    return data

def get_deposits_with_date(mess_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT username, amount, date, note FROM deposits 
                 WHERE mess_id = ? AND date BETWEEN ? AND ?
                 ORDER BY date""", 
              (mess_id, start_date, end_date))
    data = c.fetchall()
    conn.close()
    return data

def get_recent_deposits(mess_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, amount, date, note FROM deposits WHERE mess_id = ? ORDER BY date DESC LIMIT ?", 
             (mess_id, limit))
    data = c.fetchall()
    conn.close()
    return data

def get_recent_expenses(mess_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT description, amount, date, added_by FROM expenses WHERE mess_id = ? ORDER BY date DESC LIMIT ?", 
             (mess_id, limit))
    data = c.fetchall()
    conn.close()
    return data

# ============ মিল (Student Mess) সংক্রান্ত ফাংশন ============
def set_mill(mess_id, username, mill_date, mill_count):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO mills (mess_id, username, mill_date, mill_count)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(mess_id, username, mill_date)
                 DO UPDATE SET mill_count = excluded.mill_count""",
              (mess_id, username, mill_date, mill_count))
    conn.commit()
    conn.close()

def bulk_set_mills_for_date(mess_id, mill_date, usernames, defaults, exceptions=None):
    """defaults: either a single number (same for everyone) or a dict {username: count}
    (from meal presets). exceptions: dict {username: count} that override defaults for that date."""
    exceptions = exceptions or {}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for username in usernames:
        if username in exceptions:
            count = exceptions[username]
        elif isinstance(defaults, dict):
            count = defaults.get(username, 1.0)
        else:
            count = defaults
        c.execute("""INSERT INTO mills (mess_id, username, mill_date, mill_count)
                     VALUES (?, ?, ?, ?)
                     ON CONFLICT(mess_id, username, mill_date)
                     DO UPDATE SET mill_count = excluded.mill_count""",
                  (mess_id, username, mill_date, count))
    conn.commit()
    conn.close()

def get_mills_with_date(mess_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT username, mill_date, mill_count FROM mills
                 WHERE mess_id = ? AND mill_date >= ? AND mill_date <= ?
                 ORDER BY mill_date""", (mess_id, start_date, end_date))
    data = c.fetchall()
    conn.close()
    return data

def get_user_mill_total(mess_id, username, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT COALESCE(SUM(mill_count), 0) FROM mills
                 WHERE mess_id = ? AND username = ? AND mill_date >= ? AND mill_date <= ?""",
              (mess_id, username, start_date, end_date))
    total = c.fetchone()[0]
    conn.close()
    return total

def get_total_mills(mess_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT COALESCE(SUM(mill_count), 0) FROM mills
                 WHERE mess_id = ? AND mill_date >= ? AND mill_date <= ?""",
              (mess_id, start_date, end_date))
    total = c.fetchone()[0]
    conn.close()
    return total

def get_mill_dates_entered(mess_id):
    """যেসব তারিখে ইতিমধ্যে মিল এন্ট্রি করা হয়েছে (ডুপ্লিকেট এন্ট্রি সতর্কতার জন্য)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT mill_date FROM mills WHERE mess_id = ?", (mess_id,))
    dates = {row[0] for row in c.fetchall()}
    conn.close()
    return dates

def get_daily_mill_totals(mess_id, start_date, end_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT mill_date, SUM(mill_count) FROM mills
                 WHERE mess_id = ? AND mill_date >= ? AND mill_date <= ?
                 GROUP BY mill_date ORDER BY mill_date""", (mess_id, start_date, end_date))
    data = {row[0]: row[1] for row in c.fetchall()}
    conn.close()
    return data

# ============ প্রতিদিনের মিল প্রিসেট (সকাল/দুপুর/রাত) ============
def get_meal_preset(mess_id, username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT breakfast, lunch, dinner FROM meal_presets WHERE mess_id = ? AND username = ?", (mess_id, username))
    result = c.fetchone()
    conn.close()
    if result:
        return {'breakfast': result[0], 'lunch': result[1], 'dinner': result[2]}
    # কেউ নিজে প্রিসেট সেট না করলে, মেস তৈরির সময় বেছে নেওয়া সকালের কনভেনশন (১/২ বা ১) ডিফল্ট হিসেবে ধরা হয়
    mess_info = get_mess_info(mess_id)
    default_bf = mess_info.get('default_breakfast', 1.0) if mess_info else 1.0
    return {'breakfast': default_bf, 'lunch': 1.0, 'dinner': 1.0}

def set_meal_preset(mess_id, username, breakfast, lunch, dinner):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO meal_presets (mess_id, username, breakfast, lunch, dinner)
                 VALUES (?, ?, ?, ?, ?)
                 ON CONFLICT(mess_id, username)
                 DO UPDATE SET breakfast = excluded.breakfast, lunch = excluded.lunch, dinner = excluded.dinner""",
              (mess_id, username, breakfast, lunch, dinner))
    conn.commit()
    conn.close()

def get_preset_total(mess_id, username):
    p = get_meal_preset(mess_id, username)
    return p['breakfast'] + p['lunch'] + p['dinner']

def get_daily_default_mills(mess_id, usernames):
    """প্রতিটা ইউজারের নিজস্ব সকাল+দুপুর+রাত প্রিসেট অনুযায়ী দৈনিক ডিফল্ট মিল সংখ্যা"""
    return {u: get_preset_total(mess_id, u) for u in usernames}

def auto_fill_missing_mills_for_date(mess_id, mill_date):
    """যাদের জন্য এখনো এই তারিখে কোনো মিল এন্ট্রি হয়নি (admin ম্যানুয়াল দেয়নি), তাদের জন্য
    প্রিসেট অনুযায়ী অটো-ফিল করে। যাদের ইতিমধ্যে এন্ট্রি আছে (admin এর ব্যতিক্রম সহ) তা বদলায় না।"""
    usernames = [u for u, _ in get_users(mess_id)]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    filled = []
    for username in usernames:
        preset_total = get_preset_total(mess_id, username)
        c.execute("""INSERT OR IGNORE INTO mills (mess_id, username, mill_date, mill_count) VALUES (?, ?, ?, ?)""",
                  (mess_id, username, mill_date, preset_total))
        if c.rowcount > 0:
            filled.append((username, preset_total))
    conn.commit()
    conn.close()
    return filled

def get_next_mess_id():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT MAX(CAST(SUBSTR(key, 6, LENGTH(key)-10) AS INTEGER)) FROM mess_settings WHERE key LIKE 'mess_%_info'")
    result = c.fetchone()[0]
    conn.close()
    return (result or 0) + 1

def is_mess_completed(mess_id):
    info = get_mess_info(mess_id)
    if info and info['end_date'] != 'চলমান':
        return True
    return False

def complete_mess(mess_id, end_date):
    info = get_mess_info(mess_id)
    if info:
        save_mess_info(mess_id, info['start_date'], end_date, info['month_name'])

def generate_pdf_report(mess_id):
    mess_info = get_mess_info(mess_id)
    users = get_users(mess_id)
    mess_type = mess_info.get('mess_type', 'simple')
    
    start_date = mess_info['start_date']
    end_date = mess_info['end_date'] if mess_info['end_date'] != 'চলমান' else datetime.now().strftime("%Y-%m-%d")
    
    # ডিপোজিট/খরচের টাইমস্ট্যাম্প এ সময়ও (HH:MM) থাকে, তাই পুরো দিন কভার করতে
    # কোয়েরির জন্য সীমা প্রশস্ত করা হচ্ছে (নাহলে আজকের এন্ট্রি বাদ পড়ে যায়)
    query_start = f"{start_date} 00:00"
    query_end = f"{end_date} 23:59"
    
    deposits = get_deposits_with_date(mess_id, query_start, query_end)
    expenses = get_expenses_with_date(mess_id, query_start, query_end)
    
    total_dep = sum(d[1] for d in deposits)
    total_exp = sum(e[1] for e in expenses)
    balance = total_dep - total_exp
    
    buffer = io.BytesIO()
    
    # PDF ডকুমেন্ট তৈরি
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        rightMargin=50, 
        leftMargin=50, 
        topMargin=50, 
        bottomMargin=50
    )
    
    story = []
    
    # টাইটেল
    story.append(bn_text("মেসের ফাইনাল রিপোর্ট", size=20, bold=True, color=(26, 42, 108)))
    story.append(Spacer(1, 14))
    
    # মেস ইনফো
    story.append(bn_text(f"মেস নম্বর: {mess_display_label(mess_info)}", size=12))
    story.append(Spacer(1, 4))
    story.append(bn_text(f"মাস: {mess_info['month_name']}", size=12))
    story.append(Spacer(1, 4))
    story.append(bn_text(f"সময়কাল: {start_date} থেকে {end_date}", size=12))
    story.append(Spacer(1, 4))
    story.append(bn_text(f"জেনারেট: {datetime.now().strftime('%Y-%m-%d %H:%M')}", size=12))
    story.append(Spacer(1, 20))
    
    # ইউজার ডিপোজিট টেবিল
    story.append(bn_text("ইউজার ভিত্তিক ডিপোজিট", size=13, bold=True))
    story.append(Spacer(1, 10))
    
    user_data = [[bn_text("ইউজারনেম", size=11, bold=True, color=(255, 255, 255)),
                  bn_text("ডিপোজিট (টাকা)", size=11, bold=True, color=(255, 255, 255))]]
    total_user_dep = 0
    for username, full_name in users:
        dep = get_user_deposits_with_date(username, mess_id, query_start, query_end)
        user_data.append([f"@{username}", f"{dep:.2f}"])
        total_user_dep += dep
    
    if users:
        user_data.append([bn_text("সর্বমোট", size=11, bold=True), f"{total_user_dep:.2f}"])
    
    user_table = Table(user_data, colWidths=[2.5*inch, 2*inch])
    user_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a5276')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#eaf2f8')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d4e6f1')),
        ('FONTNAME', (1, -1), (1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.HexColor('#f7f9fa'), colors.HexColor('#eaf2f8')])
    ]))
    story.append(user_table)
    story.append(Spacer(1, 20))
    
    # সারাংশ টেবিল
    story.append(bn_text("সারাংশ", size=13, bold=True))
    story.append(Spacer(1, 10))
    
    summary_data = [
        [bn_text("বিবরণ", size=11, bold=True, color=(255, 255, 255)),
         bn_text("পরিমাণ (টাকা)", size=11, bold=True, color=(255, 255, 255))],
        [bn_text("মোট ডিপোজিট", size=11), f"{total_dep:.2f}"],
        [bn_text("মোট খরচ", size=11), f"{total_exp:.2f}"],
        [bn_text("অবশিষ্ট", size=11, bold=True), f"{balance:.2f}"]
    ]
    
    balance_color = colors.HexColor('#27ae60') if balance >= 0 else colors.HexColor('#e74c3c')
    
    summary_table = Table(summary_data, colWidths=[2.5*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e8449')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (1, 1), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#e8f8f5')),
        ('BACKGROUND', (0, -1), (-1, -1), balance_color),
        ('TEXTCOLOR', (1, -1), (1, -1), colors.whitesmoke),
        ('FONTNAME', (1, -1), (1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))
    
    # হিসাব (কে ফেরত পাবে / কাকে দিতে হবে) — mess_type অনুযায়ী মাথাপিছু বা মিল-ভিত্তিক
    if users:
        if mess_type == 'student':
            total_mills = get_total_mills(mess_id, start_date, end_date)
            per_mill = (total_exp / total_mills) if total_mills > 0 else 0
            story.append(bn_text("মিল হিসাব", size=13, bold=True))
            story.append(Spacer(1, 8))
            story.append(bn_text(
                f"মোট খরচ {total_exp:.2f} টাকা ÷ মোট {total_mills:g} মিল = প্রতি মিল {per_mill:.2f} টাকা",
                size=10, color=(80, 80, 80)
            ))
            story.append(Spacer(1, 10))
            
            settle_data = [[bn_text("ইউজারনেম", size=10, bold=True, color=(255, 255, 255)),
                            bn_text("মোট মিল", size=10, bold=True, color=(255, 255, 255)),
                            bn_text("জমা দিয়েছে", size=10, bold=True, color=(255, 255, 255)),
                            bn_text("মিল খরচ", size=10, bold=True, color=(255, 255, 255)),
                            bn_text("ফেরত পাবে / দিতে হবে", size=9, bold=True, color=(255, 255, 255))]]
            row_colors = []
            for username, full_name in users:
                dep = get_user_deposits_with_date(username, mess_id, query_start, query_end)
                user_mills = get_user_mill_total(mess_id, username, start_date, end_date)
                mill_cost = user_mills * per_mill
                diff = dep - mill_cost
                if diff >= 0:
                    diff_text = bn_text(f"+{diff:.2f} (ফেরত পাবে)", size=9, color=(30, 100, 40))
                    row_colors.append(colors.HexColor('#d5f5e3'))
                else:
                    diff_text = bn_text(f"{diff:.2f} (দিতে হবে)", size=9, color=(150, 30, 30))
                    row_colors.append(colors.HexColor('#fadbd8'))
                settle_data.append([f"@{username}", f"{user_mills:g}", f"{dep:.2f}", f"{mill_cost:.2f}", diff_text])
            
            settle_table = Table(settle_data, colWidths=[1.3*inch, 0.8*inch, 1.1*inch, 1.1*inch, 1.5*inch])
            settle_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6c3483')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]
            for i, rc in enumerate(row_colors, start=1):
                settle_style.append(('BACKGROUND', (0, i), (-1, i), rc))
            settle_table.setStyle(TableStyle(settle_style))
            story.append(settle_table)
            story.append(Spacer(1, 20))
            
            # তারিখ-ভিত্তিক মিল ব্রেকডাউন (কে কবে কয়টা মিল খেয়েছে)
            mill_rows = get_mills_with_date(mess_id, start_date, end_date)
            if mill_rows:
                story.append(bn_text("তারিখ ভিত্তিক মিল বিবরণ", size=13, bold=True))
                story.append(Spacer(1, 10))
                
                dates_sorted = sorted({r[1] for r in mill_rows})
                usernames_sorted = [u for u, _ in users]
                mill_map = {(u, d): cnt for u, d, cnt in mill_rows}
                
                header = [bn_text("তারিখ", size=9, bold=True, color=(255, 255, 255))] + \
                         [bn_text(f"@{u}", size=9, bold=True, color=(255, 255, 255)) for u in usernames_sorted] + \
                         [bn_text("দিনের মোট", size=9, bold=True, color=(255, 255, 255))]
                mill_table_data = [header]
                for d in dates_sorted:
                    row = [d]
                    day_total = 0
                    for u in usernames_sorted:
                        cnt = mill_map.get((u, d))
                        row.append(f"{cnt:g}" if cnt is not None else "-")
                        day_total += cnt or 0
                    row.append(bn_text(f"{day_total:g}", size=9, bold=True))
                    mill_table_data.append(row)
                total_row = [bn_text("মোট", size=9, bold=True)]
                for u in usernames_sorted:
                    total_row.append(f"{get_user_mill_total(mess_id, u, start_date, end_date):g}")
                total_row.append(bn_text(f"{get_total_mills(mess_id, start_date, end_date):g}", size=9, bold=True))
                mill_table_data.append(total_row)
                
                col_widths = [0.9*inch] + [min(0.8*inch, 4.2*inch/max(len(usernames_sorted),1))]*len(usernames_sorted) + [0.9*inch]
                mill_table = Table(mill_table_data, colWidths=col_widths)
                mill_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#117864')),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#e8f6f3')),
                    ('BACKGROUND', (-1, 1), (-1, -2), colors.HexColor('#d1f2eb')),
                    ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#a3e4d7')),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
                ]))
                story.append(mill_table)
                story.append(Spacer(1, 20))
        else:
            story.append(bn_text("মাথাপিছু হিসাব", size=13, bold=True))
            story.append(Spacer(1, 8))
            
            per_head = total_exp / len(users)
            story.append(bn_text(f"মোট খরচ {total_exp:.2f} টাকা ÷ {len(users)} জন = মাথাপিছু {per_head:.2f} টাকা", size=10, color=(80, 80, 80)))
            story.append(Spacer(1, 10))
            
            settle_data = [[bn_text("ইউজারনেম", size=10, bold=True, color=(255, 255, 255)),
                            bn_text("জমা দিয়েছে", size=10, bold=True, color=(255, 255, 255)),
                            bn_text("মাথাপিছু খরচ", size=10, bold=True, color=(255, 255, 255)),
                            bn_text("ফেরত পাবে / দিতে হবে", size=10, bold=True, color=(255, 255, 255))]]
            row_colors = []
            for username, full_name in users:
                dep = get_user_deposits_with_date(username, mess_id, query_start, query_end)
                diff = dep - per_head
                if diff >= 0:
                    diff_text = bn_text(f"+{diff:.2f} (ফেরত পাবে)", size=9, color=(30, 100, 40))
                    row_colors.append(colors.HexColor('#d5f5e3'))
                else:
                    diff_text = bn_text(f"{diff:.2f} (দিতে হবে)", size=9, color=(150, 30, 30))
                    row_colors.append(colors.HexColor('#fadbd8'))
                settle_data.append([f"@{username}", f"{dep:.2f}", f"{per_head:.2f}", diff_text])
            
            settle_table = Table(settle_data, colWidths=[1.5*inch, 1.2*inch, 1.2*inch, 1.6*inch])
            settle_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6c3483')),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 1, colors.black)
            ]
            for i, rc in enumerate(row_colors, start=1):
                settle_style.append(('BACKGROUND', (0, i), (-1, i), rc))
            settle_table.setStyle(TableStyle(settle_style))
            story.append(settle_table)
            story.append(Spacer(1, 20))
    
    # ইউজার ভিত্তিক ডিপোজিটের বিস্তারিত (তারিখসহ)
    if users:
        story.append(bn_text("ইউজার ভিত্তিক ডিপোজিটের বিস্তারিত", size=13, bold=True))
        story.append(Spacer(1, 10))
        
        deposits_by_user = {}
        for dep_username, dep_amount, dep_date, dep_note in deposits:
            deposits_by_user.setdefault(dep_username, []).append((dep_date, dep_amount))
        
        for username, full_name in users:
            user_deps = sorted(deposits_by_user.get(username, []))
            story.append(bn_text(f"@{username}", size=11, bold=True, color=(26, 82, 118)))
            story.append(Spacer(1, 4))
            
            if user_deps:
                detail_data = [[bn_text("তারিখ", size=10, bold=True, color=(255, 255, 255)),
                                 bn_text("পরিমাণ (টাকা)", size=10, bold=True, color=(255, 255, 255))]]
                user_total = 0
                for dep_date, dep_amount in user_deps:
                    detail_data.append([dep_date[:16], f"{dep_amount:.2f}"])
                    user_total += dep_amount
                detail_data.append([bn_text("মোট", size=10, bold=True), f"{user_total:.2f}"])
                
                detail_table = Table(detail_data, colWidths=[3*inch, 1.5*inch])
                detail_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2874a6')),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
                    ('FONTSIZE', (0, 1), (-1, -1), 10),
                    ('TOPPADDING', (0, 0), (-1, -1), 5),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                    ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#eaf2f8')),
                    ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d4e6f1')),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ]))
                story.append(detail_table)
            else:
                story.append(bn_text("(কোনো ডিপোজিট নেই)", size=10, color=(120, 120, 120)))
            
            story.append(Spacer(1, 14))
    
    # খরচের বিস্তারিত
    if expenses and len(expenses) <= 20:
        story.append(bn_text("খরচের বিস্তারিত", size=13, bold=True))
        story.append(Spacer(1, 10))
        expense_data = [[bn_text("বিবরণ", size=10, bold=True, color=(255, 255, 255)),
                         bn_text("পরিমাণ (টাকা)", size=10, bold=True, color=(255, 255, 255)),
                         bn_text("তারিখ", size=10, bold=True, color=(255, 255, 255)),
                         bn_text("যোগকারী", size=10, bold=True, color=(255, 255, 255))]]
        for desc, amount, date, added_by in expenses:
            expense_data.append([bn_text(desc, size=9), f"{amount:.2f}", date[:10], f"@{added_by}"])
        
        expense_table = Table(expense_data, colWidths=[1.8*inch, 1.2*inch, 1.5*inch, 1.2*inch])
        expense_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#922b21')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (1, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (1, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#fdedec')),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(expense_table)
    
    if len(expenses) > 20:
        story.append(Spacer(1, 10))
        story.append(bn_text(f"মোট {len(expenses)}টি খরচ। বিস্তারিত টেলিগ্রামে দেখুন।", size=10, color=(80, 80, 80)))
    
    # প্রতিদিনের খরচ (তারিখ ভিত্তিক মোট)
    if expenses:
        story.append(Spacer(1, 20))
        story.append(bn_text("প্রতিদিনের খরচ", size=13, bold=True))
        story.append(Spacer(1, 10))
        
        daily_totals = {}
        for desc, amount, date, added_by in expenses:
            day = date[:10]
            daily_totals[day] = daily_totals.get(day, 0) + amount
        
        daily_data = [[bn_text("তারিখ", size=10, bold=True, color=(255, 255, 255)),
                       bn_text("মোট খরচ (টাকা)", size=10, bold=True, color=(255, 255, 255))]]
        for day in sorted(daily_totals.keys()):
            daily_data.append([day, f"{daily_totals[day]:.2f}"])
        daily_data.append([bn_text("সর্বমোট", size=10, bold=True), f"{sum(daily_totals.values()):.2f}"])
        
        daily_table = Table(daily_data, colWidths=[2.5*inch, 2*inch])
        daily_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#7d3c98')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#f4ecf7')),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d7bde2')),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.HexColor('#faf5fc'), colors.HexColor('#f4ecf7')])
        ]))
        story.append(daily_table)
    
    # ফুটার
    story.append(Spacer(1, 30))
    story.append(bn_text(f"জেনারেট: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", size=9, color=(128, 128, 128)))
    story.append(Spacer(1, 3))
    story.append(bn_text("© @mess_accounting_bot", size=9, color=(128, 128, 128)))
    
    # PDF বিল্ড
    doc.build(story)
    buffer.seek(0)
    return buffer

# ============ টেলিগ্রাম হ্যান্ডলার ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tg_username = update.effective_user.username
    display_name = tg_username or update.effective_user.first_name

    if tg_username:
        link_user_id(tg_username, user_id)

    active_mess_id = get_current_mess_id(user_id)
    if active_mess_id and get_mess_info(active_mess_id) and is_member_or_admin(user_id, active_mess_id):
        await show_main_menu(update.message, active_mess_id, user_id)
        return

    my_messes = get_user_messes(user_id)

    if len(my_messes) == 1:
        set_current_mess_id(user_id, my_messes[0]['id'])
        await show_main_menu(update.message, my_messes[0]['id'], user_id)
        return

    if len(my_messes) > 1:
        keyboard = []
        for mess in my_messes:
            status = "✅" if mess['end_date'] != 'চলমান' else "🟢"
            keyboard.append([InlineKeyboardButton(
                f"{status} {mess_display_label(mess)} - {mess['month_name']}",
                callback_data=f'switch_mess_{mess["id"]}'
            )])
        keyboard.append([InlineKeyboardButton("🆕 নতুন মেস শুরু করুন", callback_data='new_mess')])
        await update.message.reply_text(
            f"🏠 **স্বাগতম, {display_name}!**\n\nআপনি একাধিক মেসের সাথে যুক্ত আছেন। কোনটা দেখতে চান?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return

    keyboard = [
        [InlineKeyboardButton("🆕 নতুন মেস শুরু করুন", callback_data='new_mess')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🏠 **মেসের হিসাব বট**\n\n"
        f"স্বাগতম, {display_name}!\n"
        f"আপনি এখনো কোনো মেসের সাথে যুক্ত নন।\n\n"
        f"নতুন মেস শুরু করলে আপনি সেই মেসের এডমিন হয়ে যাবেন।",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

MESS_TYPE_LABELS = {
    'simple': '🍽️ সাধারণ মেস (মাথাপিছু হিসাব)',
    'student': '🎓 স্টুডেন্ট মেস (মিল হিসাব)',
    'personal': '👤 ব্যক্তিগত হিসাব'
}

async def new_mess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(MESS_TYPE_LABELS['simple'], callback_data='newmess_type_simple')],
        [InlineKeyboardButton(MESS_TYPE_LABELS['student'], callback_data='newmess_type_student')],
        [InlineKeyboardButton(MESS_TYPE_LABELS['personal'], callback_data='newmess_type_personal')]
    ]
    text = (
        "🆕 **নতুন মেস**\n\n"
        "কী ধরনের হিসাব রাখতে চান?\n\n"
        "🍽️ সাধারণ মেস — খরচ সবার মধ্যে মাথাপিছু ভাগ হবে\n"
        "🎓 স্টুডেন্ট মেস — প্রতিদিন কে কয়টা মিল খেলো তার হিসাবে খরচ ভাগ হবে\n"
        "👤 ব্যক্তিগত হিসাব — শুধু নিজের আয়-ব্যয় ট্র্যাক করার জন্য"
    )
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_main_menu(message, mess_id, user_id=None):
    mess_info = get_mess_info(mess_id)
    if not mess_info:
        await message.reply_text("❌ মেস তথ্য পাওয়া যায়নি! /start দিয়ে নতুন শুরু করুন।")
        return
    
    is_completed = is_mess_completed(mess_id)
    status = "✅ সম্পন্ন" if is_completed else "🟢 চলমান"
    mess_type = mess_info.get('mess_type', 'simple')
    type_label = MESS_TYPE_LABELS.get(mess_type, mess_type)
    
    await message.reply_text(
        f"📆 **মেস ইনফো**\n"
        f"🆔 {mess_display_label(mess_info)}\n"
        f"🏷️ ধরন: {type_label}\n"
        f"📅 শুরু: {mess_info['start_date']}\n"
        f"📅 শেষ: {mess_info['end_date']}\n"
        f"📌 মাস: {mess_info['month_name']}\n"
        f"📊 স্ট্যাটাস: {status}\n"
        f"💰 ব্যালেন্স: {get_balance(mess_id):.2f} টাকা\n\n"
        f"👇 নিচের মেনু (⌨️ আইকন) থেকে কমান্ড বেছে নিন।",
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data == 'new_mess':
        await new_mess(update, context)
    
    elif data == 'show_guide':
        await query.message.reply_text(build_guide_text(), parse_mode='HTML')
    
    elif data.startswith('newmess_type_'):
        mess_type = data.replace('newmess_type_', '')
        context.user_data['new_mess_type'] = mess_type
        if mess_type == 'student':
            keyboard = [
                [InlineKeyboardButton("১/২ (হাফ)", callback_data='newmess_bf|0.5')],
                [InlineKeyboardButton("১ (পূর্ণ)", callback_data='newmess_bf|1')]
            ]
            await query.edit_message_text(
                f"✅ ধরন: {MESS_TYPE_LABELS.get(mess_type, mess_type)}\n\n"
                f"🌅 তোমাদের মেসে সাধারণত সকালের মিল কত ধরে হিসাব হয়?\n"
                f"(এটাই সবার ডিফল্ট হবে, যে কেউ চাইলে /meal_setting দিয়ে নিজেরটা বদলাতে পারবে)",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.edit_message_text(
                f"✅ ধরন: {MESS_TYPE_LABELS.get(mess_type, mess_type)}\n\n"
                f"📅 **মেস শুরুর তারিখ লিখুন** (যেমন: 2026-01-01):",
                parse_mode='Markdown'
            )
            context.user_data['action'] = 'new_mess_date'
    
    elif data.startswith('newmess_bf|'):
        value = float(data.split('|')[1])
        context.user_data['new_mess_breakfast'] = value
        await query.edit_message_text(
            f"✅ সকালের ডিফল্ট মিল: {value:g}\n\n"
            f"📅 **মেস শুরুর তারিখ লিখুন** (যেমন: 2026-01-01):",
            parse_mode='Markdown'
        )
        context.user_data['action'] = 'new_mess_date'
    
    elif data == 'old_messes':
        messes = get_user_messes(user_id)
        if not messes:
            await query.edit_message_text("📭 আপনি কোনো মেসের সাথে যুক্ত নন। /start দিয়ে নতুন শুরু করুন।")
            return
        keyboard = []
        for mess in messes:
            status = "✅" if mess['end_date'] != 'চলমান' else "🟢"
            keyboard.append([InlineKeyboardButton(
                f"{status} {mess_display_label(mess)} - {mess['month_name']} ({mess['start_date']} - {mess['end_date']})", 
                callback_data=f'switch_mess_{mess["id"]}'
            )])
        keyboard.append([InlineKeyboardButton("🔙 ব্যাক", callback_data='back_start')])
        await query.edit_message_text("📋 **আপনার মেসসমূহ:**\n\nনিচ থেকে একটি বেছে নিন:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith('switch_mess_'):
        mess_id = int(data.replace('switch_mess_', ''))
        if not is_member_or_admin(user_id, mess_id):
            await query.answer("❌ আপনি এই মেসের সদস্য নন!", show_alert=True)
            return
        set_current_mess_id(user_id, mess_id)
        await query.edit_message_text(f"✅ {mess_display_label(get_mess_info(mess_id))} এ স্যুইচ করা হয়েছে!")
        await show_main_menu(query.message, mess_id, user_id)
    
    elif data == 'back_start':
        await start(update, context)
    
    elif data == 'change_mess':
        messes = get_user_messes(user_id)
        keyboard = []
        for mess in messes:
            status = "✅" if mess['end_date'] != 'চলমান' else "🟢"
            keyboard.append([InlineKeyboardButton(
                f"{status} #{mess['id']} - {mess['month_name']}", 
                callback_data=f'switch_mess_{mess["id"]}'
            )])
        keyboard.append([InlineKeyboardButton("➕ নতুন মেস", callback_data='new_mess')])
        await query.edit_message_text("📂 **মেস পরিবর্তন করুন:**", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith('mill_date_today|'):
        mess_id = int(data.split('|')[1])
        if not is_admin(user_id, mess_id):
            await query.answer("❌ শুধুমাত্র এডমিন মিল এন্ট্রি দিতে পারবেন!", show_alert=True)
            return
        today = datetime.now().strftime("%Y-%m-%d")
        await start_mill_exception_flow(query, context, mess_id, today, via_edit=True)
    
    elif data.startswith('mill_date_custom|'):
        mess_id = int(data.split('|')[1])
        if not is_admin(user_id, mess_id):
            await query.answer("❌ শুধুমাত্র এডমিন মিল এন্ট্রি দিতে পারবেন!", show_alert=True)
            return
        context.user_data['action'] = f'mill_custom_date|{mess_id}'
        await query.edit_message_text("🗓️ **তারিখ লিখুন** (যেমন: 2026-01-15):")
    
    elif data.startswith('mill_exc_no|'):
        mess_id = int(data.split('|')[1])
        if not is_admin(user_id, mess_id):
            await query.answer("❌ অনুমতি নেই!", show_alert=True)
            return
        mill_date = context.user_data.get('mill_date')
        usernames = [u for u, _ in get_users(mess_id)]
        presets = get_daily_default_mills(mess_id, usernames)
        bulk_set_mills_for_date(mess_id, mill_date, usernames, presets, {})
        context.user_data['mill_exceptions'] = {}
        lines = "\n".join(f"@{u}: {presets[u]:g}" for u in usernames)
        await query.edit_message_text(
            f"✅ **{mill_date}** তারিখের জন্য যার যার প্রিসেট অনুযায়ী মিল সেভ হয়েছে!\n\n{lines}",
            parse_mode='Markdown'
        )
    
    elif data.startswith('mill_exc_yes|'):
        mess_id = int(data.split('|')[1])
        if not is_admin(user_id, mess_id):
            await query.answer("❌ অনুমতি নেই!", show_alert=True)
            return
        await show_mill_exception_menu(query, context, mess_id, edit=True)
    
    elif data.startswith('mill_user|'):
        _, mess_id_s, username = data.split('|')
        mess_id = int(mess_id_s)
        if not is_admin(user_id, mess_id):
            await query.answer("❌ অনুমতি নেই!", show_alert=True)
            return
        keyboard = [[InlineKeyboardButton(label, callback_data=f'mill_set|{mess_id}|{username}|{val}') for label, val in MILL_QUICK_VALUES[:3]],
                    [InlineKeyboardButton(label, callback_data=f'mill_set|{mess_id}|{username}|{val}') for label, val in MILL_QUICK_VALUES[3:]],
                    [InlineKeyboardButton("✍️ অন্য সংখ্যা লিখবো", callback_data=f'mill_custom_val|{mess_id}|{username}')],
                    [InlineKeyboardButton("🔙 ব্যাক", callback_data=f'mill_exc_yes|{mess_id}')]]
        await query.edit_message_text(
            f"@{username} — কয়টা মিল খেয়েছে (তারিখ: {context.user_data.get('mill_date')})?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith('mill_set|'):
        _, mess_id_s, username, value = data.split('|')
        mess_id = int(mess_id_s)
        if not is_admin(user_id, mess_id):
            await query.answer("❌ অনুমতি নেই!", show_alert=True)
            return
        context.user_data.setdefault('mill_exceptions', {})[username] = _mill_val_to_float(value)
        await show_mill_exception_menu(query, context, mess_id, edit=True)
    
    elif data.startswith('mill_custom_val|'):
        _, mess_id_s, username = data.split('|')
        mess_id = int(mess_id_s)
        if not is_admin(user_id, mess_id):
            await query.answer("❌ অনুমতি নেই!", show_alert=True)
            return
        context.user_data['action'] = f'mill_custom_value|{mess_id}|{username}'
        await query.edit_message_text(f"✍️ @{username} এর মিল সংখ্যা লিখুন (যেমন: 1.5):")
    
    elif data.startswith('mill_confirm|'):
        mess_id = int(data.split('|')[1])
        if not is_admin(user_id, mess_id):
            await query.answer("❌ অনুমতি নেই!", show_alert=True)
            return
        mill_date = context.user_data.get('mill_date')
        exceptions = context.user_data.get('mill_exceptions', {})
        usernames = [u for u, _ in get_users(mess_id)]
        presets = get_daily_default_mills(mess_id, usernames)
        bulk_set_mills_for_date(mess_id, mill_date, usernames, presets, exceptions)
        lines = "\n".join(
            f"@{u}: {exceptions.get(u, presets[u]):g}" + ("" if u in exceptions else " (প্রিসেট)")
            for u in usernames
        )
        context.user_data['mill_exceptions'] = {}
        await query.edit_message_text(f"✅ **{mill_date}** তারিখের মিল সেভ হয়েছে!\n\n{lines}", parse_mode='Markdown')
    
    elif data.startswith('meal_step|'):
        _, mess_id_s, meal, value = data.split('|')
        mess_id = int(mess_id_s)
        context.user_data.setdefault('meal_values', {})[meal] = _mill_val_to_float(value)
        idx = MEAL_ORDER.index(meal)
        if idx + 1 < len(MEAL_ORDER):
            await ask_meal_step(query, context, mess_id, MEAL_ORDER[idx + 1], edit=True)
        else:
            username = context.user_data.get('meal_username') or (update.effective_user.username or update.effective_user.first_name)
            await _finish_meal_preset(query, context, mess_id, username)
    
    elif data.startswith('meal_custom|'):
        _, mess_id_s, meal = data.split('|')
        mess_id = int(mess_id_s)
        context.user_data['action'] = f'meal_custom_text|{mess_id}|{meal}'
        await query.edit_message_text(f"✍️ {MEAL_LABELS[meal]} এ কয়টা মিল খাও, লিখো (যেমন: 0.5):")
    
    elif data.startswith('add_user_'):
        mess_id = int(data.replace('add_user_', ''))
        if not is_admin(user_id, mess_id):
            await query.answer("❌ শুধুমাত্র এডমিন ইউজার যোগ করতে পারবেন!", show_alert=True)
            return
        if is_mess_completed(mess_id):
            await query.edit_message_text("❌ এই মেস সম্পন্ন হয়েছে! নতুন ইউজার যোগ করা যাবে না।")
            return
        context.user_data['action'] = f'add_user_{mess_id}'
        await query.edit_message_text("👤 **ইউজার যোগ করুন**\n\n@username লিখুন (যেমন: @rahim):")
    
    elif data.startswith('deposit_user_'):
        parts = data.split('_')
        mess_id = int(parts[2])
        username = parts[3]
        if not is_member_or_admin(user_id, mess_id):
            await query.answer("❌ আপনি এই মেসের সদস্য নন!", show_alert=True)
            return
        context.user_data['deposit_user'] = username
        context.user_data['deposit_mess_id'] = mess_id
        context.user_data['action'] = f'deposit_amount_{mess_id}'
        await query.edit_message_text(f"💵 @{username} এর ডিপোজিটের পরিমাণ লিখুন (শুধু সংখ্যা):")
    
    elif data.startswith('deposit_'):
        mess_id = int(data.replace('deposit_', ''))
        if not is_member_or_admin(user_id, mess_id):
            await query.answer("❌ আপনি এই মেসের সদস্য নন!", show_alert=True)
            return
        if is_mess_completed(mess_id):
            await query.edit_message_text("❌ এই মেস সম্পন্ন হয়েছে! ডিপোজিট করা যাবে না।")
            return
        users = get_users(mess_id)
        if not users:
            await query.edit_message_text("❌ কোনো ইউজার নেই! আগে ইউজার যোগ করুন।")
            return
        if len(users) == 1:
            # ব্যক্তিগত/একক-ইউজার মেসে সরাসরি amount জিজ্ঞেস করি, বাছাইয়ের ধাপ বাদ দিয়ে
            username = users[0][0]
            context.user_data['deposit_user'] = username
            context.user_data['deposit_mess_id'] = mess_id
            context.user_data['action'] = f'deposit_amount_{mess_id}'
            await query.edit_message_text(f"💵 জমার পরিমাণ লিখুন (শুধু সংখ্যা):")
            return
        keyboard = []
        for username, full_name in users:
            keyboard.append([InlineKeyboardButton(f"@{username}", callback_data=f'deposit_user_{mess_id}_{username}')])
        keyboard.append([InlineKeyboardButton("🔙 ব্যাক", callback_data=f'back_main_{mess_id}')])
        await query.edit_message_text("👤 **কে ডিপোজিট করবেন?**", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith('add_expense_'):
        mess_id = int(data.replace('add_expense_', ''))
        if not is_admin(user_id, mess_id):
            await query.answer("❌ শুধুমাত্র এডমিন খরচ যোগ করতে পারবেন!", show_alert=True)
            return
        if is_mess_completed(mess_id):
            await query.edit_message_text("❌ এই মেস সম্পন্ন হয়েছে! খরচ যোগ করা যাবে না।")
            return
        context.user_data['action'] = f'expense_desc_{mess_id}'
        await query.edit_message_text("📝 **খরচের বিবরণ লিখুন:**")
    
    elif data.startswith('admin_panel_'):
        mess_id = int(data.replace('admin_panel_', ''))
        if not is_admin(user_id, mess_id):
            await query.answer("❌ আপনি এডমিন নন!", show_alert=True)
            return
        keyboard = [
            [InlineKeyboardButton("👑 নতুন এডমিন বানান", callback_data=f'promote_user_{mess_id}')],
            [InlineKeyboardButton("👤 এডমিন বাদ দিন", callback_data=f'demote_admin_{mess_id}')],
            [InlineKeyboardButton("🗑️ ইউজার রিমুভ করুন", callback_data=f'remove_user_{mess_id}')],
            [InlineKeyboardButton("🔙 ব্যাক", callback_data=f'back_main_{mess_id}')]
        ]
        await query.edit_message_text("⚙️ **এডমিন ম্যানেজমেন্ট**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data.startswith('promote_user_'):
        mess_id = int(data.replace('promote_user_', ''))
        if not is_admin(user_id, mess_id):
            await query.answer("❌ আপনি এডমিন নন!", show_alert=True)
            return
        users = get_users(mess_id)
        if not users:
            await query.edit_message_text("❌ কোনো ইউজার নেই!")
            return
        keyboard = []
        for username, full_name in users:
            keyboard.append([InlineKeyboardButton(f"@{username}", callback_data=f'confirm_promote_{mess_id}_{username}')])
        keyboard.append([InlineKeyboardButton("🔙 ব্যাক", callback_data=f'admin_panel_{mess_id}')])
        await query.edit_message_text("👑 **কাকে এডমিন বানাবেন?**\n\n⚠️ তাকে আগে অন্তত একবার বটে /start করতে হবে।", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data.startswith('confirm_promote_'):
        parts = data.split('_')
        mess_id = int(parts[2])
        username = parts[3]
        if not is_admin(user_id, mess_id):
            await query.answer("❌ আপনি এডমিন নন!", show_alert=True)
            return
        try:
            chat = await context.bot.get_chat(f"@{username}")
            add_admin(chat.id, mess_id, username)
            await query.edit_message_text(f"✅ @{username} কে এডমিন করা হয়েছে!")
        except Exception:
            await query.edit_message_text(f"❌ @{username} কে এডমিন করা যায়নি।\n\nতাকে আগে বটে /start দিতে বলুন, তারপর আবার চেষ্টা করুন।")
        await show_main_menu(query.message, mess_id, user_id)
    
    elif data.startswith('demote_admin_'):
        mess_id = int(data.replace('demote_admin_', ''))
        if not is_admin(user_id, mess_id):
            await query.answer("❌ আপনি এডমিন নন!", show_alert=True)
            return
        admins = get_admins(mess_id)
        if not admins:
            await query.edit_message_text("❌ কোনো এডমিন নেই!")
            return
        keyboard = []
        for admin_id, admin_username in admins:
            label = f"@{admin_username}" if admin_username else str(admin_id)
            keyboard.append([InlineKeyboardButton(label, callback_data=f'confirmdemote_{mess_id}_{admin_id}')])
        keyboard.append([InlineKeyboardButton("🔙 ব্যাক", callback_data=f'admin_panel_{mess_id}')])
        await query.edit_message_text("👤 **কাকে এডমিন থেকে বাদ দেবেন?**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data.startswith('confirmdemote_'):
        parts = data.split('_')
        mess_id = int(parts[1])
        target_id = int(parts[2])
        if not is_admin(user_id, mess_id):
            await query.answer("❌ আপনি এডমিন নন!", show_alert=True)
            return
        remove_admin(target_id, mess_id)
        await query.edit_message_text("✅ এডমিন বাদ দেওয়া হয়েছে!")
        await show_main_menu(query.message, mess_id, user_id)
    
    elif data.startswith('remove_user_'):
        mess_id = int(data.replace('remove_user_', ''))
        if not is_admin(user_id, mess_id):
            await query.answer("❌ আপনি এডমিন নন!", show_alert=True)
            return
        users = get_users(mess_id)
        if not users:
            await query.edit_message_text("❌ কোনো ইউজার নেই!")
            return
        keyboard = []
        for username, full_name in users:
            keyboard.append([InlineKeyboardButton(f"@{username}", callback_data=f'confirmremove_{mess_id}_{username}')])
        keyboard.append([InlineKeyboardButton("🔙 ব্যাক", callback_data=f'admin_panel_{mess_id}')])
        await query.edit_message_text("🗑️ **কোন ইউজার রিমুভ করবেন?**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data.startswith('confirmremove_'):
        parts = data.split('_')
        mess_id = int(parts[1])
        username = parts[2]
        if not is_admin(user_id, mess_id):
            await query.answer("❌ আপনি এডমিন নন!", show_alert=True)
            return
        remove_user(username, mess_id)
        await query.edit_message_text(f"✅ @{username} কে রিমুভ করা হয়েছে!")
        await show_main_menu(query.message, mess_id, user_id)
    
    elif data.startswith('summary_'):
        mess_id = int(data.replace('summary_', ''))
        if not is_member_or_admin(user_id, mess_id):
            await query.answer("❌ আপনি এই মেসের সদস্য নন!", show_alert=True)
            return
        await show_summary(query, mess_id)
    
    elif data.startswith('history_'):
        mess_id = int(data.replace('history_', ''))
        if not is_member_or_admin(user_id, mess_id):
            await query.answer("❌ আপনি এই মেসের সদস্য নন!", show_alert=True)
            return
        await show_history(query, mess_id)
    
    elif data.startswith('pdf_report_'):
        mess_id = int(data.replace('pdf_report_', ''))
        if not is_member_or_admin(user_id, mess_id):
            await query.answer("❌ আপনি এই মেসের সদস্য নন!", show_alert=True)
            return
        await query.edit_message_text("⏳ **PDF রিপোর্ট তৈরি হচ্ছে...** দয়া করে অপেক্ষা করুন।")
        try:
            pdf_buffer = generate_pdf_report(mess_id)
            await query.message.reply_document(
                document=pdf_buffer,
                filename=f"mess_report_{mess_id}_{datetime.now().strftime('%Y%m%d')}.pdf",
                caption=f"📄 {mess_display_label(get_mess_info(mess_id))} এর ফাইনাল রিপোর্ট"
            )
            await query.delete_message()
        except Exception as e:
            await query.edit_message_text(f"❌ PDF তৈরি করতে সমস্যা হয়েছে: {str(e)}")
    
    elif data.startswith('end_mess_'):
        mess_id = int(data.replace('end_mess_', ''))
        if not is_admin(user_id, mess_id):
            await query.answer("❌ শুধুমাত্র এডমিন মেস শেষ করতে পারবেন!", show_alert=True)
            return
        await query.edit_message_text("📅 **মেস শেষ করার তারিখ লিখুন** (যেমন: 2026-01-31):\n\n⚠️ মনে রাখবেন: একবার শেষ করলে আর ডিপোজিট/খরচ যোগ করা যাবে না!")
        context.user_data['action'] = f'end_mess_{mess_id}'
    
    elif data.startswith('back_main_'):
        mess_id = int(data.replace('back_main_', ''))
        await show_main_menu(query.message, mess_id, user_id)

def build_summary_text(mess_id):
    users = get_users(mess_id)
    total_dep = get_total_deposits(mess_id)
    total_exp = get_total_expenses(mess_id)
    balance = get_balance(mess_id)
    mess_info = get_mess_info(mess_id)
    
    if not mess_info:
        return None
    
    text = f"📊 **মেসের সারাংশ**\n"
    text += f"🆔 {mess_display_label(mess_info)}\n"
    text += f"📅 মাস: {mess_info['month_name']}\n"
    text += f"📅 সময়কাল: {mess_info['start_date']} - {mess_info['end_date']}\n"
    text += "="*30 + "\n\n"
    
    text += "💰 **ডিপোজিটের তালিকা:**\n"
    if users:
        for username, full_name in users:
            dep = get_user_deposits(username, mess_id)
            text += f"  @{username}: {dep:.2f} টাকা\n"
    else:
        text += "  (কোনো ইউজার নেই)\n"
    
    text += f"\n📈 **মোট ডিপোজিট:** {total_dep:.2f} টাকা"
    text += f"\n📉 **মোট খরচ:** {total_exp:.2f} টাকা"
    text += f"\n💵 **অবশিষ্ট:** {balance:.2f} টাকা"
    
    if balance < 0:
        text += "\n\n⚠️ *সতর্কতা: খরচ ডিপোজিটের চেয়ে বেশি!*"
    
    mess_type = mess_info.get('mess_type', 'simple')
    
    if mess_type == 'student' and users:
        s_date = mess_info['start_date']
        e_date = mess_info['end_date'] if mess_info['end_date'] != 'চলমান' else datetime.now().strftime("%Y-%m-%d")
        daily_totals = get_daily_mill_totals(mess_id, s_date, e_date)
        if daily_totals:
            text += "\n\n🍽️ **সাম্প্রতিক দিনের মোট মিল:**\n"
            for d in sorted(daily_totals.keys())[-7:]:
                text += f"  {d}: {daily_totals[d]:g} মিল\n"
        total_mills = get_total_mills(mess_id, s_date, e_date)
        if total_mills > 0:
            per_mill = total_exp / total_mills
            text += f"\n🧮 **মিল হিসাব**\n"
            text += f"মোট খরচ {total_exp:.2f} ÷ মোট {total_mills:g} মিল = প্রতি মিল {per_mill:.2f} টাকা\n"
            for username, full_name in users:
                dep = get_user_deposits(username, mess_id)
                user_mills = get_user_mill_total(mess_id, username, s_date, e_date)
                diff = dep - (user_mills * per_mill)
                if diff >= 0:
                    text += f"  @{username}: {user_mills:g} মিল, +{diff:.2f} টাকা (ফেরত পাবে)\n"
                else:
                    text += f"  @{username}: {user_mills:g} মিল, {diff:.2f} টাকা (দিতে হবে)\n"
    
    # মাথাপিছু হিসাব — শুধু খরচ ৫০০০ টাকা বা তার বেশি হলে দেখাবে (simple/personal মেসের জন্য)
    if mess_type != 'student' and users and total_exp >= 5000:
        per_head = total_exp / len(users)
        text += f"\n\n🧮 **মাথাপিছু হিসাব**\n"
        text += f"মোট খরচ {total_exp:.2f} ÷ {len(users)} জন = মাথাপিছু {per_head:.2f} টাকা\n"
        for username, full_name in users:
            dep = get_user_deposits(username, mess_id)
            diff = dep - per_head
            if diff >= 0:
                text += f"  @{username}: +{diff:.2f} টাকা (ফেরত পাবে)\n"
            else:
                text += f"  @{username}: {diff:.2f} টাকা (দিতে হবে)\n"
    
    return text

async def show_summary(query, mess_id):
    text = build_summary_text(mess_id)
    if text is None:
        await query.edit_message_text("❌ মেস তথ্য পাওয়া যায়নি!")
        return
    await query.edit_message_text(text, parse_mode='Markdown')

def build_history_text(mess_id):
    deposits = get_recent_deposits(mess_id, 10)
    expenses = get_recent_expenses(mess_id, 10)
    
    text = f"📋 **সর্বশেষ লেনদেন** ({mess_display_label(get_mess_info(mess_id))})\n"
    text += "="*30 + "\n\n"
    
    text += "💰 **ডিপোজিট:**\n"
    if deposits:
        for username, amount, date, note in deposits:
            text += f"  @{username}: {amount:.2f} টাকা\n"
            text += f"    📅 {date}\n"
    else:
        text += "  (কোনো ডিপোজিট নেই)\n"
    
    text += "\n💸 **খরচ:**\n"
    if expenses:
        for desc, amount, date, added_by in expenses:
            text += f"  {desc}: {amount:.2f} টাকা\n"
            text += f"    📅 {date}\n"
    else:
        text += "  (কোনো খরচ নেই)\n"
    
    return text

async def show_history(query, mess_id):
    text = build_history_text(mess_id)
    await query.edit_message_text(text)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    action = context.user_data.get('action')
    
    if not action:
        return
    
    if action == 'new_mess_date':
        try:
            start_date = text
            datetime.strptime(start_date, "%Y-%m-%d")
            context.user_data['new_mess_date'] = start_date
            context.user_data['action'] = 'new_mess_month'
            await update.message.reply_text("📌 **মাসের নাম লিখুন:**\nযেমন: জানুয়ারি ২০২৬")
        except ValueError:
            await update.message.reply_text("❌ ভুল ফরম্যাট! তারিখটি YYYY-MM-DD ফরম্যাটে দিন।")
    
    elif action == 'new_mess_month':
        month_name = text
        start_date = context.user_data['new_mess_date']
        mess_type = context.user_data.get('new_mess_type', 'simple')
        mess_id = get_next_mess_id()
        user_id = update.effective_user.id
        type_ordinal = get_next_type_ordinal(user_id, mess_type)
        default_breakfast = context.user_data.get('new_mess_breakfast', 1.0)
        save_mess_info(mess_id, start_date, 'চলমান', month_name, mess_type=mess_type,
                        creator_user_id=user_id, type_ordinal=type_ordinal, default_breakfast=default_breakfast)
        tg_username = update.effective_user.username or update.effective_user.first_name
        add_admin(user_id, mess_id, tg_username)
        add_user(tg_username, mess_id, user_id=user_id)
        set_current_mess_id(user_id, mess_id)
        context.user_data['action'] = None
        context.user_data['new_mess_type'] = None
        context.user_data['new_mess_breakfast'] = None
        label = mess_display_label(get_mess_info(mess_id))
        extra_hint = ""
        if mess_type == 'student':
            extra_hint = "\n\n📌 প্রতিদিন মিল এন্ট্রি দিতে /mill_entry কমান্ড ব্যবহার করুন।"
        elif mess_type == 'personal':
            extra_hint = "\n\n📌 এটি আপনার ব্যক্তিগত হিসাব — শুধু আপনার ডিপোজিট/খরচ যোগ করুন।"
        await update.message.reply_text(
            f"✅ **নতুন মেস শুরু হয়েছে!**\n\n"
            f"🆔 {label}\n"
            f"📅 শুরুর তারিখ: {start_date}\n"
            f"📌 মাস: {month_name}\n"
            f"🏷️ ধরন: {MESS_TYPE_LABELS.get(mess_type, mess_type)}\n"
            f"👑 আপনি এই মেসের এডমিন\n"
            f"💰 বর্তমান ব্যালেন্স: 0.00 টাকা\n\n"
            f"এখন ইউজার যোগ করুন এবং ডিপোজিট শুরু করুন!{extra_hint}"
        )
        await show_main_menu(update.message, mess_id, user_id)
    
    elif action.startswith('end_mess_'):
        mess_id = int(action.replace('end_mess_', ''))
        if not is_admin(update.effective_user.id, mess_id):
            context.user_data['action'] = None
            return
        try:
            end_date = text
            datetime.strptime(end_date, "%Y-%m-%d")
            complete_mess(mess_id, end_date)
            context.user_data['action'] = None
            await update.message.reply_text(f"✅ **{mess_display_label(get_mess_info(mess_id))} সম্পন্ন হয়েছে!**\n📅 শেষ তারিখ: {end_date}\n\nফাইনাল রিপোর্ট দেখতে '📄 ফাইনাল রিপোর্ট (PDF)' বাটনে ক্লিক করুন।")
            await show_main_menu(update.message, mess_id, update.effective_user.id)
        except ValueError:
            await update.message.reply_text("❌ ভুল ফরম্যাট! তারিখটি YYYY-MM-DD ফরম্যাটে দিন।")
    
    elif action.startswith('meal_custom_text|'):
        _, mess_id_s, meal = action.split('|')
        mess_id = int(mess_id_s)
        try:
            value = float(text.strip())
            if value < 0:
                raise ValueError
            context.user_data.setdefault('meal_values', {})[meal] = value
            context.user_data['action'] = None
            idx = MEAL_ORDER.index(meal)
            if idx + 1 < len(MEAL_ORDER):
                await ask_meal_step(update.message, context, mess_id, MEAL_ORDER[idx + 1], edit=False)
            else:
                username = context.user_data.get('meal_username') or (update.effective_user.username or update.effective_user.first_name)
                await _finish_meal_preset(update.message, context, mess_id, username)
        except ValueError:
            await update.message.reply_text("❌ দয়া করে ০ বা তার বেশি একটি সংখ্যা দিন (যেমন: 0, 0.5, 1):")
    
    elif action.startswith('mill_custom_date|'):
        mess_id = int(action.split('|')[1])
        if not is_admin(update.effective_user.id, mess_id):
            context.user_data['action'] = None
            return
        try:
            mill_date = text.strip()
            datetime.strptime(mill_date, "%Y-%m-%d")
            context.user_data['action'] = None
            await start_mill_exception_flow(update.message, context, mess_id, mill_date)
        except ValueError:
            await update.message.reply_text("❌ ভুল ফরম্যাট! তারিখটি YYYY-MM-DD ফরম্যাটে দিন (যেমন: 2026-01-15)।")
    
    elif action.startswith('mill_custom_value|'):
        _, mess_id_s, username = action.split('|')
        mess_id = int(mess_id_s)
        try:
            value = float(text.strip())
            if value < 0:
                raise ValueError
            context.user_data.setdefault('mill_exceptions', {})[username] = value
            context.user_data['action'] = None
            await show_mill_exception_menu(update.message, context, mess_id, edit=False)
        except ValueError:
            await update.message.reply_text("❌ দয়া করে ০ বা তার বেশি একটি সংখ্যা দিন (যেমন: 0, 0.5, 1, 2):")
    
    elif action.startswith('add_user_'):
        mess_id = int(action.replace('add_user_', ''))
        if not is_admin(update.effective_user.id, mess_id):
            await update.message.reply_text("❌ শুধুমাত্র এডমিন ইউজার যোগ করতে পারবেন!")
            context.user_data['action'] = None
            return
        username = text.replace('@', '').strip()
        resolved_id = None
        try:
            chat = await context.bot.get_chat(f"@{username}")
            resolved_id = chat.id
        except Exception:
            resolved_id = None
        if add_user(username, mess_id, user_id=resolved_id):
            await update.message.reply_text(f"✅ @{username} যোগ করা হয়েছে!")
        else:
            await update.message.reply_text(f"❌ @{username} আগেই আছে!")
        context.user_data['action'] = None
        await show_main_menu(update.message, mess_id, update.effective_user.id)
    
    elif action.startswith('deposit_amount_'):
        mess_id = int(action.replace('deposit_amount_', ''))
        try:
            amount = float(text)
            username = context.user_data.get('deposit_user')
            add_deposit(username, amount, mess_id)
            await update.message.reply_text(f"✅ @{username} এর {amount:.2f} টাকা ডিপোজিট হয়েছে!\n💰 বর্তমান ব্যালেন্স: {get_balance(mess_id):.2f} টাকা")
            context.user_data['action'] = None
            await show_main_menu(update.message, mess_id, update.effective_user.id)
        except ValueError:
            await update.message.reply_text("❌ দয়া করে সঠিক সংখ্যা দিন!")
    
    elif action.startswith('expense_desc_'):
        mess_id = int(action.replace('expense_desc_', ''))
        context.user_data['expense_desc'] = text
        context.user_data['expense_mess_id'] = mess_id
        context.user_data['action'] = f'expense_amount_{mess_id}'
        await update.message.reply_text(f"💸 '{text}' খরচের পরিমাণ লিখুন (শুধু সংখ্যা):")
    
    elif action.startswith('expense_amount_'):
        mess_id = int(action.replace('expense_amount_', ''))
        if not is_admin(update.effective_user.id, mess_id):
            await update.message.reply_text("❌ শুধুমাত্র এডমিন খরচ যোগ করতে পারবেন!")
            context.user_data['action'] = None
            return
        try:
            amount = float(text)
            desc = context.user_data.get('expense_desc')
            add_expense(desc, amount, mess_id, update.message.from_user.username or "User")
            await update.message.reply_text(f"✅ '{desc}' খরচ {amount:.2f} টাকা যোগ হয়েছে!\n💰 বর্তমান ব্যালেন্স: {get_balance(mess_id):.2f} টাকা")
            context.user_data['action'] = None
            await show_main_menu(update.message, mess_id, update.effective_user.id)
        except ValueError:
            await update.message.reply_text("❌ দয়া করে সঠিক সংখ্যা দিন!")

async def myaccounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    messes = get_user_messes(user_id)
    if not messes:
        await update.message.reply_text("📭 আপনি কোনো মেসের সাথে যুক্ত নন। /new দিয়ে নতুন শুরু করুন।")
        return
    keyboard = []
    for mess in messes:
        status = "✅" if mess['end_date'] != 'চলমান' else "🟢"
        keyboard.append([InlineKeyboardButton(
            f"{status} #{mess['id']} - {mess['month_name']}",
            callback_data=f'switch_mess_{mess["id"]}'
        )])
    keyboard.append([InlineKeyboardButton("➕ নতুন মেস", callback_data='new_mess')])
    await update.message.reply_text("📂 **আপনার মেসসমূহ:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def _active_mess_or_prompt(update: Update):
    """বর্তমান সক্রিয় মেস রিটার্ন করে; না থাকলে ইউজারকে জানিয়ে None রিটার্ন করে।"""
    user_id = update.effective_user.id
    mess_id = get_current_mess_id(user_id)
    if not mess_id or not get_mess_info(mess_id) or not is_member_or_admin(user_id, mess_id):
        await update.message.reply_text(
            "❌ কোনো সক্রিয় মেস নেই।\n\n📂 /myaccounts দিয়ে বেছে নিন অথবা 🆕 /new দিয়ে নতুন শুরু করুন।"
        )
        return None
    return mess_id

async def adduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id, mess_id):
        await update.message.reply_text("❌ শুধুমাত্র এডমিন ইউজার যোগ করতে পারবেন!")
        return
    if is_mess_completed(mess_id):
        await update.message.reply_text("❌ এই মেস সম্পন্ন হয়েছে! নতুন ইউজার যোগ করা যাবে না।")
        return
    context.user_data['action'] = f'add_user_{mess_id}'
    await update.message.reply_text("👤 **ইউজার যোগ করুন**\n\n@username লিখুন (যেমন: @rahim):")

async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    if is_mess_completed(mess_id):
        await update.message.reply_text("❌ এই মেস সম্পন্ন হয়েছে! ডিপোজিট করা যাবে না।")
        return
    users = get_users(mess_id)
    if not users:
        await update.message.reply_text("❌ কোনো ইউজার নেই! আগে /adduser দিয়ে ইউজার যোগ করুন।")
        return
    keyboard = []
    for username, full_name in users:
        keyboard.append([InlineKeyboardButton(f"@{username}", callback_data=f'deposit_user_{mess_id}_{username}')])
    await update.message.reply_text("👤 **কে ডিপোজিট করবেন?**", reply_markup=InlineKeyboardMarkup(keyboard))

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    text = build_summary_text(mess_id)
    await update.message.reply_text(text or "❌ মেস তথ্য পাওয়া যায়নি!", parse_mode='Markdown')

async def addexpense_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id, mess_id):
        await update.message.reply_text("❌ শুধুমাত্র এডমিন খরচ যোগ করতে পারবেন!")
        return
    if is_mess_completed(mess_id):
        await update.message.reply_text("❌ এই মেস সম্পন্ন হয়েছে! খরচ যোগ করা যাবে না।")
        return
    context.user_data['action'] = f'expense_desc_{mess_id}'
    await update.message.reply_text("📝 **খরচের বিবরণ লিখুন:**")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    await update.message.reply_text(build_history_text(mess_id))

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    await update.message.reply_text("⏳ **PDF রিপোর্ট তৈরি হচ্ছে...** দয়া করে অপেক্ষা করুন।")
    try:
        pdf_buffer = generate_pdf_report(mess_id)
        await update.message.reply_document(
            document=pdf_buffer,
            filename=f"mess_report_{mess_id}_{datetime.now().strftime('%Y%m%d')}.pdf",
            caption=f"📄 {mess_display_label(get_mess_info(mess_id))} এর ফাইনাল রিপোর্ট"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ PDF তৈরি করতে সমস্যা হয়েছে: {str(e)}")

MILL_QUICK_VALUES = [("নাই", "0"), ("হাফ", "0_5"), ("১", "1"), ("দেড়", "1_5"), ("২", "2")]

def _mill_val_to_float(v):
    return float(v.replace('_', '.'))

BD_TZ = ZoneInfo("Asia/Dhaka")

async def auto_nightly_digest_job(context: ContextTypes.DEFAULT_TYPE):
    """প্রতিদিন রাত ১১টায় (বাংলাদেশ সময়) চলে - পার্সোনাল বাদে সব ধরনের মেসের প্রতিটা সদস্যকে
    তাদের /summary ও /history অটোমেটিক পাঠিয়ে দেয়, যাতে সবাই দিন শেষে হিসাবটা দেখে নিতে পারে"""
    for mess in get_all_messes():
        if mess.get('mess_type') == 'personal':
            continue
        if is_mess_completed(mess['id']):
            continue
        members = get_users_with_ids(mess['id'])
        if not members:
            continue
        summary_text = build_summary_text(mess['id'])
        history_text = build_history_text(mess['id'])
        if not summary_text:
            continue
        for username, member_user_id in members:
            try:
                await context.bot.send_message(chat_id=member_user_id, text=f"🌙 রাত ১১টার দৈনিক আপডেট\n\n{summary_text}", parse_mode='Markdown')
                await context.bot.send_message(chat_id=member_user_id, text=history_text, parse_mode='Markdown')
            except Exception:
                pass

async def auto_apply_daily_mills_job(context: ContextTypes.DEFAULT_TYPE):
    """প্রতিদিন রাত ১০টায় (বাংলাদেশ সময়) চলে - যাদের সেদিনের মিল এখনো এন্ট্রি হয়নি
    (admin ম্যানুয়ালি কিছু করে থাকলে সেটা বাদে) তাদের জন্য নিজ নিজ প্রিসেট অনুযায়ী অটো-ফিল করে"""
    today = datetime.now(BD_TZ).strftime("%Y-%m-%d")
    for mess in get_all_messes():
        if mess.get('mess_type') != 'student':
            continue
        if is_mess_completed(mess['id']):
            continue
        if not get_users(mess['id']):
            continue
        filled = auto_fill_missing_mills_for_date(mess['id'], today)
        if not filled:
            continue
        lines = "\n".join(f"@{u}: {c:g} (প্রিসেট)" for u, c in filled)
        text = (
            f"🌙 <b>{mess_display_label(get_mess_info(mess['id']))} — {today}</b>\n\n"
            f"রাত ১০টায় অটো মিল এন্ট্রি হয়েছে (যাদের আজ ম্যানুয়াল এন্ট্রি হয়নি):\n{lines}\n\n"
            f"কারো ভুল হলে /mill_entry দিয়ে ঠিক করে নিতে পারবেন।"
        )
        for admin_user_id, _ in get_admins(mess['id']):
            try:
                await context.bot.send_message(chat_id=admin_user_id, text=text, parse_mode='HTML')
            except Exception:
                pass

def build_guide_text():
    return (
        "📖 <b>ব্যবহারবিধি — মেস ম্যানেজমেন্ট বট</b>\n\n"
        "🆕 <b>নতুন মেস শুরু</b>\n"
        "/new দিয়ে শুরু করুন। ৩ ধরনের মেস আছে:\n"
        "  🍽️ Normal — খরচ সবার মধ্যে মাথাপিছু ভাগ হয়\n"
        "  🎓 Student — প্রতিদিন কে কয়টা মিল খেলো তার হিসাবে খরচ ভাগ হয়\n"
        "  👤 Personal — শুধু নিজের হিসাব রাখার জন্য\n"
        "প্রতিটা মেস \"টাইপ মেস #নম্বর\" আকারে দেখা যায় (যেমন Student মেস #1) — নিজের প্রতিটা ধরনের মেস ১ থেকে গোনা হয়।\n\n"
        "👥 <b>সদস্য যোগ</b>\n"
        "/adduser দিয়ে @username যোগ করুন (এডমিন)।\n\n"
        "💰 <b>ডিপোজিট ও খরচ</b>\n"
        "/deposit — কে কত জমা দিলো তা লিখুন\n"
        "/addexpense — বাজার/খরচ যোগ করুন\n\n"
        "🎓 <b>স্টুডেন্ট মেস — মিল হিসাব</b>\n"
        "প্রতিটা সদস্য /meal_setting দিয়ে নিজের সকাল/দুপুর/রাতের মিল সংখ্যা একবার সেট করে রাখবে (যেমন সকাল ১/২, দুপুর ১, রাত ১)।\n"
        "প্রতি রাত ১০টায় বট নিজে থেকেই সবার প্রিসেট অনুযায়ী সেদিনের মিল সেভ করে দেয় — এডমিনকে কিছু করতে হয় না।\n"
        "কারো সেদিন মিল কম/বেশি/অনুপস্থিত থাকলে এডমিন /mill_entry দিয়ে শুধু সেই ব্যতিক্রমটুকু বসিয়ে দিলেই হয় (রাত ১০টার আগে); বাকিদের প্রিসেট অনুযায়ীই থেকে যায়।\n\n"
        "📊 <b>সারাংশ ও রিপোর্ট</b>\n"
        "/summary — এখন পর্যন্তের হিসাব (টেক্সট)\n"
        "/report — ফাইনাল PDF রিপোর্ট (ডিপোজিট, খরচ, সেটেলমেন্ট, স্টুডেন্ট মেসে দৈনিক মিল ব্রেকডাউনসহ)\n"
        "/history — সাম্প্রতিক লেনদেন\n"
        "🌙 Normal ও Student মেসে প্রতি রাত ১১টায় প্রতিটা সদস্যকে নিজে থেকেই /summary ও /history পাঠিয়ে দেয় বট (Personal মেসে এটা হয় না)।\n\n"
        "⚙️ <b>এডমিন</b>\n"
        "/admin — এডমিন প্যানেল (নতুন এডমিন বানানো, সদস্য রিমুভ)\n"
        "/endmess — মেস সম্পন্ন করে ফাইনাল রিপোর্ট তৈরি করুন\n\n"
        "🔀 একাধিক মেসের সাথে যুক্ত থাকলে /start দিয়ে যেকোনো একটায় সুইচ করা যায়।"
    )

async def guide_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_guide_text(), parse_mode='HTML')

async def mill_entry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id, mess_id):
        await update.message.reply_text("❌ শুধুমাত্র এডমিন মিল এন্ট্রি দিতে পারবেন!")
        return
    mess_info = get_mess_info(mess_id)
    if mess_info.get('mess_type') != 'student':
        await update.message.reply_text("❌ এই মেসটি স্টুডেন্ট (মিল হিসাব) ধরনের নয়।")
        return
    if is_mess_completed(mess_id):
        await update.message.reply_text("❌ এই মেস সম্পন্ন হয়েছে! মিল এন্ট্রি দেওয়া যাবে না।")
        return
    if not get_users(mess_id):
        await update.message.reply_text("❌ কোনো ইউজার নেই! আগে /adduser দিয়ে ইউজার যোগ করুন।")
        return
    today = datetime.now().strftime("%Y-%m-%d")
    keyboard = [
        [InlineKeyboardButton(f"📅 আজকে ({today})", callback_data=f'mill_date_today|{mess_id}')],
        [InlineKeyboardButton("🗓️ অন্য তারিখ লিখবো", callback_data=f'mill_date_custom|{mess_id}')],
        [InlineKeyboardButton("📖 ব্যবহারবিধি", callback_data='show_guide')]
    ]
    await update.message.reply_text(
        "🍽️ **মিল এন্ট্রি**\n\nকোন তারিখের জন্য মিল এন্ট্রি দিতে চান?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def start_mill_exception_flow(update_or_query, context, mess_id, mill_date, via_edit=False):
    context.user_data['mill_mess_id'] = mess_id
    context.user_data['mill_date'] = mill_date
    context.user_data['mill_exceptions'] = {}
    keyboard = [
        [InlineKeyboardButton("✅ হ্যাঁ, ব্যতিক্রম আছে", callback_data=f'mill_exc_yes|{mess_id}')],
        [InlineKeyboardButton("🚀 না, সবাই নিজের প্রিসেট অনুযায়ী খেয়েছে", callback_data=f'mill_exc_no|{mess_id}')]
    ]
    text = f"📅 তারিখ: **{mill_date}**\n\nকারো মিল সংখ্যায় ব্যতিক্রম (কম/বেশি/অনুপস্থিত) আছে কি?"
    if via_edit:
        await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_mill_exception_menu(message_or_query, context, mess_id, edit=True):
    users = get_users(mess_id)
    exceptions = context.user_data.get('mill_exceptions', {})
    mill_date = context.user_data.get('mill_date')
    keyboard = []
    for username, full_name in users:
        preset_total = get_preset_total(mess_id, username)
        if username in exceptions:
            mark = f" ✏️ {exceptions[username]:g}"
        else:
            mark = f" (প্রিসেট: {preset_total:g})"
        keyboard.append([InlineKeyboardButton(f"@{username}{mark}", callback_data=f'mill_user|{mess_id}|{username}')])
    keyboard.append([InlineKeyboardButton("✅ সম্পন্ন — বাকিরা নিজের প্রিসেট", callback_data=f'mill_confirm|{mess_id}')])
    text = f"📅 তারিখ: **{mill_date}**\n\nযাদের আজ ব্যতিক্রম আছে তাদের নামে ট্যাপ করুন (নাহলে যার যার সকাল+দুপুর+রাত প্রিসেট অনুযায়ী মিল বসবে)।\nবাছাই শেষে ✅ চাপুন।"
    if edit and hasattr(message_or_query, 'edit_message_text'):
        await message_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

MEAL_LABELS = {'breakfast': '🌅 সকাল', 'lunch': '☀️ দুপুর', 'dinner': '🌙 রাত'}
MEAL_ORDER = ['breakfast', 'lunch', 'dinner']

async def ask_meal_step(message_or_query, context, mess_id, meal, edit=False):
    label = MEAL_LABELS[meal]
    keyboard = [
        [InlineKeyboardButton(l, callback_data=f'meal_step|{mess_id}|{meal}|{v}') for l, v in MILL_QUICK_VALUES[:3]],
        [InlineKeyboardButton(l, callback_data=f'meal_step|{mess_id}|{meal}|{v}') for l, v in MILL_QUICK_VALUES[3:]],
        [InlineKeyboardButton("✍️ অন্য সংখ্যা", callback_data=f'meal_custom|{mess_id}|{meal}')]
    ]
    text = f"{label} এ সাধারণত কয়টা মিল খাও?"
    if edit:
        await message_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def _finish_meal_preset(reply_target, context, mess_id, username):
    vals = context.user_data.get('meal_values', {})
    b, l, d = vals.get('breakfast', 1.0), vals.get('lunch', 1.0), vals.get('dinner', 1.0)
    set_meal_preset(mess_id, username, b, l, d)
    total = b + l + d
    context.user_data['meal_values'] = {}
    text = (
        f"✅ <b>প্রিসেট সেভ হয়েছে!</b>\n\n"
        f"🌅 সকাল {b:g} + ☀️ দুপুর {l:g} + 🌙 রাত {d:g} = <b>দৈনিক {total:g} মিল</b>\n\n"
        f"এখন থেকে admin bulk এন্ট্রি করলে এটাই তোমার ডিফল্ট মিল হিসেবে বসবে। বদলাতে চাইলে আবার /meal_setting করো।"
    )
    if hasattr(reply_target, 'edit_message_text'):
        await reply_target.edit_message_text(text, parse_mode='HTML')
    else:
        await reply_target.reply_text(text, parse_mode='HTML')

async def meal_setting_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    mess_info = get_mess_info(mess_id)
    if mess_info.get('mess_type') != 'student':
        await update.message.reply_text("❌ এই মেসটি স্টুডেন্ট (মিল হিসাব) ধরনের নয়, প্রিসেট প্রযোজ্য নয়।")
        return
    user_id = update.effective_user.id
    if not is_member_or_admin(user_id, mess_id):
        await update.message.reply_text("❌ আপনি এই মেসের সদস্য নন!")
        return
    tg_username = update.effective_user.username or update.effective_user.first_name
    context.user_data['meal_mess_id'] = mess_id
    context.user_data['meal_username'] = tg_username
    context.user_data['meal_values'] = {}
    current = get_meal_preset(mess_id, tg_username)
    await update.message.reply_text(
        f"🍽️ **তোমার দৈনিক মিল প্রিসেট সেট করো**\n\n"
        f"বর্তমান: সকাল {current['breakfast']:g}, দুপুর {current['lunch']:g}, রাত {current['dinner']:g}\n\n"
        f"একে একে সকাল/দুপুর/রাতের মিল সংখ্যা বেছে নাও:",
        parse_mode='Markdown'
    )
    await ask_meal_step(update.message, context, mess_id, 'breakfast')

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id, mess_id):
        await update.message.reply_text("❌ আপনি এডমিন নন!")
        return
    keyboard = [
        [InlineKeyboardButton("👑 নতুন এডমিন বানান", callback_data=f'promote_user_{mess_id}')],
        [InlineKeyboardButton("👤 এডমিন বাদ দিন", callback_data=f'demote_admin_{mess_id}')],
        [InlineKeyboardButton("🗑️ ইউজার রিমুভ করুন", callback_data=f'remove_user_{mess_id}')]
    ]
    await update.message.reply_text("⚙️ **এডমিন ম্যানেজমেন্ট**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def endmess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mess_id = await _active_mess_or_prompt(update)
    if not mess_id:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id, mess_id):
        await update.message.reply_text("❌ শুধুমাত্র এডমিন মেস শেষ করতে পারবেন!")
        return
    context.user_data['action'] = f'end_mess_{mess_id}'
    await update.message.reply_text("📅 **মেস শেষ করার তারিখ লিখুন** (যেমন: 2026-01-31):\n\n⚠️ মনে রাখবেন: একবার শেষ করলে আর ডিপোজিট/খরচ যোগ করা যাবে না!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ <b>সাহায্য — সব কমান্ড</b>\n\n"
        "🏠 /start — বট শুরু করুন বা মেনু দেখুন\n"
        "🆕 /new — নতুন মেস/হিসাব শুরু করুন\n"
        "📂 /myaccounts — আপনার সব মেস দেখুন/পরিবর্তন করুন\n"
        "👥 /adduser — ইউজার যোগ করুন (এডমিন)\n"
        "💰 /deposit — ডিপোজিট করুন\n"
        "💸 /addexpense — খরচ যোগ করুন (এডমিন)\n"
        "📊 /summary — সারাংশ দেখুন\n"
        "📋 /history — লেনদেনের ইতিহাস\n"
        "📄 /report — ফাইনাল রিপোর্ট (PDF)\n"
        "🍽️ /mill_entry — দিনের মিল এন্ট্রি (স্টুডেন্ট মেস, এডমিন)\n"
        "🌅 /meal_setting — নিজের সকাল/দুপুর/রাতের মিল প্রিসেট সেট করুন (স্টুডেন্ট মেস)\n"
        "⚙️ /admin — এডমিন ম্যানেজমেন্ট (এডমিন)\n"
        "🔚 /endmess — মেস শেষ করুন (এডমিন)\n"
        "📖 /guide — বিস্তারিত ব্যবহারবিধি\n"
        "❓ /help — এই সাহায্য বার্তা\n\n"
        "প্রতিটা মেসের নিজস্ব এডমিন থাকে। যে মেস শুরু করে সে-ই সেই মেসের এডমিন।",
        parse_mode='HTML'
    )

# ============ মেইন ফাংশন ============
async def post_init(application: Application):
    # ৪-ডট মেনু বাটনে (Telegram commands menu) এই কমান্ডগুলো দেখাবে
    await application.bot.set_my_commands([
        BotCommand("start", "🏠 মেনু দেখুন"),
        BotCommand("deposit", "💰 ডিপোজিট করুন"),
        BotCommand("summary", "📊 সারাংশ দেখুন"),
        BotCommand("addexpense", "💸 খরচ যোগ করুন"),
        BotCommand("history", "📋 লেনদেনের ইতিহাস"),
        BotCommand("report", "📄 ফাইনাল রিপোর্ট (PDF)"),
        BotCommand("mill_entry", "🍽️ দিনের মিল এন্ট্রি"),
        BotCommand("meal_setting", "🌅 আমার মিল প্রিসেট সেট করো"),
        BotCommand("adduser", "👥 ইউজার যোগ করুন"),
        BotCommand("admin", "⚙️ এডমিন ম্যানেজমেন্ট"),
        BotCommand("endmess", "🔚 মেস শেষ করুন"),
        BotCommand("myaccounts", "📂 আমার মেসসমূহ"),
        BotCommand("new", "🆕 নতুন মেস শুরু করুন"),
        BotCommand("help", "❓ সাহায্য"),
        BotCommand("guide", "📖 ব্যবহারবিধি")
    ])

def main():
    init_db()
    TOKEN = os.environ.get('BOT_TOKEN') or "YOUR_BOT_TOKEN_HERE"
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_mess))
    app.add_handler(CommandHandler("myaccounts", myaccounts_command))
    app.add_handler(CommandHandler("adduser", adduser_command))
    app.add_handler(CommandHandler("deposit", deposit_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("addexpense", addexpense_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("mill_entry", mill_entry_command))
    app.add_handler(CommandHandler("meal_setting", meal_setting_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("endmess", endmess_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("guide", guide_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    if app.job_queue is not None:
        app.job_queue.run_daily(auto_apply_daily_mills_job, time=dtime(22, 0, tzinfo=BD_TZ), name="auto_apply_daily_mills")
        app.job_queue.run_daily(auto_nightly_digest_job, time=dtime(23, 0, tzinfo=BD_TZ), name="auto_nightly_digest")
    else:
        print("⚠️ job-queue ইনস্টল করা নেই — রাত ১০/১১টার অটো জব চালু হয়নি। requirements.txt-এ 'python-telegram-bot[job-queue]' যোগ করুন।")
    
    print("🤖 বট চালু হয়েছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
