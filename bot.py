import os
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
import io

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

def get_current_mess_id():
    mess_id = get_setting('current_mess_id')
    if mess_id:
        return int(mess_id)
    return None

def set_current_mess_id(mess_id):
    set_setting('current_mess_id', str(mess_id))

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

def get_users(mess_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, full_name FROM users WHERE mess_id = ?", (mess_id,))
    users = c.fetchall()
    conn.close()
    return users

def add_user(username, mess_id, full_name=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, full_name, mess_id, added_date) VALUES (?, ?, ?, ?)", 
                 (username, full_name or username, mess_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

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
    
    deposits = get_deposits_with_date(mess_id, start_date, end_date)
    expenses = get_expenses_with_date(mess_id, start_date, end_date)
    
    total_dep = sum(d[1] for d in deposits)
    total_exp = sum(e[1] for e in expenses)
    balance = total_dep - total_exp
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=72)
    styles = getSampleStyleSheet()
    story = []
    
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=24, textColor=colors.darkblue, alignment=TA_CENTER, spaceAfter=30)
    story.append(Paragraph("📄 মেসের ফাইনাল রিপোর্ট", title_style))
    
    info_style = ParagraphStyle('InfoStyle', parent=styles['Normal'], fontSize=12, textColor=colors.black, alignment=TA_LEFT, spaceAfter=6)
    story.append(Paragraph(f"<b>মেস নম্বর:</b> #{mess_id}", info_style))
    story.append(Paragraph(f"<b>মাস:</b> {mess_info['month_name']}", info_style))
    story.append(Paragraph(f"<b>সময়কাল:</b> {start_date} থেকে {end_date}", info_style))
    story.append(Paragraph(f"<b>জেনারেট:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}", info_style))
    story.append(Spacer(1, 20))
    
    story.append(Paragraph("<b>👥 ইউজার ভাইস ডিপোজিট</b>", styles['Heading3']))
    story.append(Spacer(1, 10))
    
    user_data = [["ইউজারনেম", "ডিপোজিট (টাকা)"]]
    for username, full_name in users:
        dep = get_user_deposits_with_date(username, mess_id, start_date, end_date)
        user_data.append([f"@{username}", f"{dep:.2f}"])
    
    user_table = Table(user_data, colWidths=[2.5*inch, 2*inch])
    user_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(user_table)
    story.append(Spacer(1, 20))
    
    story.append(Paragraph("<b>📊 সারাংশ</b>", styles['Heading3']))
    story.append(Spacer(1, 10))
    
    summary_data = [
        ["বিবরণ", "পরিমাণ (টাকা)"],
        ["মোট ডিপোজিট", f"{total_dep:.2f}"],
        ["মোট খরচ", f"{total_exp:.2f}"],
        ["অবশিষ্ট", f"{balance:.2f}"]
    ]
    
    summary_table = Table(summary_data, colWidths=[2.5*inch, 2*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -2), colors.lightgrey),
        ('BACKGROUND', (0, -1), (-1, -1), colors.yellow),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 20))
    
    if expenses and len(expenses) <= 20:
        story.append(Paragraph("<b>📋 খরচের বিস্তারিত</b>", styles['Heading3']))
        story.append(Spacer(1, 10))
        expense_data = [["বিবরণ", "পরিমাণ (টাকা)", "তারিখ", "যোগকারী"]]
        for desc, amount, date, added_by in expenses:
            expense_data.append([desc, f"{amount:.2f}", date[:10], f"@{added_by}"])
        expense_table = Table(expense_data, colWidths=[2*inch, 1.2*inch, 1.5*inch, 1.2*inch])
        expense_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkred),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 9)
        ]))
        story.append(expense_table)
    
    if deposits and len(deposits) <= 20:
        story.append(Spacer(1, 20))
        story.append(Paragraph("<b>💰 ডিপোজিটের বিস্তারিত</b>", styles['Heading3']))
        story.append(Spacer(1, 10))
        deposit_data = [["ইউজার", "পরিমাণ (টাকা)", "তারিখ", "নোট"]]
        for username, amount, date, note in deposits:
            deposit_data.append([f"@{username}", f"{amount:.2f}", date[:10], note or "-"])
        deposit_table = Table(deposit_data, colWidths=[1.5*inch, 1.2*inch, 1.5*inch, 1.5*inch])
        deposit_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTSIZE', (0, 1), (-1, -1), 9)
        ]))
        story.append(deposit_table)
    
    story.append(Spacer(1, 30))
    story.append(Paragraph(f"<i>জেনারেট: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>", styles['Normal']))
    story.append(Paragraph("<i>© মেসের হিসাব বট</i>", styles['Normal']))
    
    doc.build(story)
    buffer.seek(0)
    return buffer

# ============ টেলিগ্রাম হ্যান্ডলার ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_mess_id = get_current_mess_id()
    
    if current_mess_id:
        mess_info = get_mess_info(current_mess_id)
        if mess_info:
            await show_main_menu(update.message, current_mess_id)
            return
    
    keyboard = [
        [InlineKeyboardButton("🆕 নতুন মেস শুরু করুন", callback_data='new_mess')],
        [InlineKeyboardButton("📋 পুরাতন মেস দেখুন", callback_data='old_messes')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🏠 **মেসের হিসাব বট**\n\n"
        "বর্তমানে কোনো মেস চলমান নেই।\n"
        "আপনি চাইলে:",
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

async def show_main_menu(message, mess_id):
    mess_info = get_mess_info(mess_id)
    if not mess_info:
        await message.reply_text("❌ মেস তথ্য পাওয়া যায়নি! /start দিয়ে নতুন শুরু করুন।")
        return
    
    is_completed = is_mess_completed(mess_id)
    status = "✅ সম্পন্ন" if is_completed else "🟢 চলমান"
    
    keyboard = [
        [InlineKeyboardButton("👥 ইউজার যোগ করুন", callback_data=f'add_user_{mess_id}')],
        [InlineKeyboardButton("💰 ডিপোজিট করুন", callback_data=f'deposit_{mess_id}')],
        [InlineKeyboardButton("💸 খরচ যোগ করুন", callback_data=f'add_expense_{mess_id}')],
        [InlineKeyboardButton("📊 সারাংশ দেখুন", callback_data=f'summary_{mess_id}')],
        [InlineKeyboardButton("📋 লেনদেনের ইতিহাস", callback_data=f'history_{mess_id}')],
        [InlineKeyboardButton("📄 ফাইনাল রিপোর্ট (PDF)", callback_data=f'pdf_report_{mess_id}')],
        [InlineKeyboardButton("📂 মেস পরিবর্তন করুন", callback_data='change_mess')]
    ]
    
    if not is_completed:
        keyboard.append([InlineKeyboardButton("🔚 মেস শেষ করুন", callback_data=f'end_mess_{mess_id}')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(
        f"📆 **মেস ইনফো**\n"
        f"🆔 মেস #{mess_id}\n"
        f"📅 শুরু: {mess_info['start_date']}\n"
        f"📅 শেষ: {mess_info['end_date']}\n"
        f"📌 মাস: {mess_info['month_name']}\n"
        f"📊 স্ট্যাটাস: {status}\n"
        f"💰 ব্যালেন্স: {get_balance(mess_id):.2f} টাকা\n\n"
        f"নিচের অপশন থেকে বেছে নিন:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == 'new_mess':
        await new_mess(update, context)
    
    elif data == 'old_messes':
        messes = get_all_messes()
        if not messes:
            await query.edit_message_text("📭 কোনো পুরাতন মেস নেই। /start দিয়ে নতুন শুরু করুন।")
            return
        keyboard = []
        for mess in messes:
            status = "✅" if mess['end_date'] != 'চলমান' else "🟢"
            keyboard.append([InlineKeyboardButton(
                f"{status} #{mess['id']} - {mess['month_name']} ({mess['start_date']} - {mess['end_date']})", 
                callback_data=f'switch_mess_{mess["id"]}'
            )])
        keyboard.append([InlineKeyboardButton("🔙 ব্যাক", callback_data='back_start')])
        await query.edit_message_text("📋 **পুরাতন মেসসমূহ:**\n\nনিচ থেকে একটি বেছে নিন:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data.startswith('switch_mess_'):
        mess_id = int(data.replace('switch_mess_', ''))
        set_current_mess_id(mess_id)
        await query.edit_message_text(f"✅ মেস #{mess_id} এ স্যুইচ করা হয়েছে!")
        await show_main_menu(query.message, mess_id)
    
    elif data == 'back_start':
        await start(update, context)
    
    elif data == 'change_mess':
        messes = get_all_messes()
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
        if is_mess_completed(mess_id):
            await query.edit_message_text("❌ এই মেস সম্পন্ন হয়েছে! নতুন ইউজার যোগ করা যাবে না।")
            return
        context.user_data['action'] = f'add_user_{mess_id}'
        await query.edit_message_text("👤 **ইউজার যোগ করুন**\n\n@username লিখুন (যেমন: @rahim):")
    
    elif data.startswith('deposit_'):
        mess_id = int(data.replace('deposit_', ''))
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
    
    elif data.startswith('deposit_user_'):
        parts = data.split('_')
        mess_id = int(parts[2])
        username = parts[3]
        context.user_data['deposit_user'] = username
        context.user_data['deposit_mess_id'] = mess_id
        context.user_data['action'] = f'deposit_amount_{mess_id}'
        await query.edit_message_text(f"💵 @{username} এর ডিপোজিটের পরিমাণ লিখুন (শুধু সংখ্যা):")
    
    elif data.startswith('add_expense_'):
        mess_id = int(data.replace('add_expense_', ''))
        if is_mess_completed(mess_id):
            await query.edit_message_text("❌ এই মেস সম্পন্ন হয়েছে! খরচ যোগ করা যাবে না।")
            return
        context.user_data['action'] = f'expense_desc_{mess_id}'
        await query.edit_message_text("📝 **খরচের বিবরণ লিখুন:**")
    
    elif data.startswith('summary_'):
        mess_id = int(data.replace('summary_', ''))
        await show_summary(query, mess_id)
    
    elif data.startswith('history_'):
        mess_id = int(data.replace('history_', ''))
        await show_history(query, mess_id)
    
    elif data.startswith('pdf_report_'):
        mess_id = int(data.replace('pdf_report_', ''))
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
        await query.edit_message_text("📅 **মেস শেষ করার তারিখ লিখুন** (যেমন: 2026-01-31):\n\n⚠️ মনে রাখবেন: একবার শেষ করলে আর ডিপোজিট/খরচ যোগ করা যাবে না!")
        context.user_data['action'] = f'end_mess_{mess_id}'
    
    elif data.startswith('back_main_'):
        mess_id = int(data.replace('back_main_', ''))
        await show_main_menu(query.message, mess_id)

async def show_summary(query, mess_id):
    users = get_users(mess_id)
    total_dep = get_total_deposits(mess_id)
    total_exp = get_total_expenses(mess_id)
    balance = get_balance(mess_id)
    mess_info = get_mess_info(mess_id)
    
    if not mess_info:
        await query.edit_message_text("❌ মেস তথ্য পাওয়া যায়নি!")
        return
    
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
    
    await query.edit_message_text(text, parse_mode='Markdown')

async def show_history(query, mess_id):
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
        set_current_mess_id(mess_id)
        context.user_data['action'] = None
        await update.message.reply_text(
            f"✅ **নতুন মেস শুরু হয়েছে!**\n\n"
            f"🆔 মেস নম্বর: #{mess_id}\n"
            f"📅 শুরুর তারিখ: {start_date}\n"
            f"📌 মাস: {month_name}\n"
            f"💰 বর্তমান ব্যালেন্স: 0.00 টাকা\n\n"
            f"এখন ইউজার যোগ করুন এবং ডিপোজিট শুরু করুন!"
        )
        await show_main_menu(update.message, mess_id)
    
    elif action.startswith('end_mess_'):
        mess_id = int(action.replace('end_mess_', ''))
        try:
            end_date = text
            datetime.strptime(end_date, "%Y-%m-%d")
            complete_mess(mess_id, end_date)
            context.user_data['action'] = None
            await update.message.reply_text(f"✅ **মেস #{mess_id} সম্পন্ন হয়েছে!**\n📅 শেষ তারিখ: {end_date}\n\nফাইনাল রিপোর্ট দেখতে '📄 ফাইনাল রিপোর্ট (PDF)' বাটনে ক্লিক করুন।")
            await show_main_menu(update.message, mess_id)
        except ValueError:
            await update.message.reply_text("❌ ভুল ফরম্যাট! তারিখটি YYYY-MM-DD ফরম্যাটে দিন।")
    
    elif action.startswith('add_user_'):
        mess_id = int(action.replace('add_user_', ''))
        username = text.replace('@', '').strip()
        if add_user(username, mess_id):
            await update.message.reply_text(f"✅ @{username} যোগ করা হয়েছে!")
        else:
            await update.message.reply_text(f"❌ @{username} আগেই আছে!")
        context.user_data['action'] = None
        await show_main_menu(update.message, mess_id)
    
    elif action.startswith('deposit_amount_'):
        mess_id = int(action.replace('deposit_amount_', ''))
        try:
            amount = float(text)
            username = context.user_data.get('deposit_user')
            add_deposit(username, amount, mess_id)
            await update.message.reply_text(f"✅ @{username} এর {amount:.2f} টাকা ডিপোজিট হয়েছে!\n💰 বর্তমান ব্যালেন্স: {get_balance(mess_id):.2f} টাকা")
            context.user_data['action'] = None
            await show_main_menu(update.message, mess_id)
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
        try:
            amount = float(text)
            desc = context.user_data.get('expense_desc')
            add_expense(desc, amount, mess_id, update.message.from_user.username or "User")
            await update.message.reply_text(f"✅ '{desc}' খরচ {amount:.2f} টাকা যোগ হয়েছে!\n💰 বর্তমান ব্যালেন্স: {get_balance(mess_id):.2f} টাকা")
            context.user_data['action'] = None
            await show_main_menu(update.message, mess_id)
        except ValueError:
            await update.message.reply_text("❌ দয়া করে সঠিক সংখ্যা দিন!")

# ============ মেইন ফাংশন ============
def main():
    init_db()
    TOKEN = os.environ.get('BOT_TOKEN') or "YOUR_BOT_TOKEN_HERE"
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_mess))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 বট চালু হয়েছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
