import os
import sqlite3
from datetime import datetime
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
        c.execute("SELECT start_date, end_date, month_name FROM mess_settings WHERE key = ?", (f'mess_{mess_id}_info',))
        result = c.fetchone()
        conn.close()
        if result:
            return {
                'start_date': result[0] or 'অজানা',
                'end_date': result[1] or 'চলমান',
                'month_name': result[2] or 'অজানা'
            }
        return None
    except:
        conn.close()
        return None

def save_mess_info(mess_id, start_date, end_date, month_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO mess_settings (key, value, start_date, end_date, month_name) VALUES (?, ?, ?, ?, ?)", 
              (f'mess_{mess_id}_info', f'{start_date}|{end_date}|{month_name}', start_date, end_date, month_name))
    conn.commit()
    conn.close()

def get_all_messes():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, start_date, end_date, month_name FROM mess_settings WHERE key LIKE 'mess_%_info'")
    results = c.fetchall()
    conn.close()
    
    messes = []
    for key, start_date, end_date, month_name in results:
        mess_id = key.split('_')[1]
        messes.append({
            'id': int(mess_id),
            'start_date': start_date or 'অজানা',
            'end_date': end_date or 'চলমান',
            'month_name': month_name or 'অজানা'
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
    story.append(bn_text(f"মেস নম্বর: #{mess_id}", size=12))
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
    
    # ফুটার
    story.append(Spacer(1, 30))
    story.append(bn_text(f"জেনারেট: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", size=9, color=(128, 128, 128)))
    story.append(Spacer(1, 3))
    story.append(bn_text("© মেসের হিসাব বট", size=9, color=(128, 128, 128)))
    
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
                f"{status} #{mess['id']} - {mess['month_name']}",
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

async def new_mess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("📅 **মেস শুরুর তারিখ লিখুন** (যেমন: 2026-01-01):")
    else:
        await update.message.reply_text("📅 **মেস শুরুর তারিখ লিখুন** (যেমন: 2026-01-01):")
    context.user_data['action'] = 'new_mess_date'

async def show_main_menu(message, mess_id, user_id=None):
    mess_info = get_mess_info(mess_id)
    if not mess_info:
        await message.reply_text("❌ মেস তথ্য পাওয়া যায়নি! /start দিয়ে নতুন শুরু করুন।")
        return
    
    is_completed = is_mess_completed(mess_id)
    status = "✅ সম্পন্ন" if is_completed else "🟢 চলমান"
    
    await message.reply_text(
        f"📆 **মেস ইনফো**\n"
        f"🆔 মেস #{mess_id}\n"
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
    
    elif data == 'old_messes':
        messes = get_user_messes(user_id)
        if not messes:
            await query.edit_message_text("📭 আপনি কোনো মেসের সাথে যুক্ত নন। /start দিয়ে নতুন শুরু করুন।")
            return
        keyboard = []
        for mess in messes:
            status = "✅" if mess['end_date'] != 'চলমান' else "🟢"
            keyboard.append([InlineKeyboardButton(
                f"{status} #{mess['id']} - {mess['month_name']} ({mess['start_date']} - {mess['end_date']})", 
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
        await query.edit_message_text(f"✅ মেস #{mess_id} এ স্যুইচ করা হয়েছে!")
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
                caption=f"📄 মেস #{mess_id} এর ফাইনাল রিপোর্ট"
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
    text += f"🆔 মেস #{mess_id}\n"
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
    
    text = f"📋 **সর্বশেষ লেনদেন** (মেস #{mess_id})\n"
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
        mess_id = get_next_mess_id()
        save_mess_info(mess_id, start_date, 'চলমান', month_name)
        user_id = update.effective_user.id
        tg_username = update.effective_user.username or update.effective_user.first_name
        add_admin(user_id, mess_id, tg_username)
        add_user(tg_username, mess_id, user_id=user_id)
        set_current_mess_id(user_id, mess_id)
        context.user_data['action'] = None
        await update.message.reply_text(
            f"✅ **নতুন মেস শুরু হয়েছে!**\n\n"
            f"🆔 মেস নম্বর: #{mess_id}\n"
            f"📅 শুরুর তারিখ: {start_date}\n"
            f"📌 মাস: {month_name}\n"
            f"👑 আপনি এই মেসের এডমিন\n"
            f"💰 বর্তমান ব্যালেন্স: 0.00 টাকা\n\n"
            f"এখন ইউজার যোগ করুন এবং ডিপোজিট শুরু করুন!"
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
            await update.message.reply_text(f"✅ **মেস #{mess_id} সম্পন্ন হয়েছে!**\n📅 শেষ তারিখ: {end_date}\n\nফাইনাল রিপোর্ট দেখতে '📄 ফাইনাল রিপোর্ট (PDF)' বাটনে ক্লিক করুন।")
            await show_main_menu(update.message, mess_id, update.effective_user.id)
        except ValueError:
            await update.message.reply_text("❌ ভুল ফরম্যাট! তারিখটি YYYY-MM-DD ফরম্যাটে দিন।")
    
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
            caption=f"📄 মেস #{mess_id} এর ফাইনাল রিপোর্ট"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ PDF তৈরি করতে সমস্যা হয়েছে: {str(e)}")

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
        "❓ **সাহায্য — সব কমান্ড**\n\n"
        "🏠 /start — বট শুরু করুন বা মেনু দেখুন\n"
        "🆕 /new — নতুন মেস/হিসাব শুরু করুন\n"
        "📂 /myaccounts — আপনার সব মেস দেখুন/পরিবর্তন করুন\n"
        "👥 /adduser — ইউজার যোগ করুন (এডমিন)\n"
        "💰 /deposit — ডিপোজিট করুন\n"
        "💸 /addexpense — খরচ যোগ করুন (এডমিন)\n"
        "📊 /summary — সারাংশ দেখুন\n"
        "📋 /history — লেনদেনের ইতিহাস\n"
        "📄 /report — ফাইনাল রিপোর্ট (PDF)\n"
        "⚙️ /admin — এডমিন ম্যানেজমেন্ট (এডমিন)\n"
        "🔚 /endmess — মেস শেষ করুন (এডমিন)\n"
        "❓ /help — এই সাহায্য বার্তা\n\n"
        "প্রতিটা মেসের নিজস্ব এডমিন থাকে। যে মেস শুরু করে সে-ই সেই মেসের এডমিন।",
        parse_mode='Markdown'
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
        BotCommand("adduser", "👥 ইউজার যোগ করুন"),
        BotCommand("admin", "⚙️ এডমিন ম্যানেজমেন্ট"),
        BotCommand("endmess", "🔚 মেস শেষ করুন"),
        BotCommand("myaccounts", "📂 আমার মেসসমূহ"),
        BotCommand("new", "🆕 নতুন মেস শুরু করুন"),
        BotCommand("help", "❓ সাহায্য")
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
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("endmess", endmess_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 বট চালু হয়েছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
