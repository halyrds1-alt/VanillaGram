import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
import sqlite3
import json
import threading
import time
import os
import requests
from datetime import datetime, timedelta
import logging
import random

# ==================== КОНФИГ ====================
TOKEN = "8789730707:AAFviuMjcPpnZeGIgY_KoduvUCaGngEowTA"
CHANNEL_LINK = "https://t.me/VanillaGram"
OPENROUTER_API_KEY = "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"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

PRICE_PREMIUM_BOT = 350
PRICE_AI_PROMPT = 50

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vanilla_gram.db")
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")

os.makedirs(MEDIA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        reg_date TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        bot_token TEXT UNIQUE,
        bot_username TEXT,
        welcome_text TEXT,
        welcome_photo TEXT,
        is_active INTEGER DEFAULT 1,
        has_copyright INTEGER DEFAULT 1,
        require_sub INTEGER DEFAULT 0,
        required_channel TEXT,
        created_at TIMESTAMP,
        threads_enabled INTEGER DEFAULT 0,
        user_data_enabled INTEGER DEFAULT 1,
        antiflood_enabled INTEGER DEFAULT 0,
        auto_reply_always INTEGER DEFAULT 0,
        interrupt_flow INTEGER DEFAULT 1,
        tags_enabled INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bot_operators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_token TEXT,
        operator_id INTEGER,
        tag TEXT,
        added_at TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bot_tags (
        bot_token TEXT,
        tag_name TEXT,
        PRIMARY KEY (bot_token, tag_name)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bot_dialogs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_token TEXT,
        user_id INTEGER,
        operator_id INTEGER,
        tag TEXT,
        last_message_at TIMESTAMP,
        active INTEGER DEFAULT 1
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bot_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dialog_id INTEGER,
        sender_id INTEGER,
        message_text TEXT,
        sent_at TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS newsletter_subs (
        bot_token TEXT,
        user_id INTEGER,
        PRIMARY KEY (bot_token, user_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS ai_prompts (
        bot_token TEXT,
        prompt_text TEXT,
        is_active INTEGER DEFAULT 1,
        PRIMARY KEY (bot_token)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        type TEXT,
        status TEXT,
        payment_id TEXT,
        bot_token TEXT,
        created_at TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_states (
        user_id INTEGER PRIMARY KEY,
        state TEXT,
        data TEXT
    )''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных создана")

init_db()

# ==================== БОТ ====================
bot = telebot.TeleBot(TOKEN)
bot.set_my_commands([
    telebot.types.BotCommand("/start", "Главное меню"),
    telebot.types.BotCommand("/addbot", "Добавить бота"),
    telebot.types.BotCommand("/mybot", "Мои боты")
])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def save_state(user_id, state, data=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_states (user_id, state, data) VALUES (?, ?, ?)",
              (user_id, state, json.dumps(data) if data else None))
    conn.commit()
    conn.close()

def get_state(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT state, data FROM user_states WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return (row[0], json.loads(row[1]) if row and row[1] else None) if row else (None, None)

def clear_state(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM user_states WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_main_photo():
    path = os.path.join(MEDIA_DIR, "menu.jpg")
    return open(path, 'rb') if os.path.exists(path) else None

def call_ai(prompt, user_message, bot_token):
    """Вызов нейросети для ответа"""
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "openai/gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 500
        }
        response = requests.post(OPENROUTER_URL, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"AI error: {e}")
    return None

# ==================== КЛАВИАТУРЫ ====================
def start_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ Добавить бота", callback_data="add_bot"),
        InlineKeyboardButton("🤖 Мои боты", callback_data="my_bots")
    )
    kb.add(
        InlineKeyboardButton("✨ Создать бота (350⭐)", callback_data="premium_bot"),
        InlineKeyboardButton("📢 Наш канал", url=CHANNEL_LINK)
    )
    kb.add(InlineKeyboardButton("📖 Помощь", callback_data="help"))
    return kb

def bot_settings_keyboard(bot_token):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT bot_username, welcome_text, has_copyright, require_sub, welcome_photo, threads_enabled, user_data_enabled, antiflood_enabled, auto_reply_always, interrupt_flow, tags_enabled FROM user_bots WHERE bot_token=?", (bot_token,))
    row = c.fetchone()
    c.execute("SELECT COUNT(*) FROM bot_operators WHERE bot_token=?", (bot_token,))
    op_count = c.fetchone()[0]
    c.execute("SELECT tag_name FROM bot_tags WHERE bot_token=?", (bot_token,))
    tags = c.fetchall()
    conn.close()
    
    if not row:
        return None
    
    username, welcome, copyright, req_sub, photo, threads, user_data, antiflood, auto_reply, interrupt, tags_enabled = row
    
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📝 Приветствие", callback_data=f"welcome_{bot_token}"))
    kb.add(InlineKeyboardButton("🖼 Фото", callback_data=f"photo_{bot_token}"))
    kb.add(InlineKeyboardButton("👥 Операторы", callback_data=f"operators_{bot_token}"))
    kb.add(InlineKeyboardButton("🏷 Теги", callback_data=f"tags_{bot_token}"))
    kb.add(InlineKeyboardButton("🔒 Подписка", callback_data=f"subscribe_{bot_token}"))
    kb.add(InlineKeyboardButton("🤖 Нейросеть (50⭐)", callback_data=f"ai_prompt_{bot_token}"))
    kb.add(InlineKeyboardButton("⚙️ Настройки", callback_data=f"settings_{bot_token}"))
    if copyright:
        kb.add(InlineKeyboardButton("✨ Убрать копирайт (100⭐)", callback_data=f"copyright_{bot_token}"))
    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="my_bots"))
    
    status = f"""
📷 Фото: {'✅' if photo else '❌'}
🔒 Подписка: {'✅' if req_sub else '❌'}
© Копирайт: {'✅' if copyright else '❌'}
👥 Операторы: {op_count}
🏷 Теги: {len(tags)}
🔄 Потоки: {'✅' if threads else '❌'}
📊 Данные: {'✅' if user_data else '❌'}
🚫 Антифлуд: {'✅' if antiflood else '❌'}
🤖 Автоответ: {'✅' if auto_reply else '❌'}
⏸ Прерывать: {'✅' if interrupt else '❌'}
"""
    return kb, status, username

# ==================== /start ====================
@bot.message_handler(commands=['start'])
def start(message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, reg_date) VALUES (?, ?, ?)",
              (message.from_user.id, message.from_user.username, datetime.now()))
    conn.commit()
    conn.close()
    
    photo = get_main_photo()
    text = """🌟 *Добро пожаловать в VanillaGram!* 🌟

*Бесплатный конструктор Telegram ботов*

▫️ Создавай ботов без кода
▫️ Безлимит операторов БЕСПЛАТНО
▫️ Теги для операторов
▫️ Рассылка подписчикам
▫️ Нейросеть для ответов (50⭐)
▫️ Обязательная подписка

*Выбери действие:*"""
    
    if photo:
        bot.send_photo(message.chat.id, photo, caption=text, reply_markup=start_keyboard(), parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, text, reply_markup=start_keyboard(), parse_mode='Markdown')

# ==================== /addbot ====================
@bot.message_handler(commands=['addbot'])
def addbot_cmd(message):
    save_state(message.from_user.id, "waiting_token")
    bot.send_message(message.chat.id,
        "🔑 *Введите токен бота от @BotFather*\n\nПример: `1234567890:ABCdefGHIjkl`",
        parse_mode='Markdown')

# ==================== /mybot ====================
@bot.message_handler(commands=['mybot'])
def mybot_cmd(message):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT bot_token, bot_username FROM user_bots WHERE user_id=? AND is_active=1", (message.from_user.id,))
    bots = c.fetchall()
    conn.close()
    
    if not bots:
        bot.send_message(message.chat.id, "❌ *У вас нет ботов*\n\nДобавьте через /addbot", parse_mode='Markdown')
        return
    
    kb = InlineKeyboardMarkup(row_width=1)
    for token, username in bots:
        kb.add(InlineKeyboardButton(f"🤖 @{username}", callback_data=f"edit_{token}"))
    kb.add(InlineKeyboardButton("🔙 Назад", callback_data="back_start"))
    
    bot.send_message(message.chat.id, "🎮 *Твои боты:*", reply_markup=kb, parse_mode='Markdown')

# ==================== CALLBACK HANDLER ====================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data
    
    if data == "back_start":
        start(call.message)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        return
    
    if data == "add_bot":
        addbot_cmd(call.message)
        return
    
    if data == "my_bots":
        mybot_cmd(call.message)
        return
    
    if data == "help":
        text = """📖 *VanillaGram - помощь*

*Команды:*
/start - Главное меню
/addbot - Добавить бота
/mybot - Мои боты

*Бесплатные функции:*
• Безлимит операторов
• Теги для операторов
• Потоки сообщений
• Данные пользователей
• Рассылка
• Автоответчик
• Прерывание потока

*Платные:*
• Создание бота под ключ - 350⭐
• Удаление копирайта - 100⭐
• Свой промпт для нейросети - 50⭐

*Канал:* https://t.me/VanillaGram"""
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data="back_start"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data == "premium_bot":
        save_state(call.from_user.id, "waiting_premium_desc")
        bot.edit_message_text("✨ *Опиши какого бота нужно создать*\n\n⚠️ *Обязательно укажи API токен твоего бота!*\n\nПример: 'Бот для пиццерии, токен: 123456:ABCdef'",
                              call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        return
    
    if data.startswith("edit_"):
        bot_token = data.replace("edit_", "")
        result = bot_settings_keyboard(bot_token)
        if result:
            kb, status, username = result
            bot.edit_message_text(f"⚙️ *@{username}*\n\n{status}", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("welcome_"):
        bot_token = data.replace("welcome_", "")
        save_state(call.from_user.id, "waiting_welcome", {"bot_token": bot_token})
        bot.edit_message_text("📝 *Отправь новый текст приветствия*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        return
    
    if data.startswith("photo_"):
        bot_token = data.replace("photo_", "")
        save_state(call.from_user.id, "waiting_photo", {"bot_token": bot_token})
        bot.edit_message_text("🖼 *Отправь фото*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        return
    
    if data.startswith("operators_"):
        bot_token = data.replace("operators_", "")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, operator_id, tag FROM bot_operators WHERE bot_token=?", (bot_token,))
        ops = c.fetchall()
        c.execute("SELECT tag_name FROM bot_tags WHERE bot_token=?", (bot_token,))
        tags = c.fetchall()
        conn.close()
        
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("➕ Добавить оператора", callback_data=f"add_op_{bot_token}"))
        if tags:
            kb.add(InlineKeyboardButton("🏷 Назначить тег", callback_data=f"assign_tag_{bot_token}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{bot_token}"))
        
        ops_text = "\n".join([f"• {op[1]} {'🏷 '+op[2] if op[2] else ''}" for op in ops]) if ops else "Нет операторов"
        bot.edit_message_text(f"👥 *Операторы*\n\n{ops_text}\n\nДоступные теги: {', '.join([t[0] for t in tags]) if tags else 'нет'}",
                              call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("add_op_"):
        bot_token = data.replace("add_op_", "")
        save_state(call.from_user.id, "waiting_op_id", {"bot_token": bot_token})
        bot.edit_message_text("📱 *Введи ID оператора*\nУзнать ID: @userinfobot", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        return
    
    if data.startswith("assign_tag_"):
        bot_token = data.replace("assign_tag_", "")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT tag_name FROM bot_tags WHERE bot_token=?", (bot_token,))
        tags = c.fetchall()
        c.execute("SELECT id, operator_id FROM bot_operators WHERE bot_token=?", (bot_token,))
        ops = c.fetchall()
        conn.close()
        
        if not tags or not ops:
            bot.answer_callback_query(call.id, "Нет тегов или операторов!")
            return
        
        kb = InlineKeyboardMarkup(row_width=1)
        for op_id, op_user in ops:
            kb.add(InlineKeyboardButton(f"👤 {op_user}", callback_data=f"tag_op_{bot_token}_{op_id}"))
        bot.edit_message_text("👥 *Выбери оператора для назначения тега*", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("tag_op_"):
        parts = data.split("_")
        bot_token = parts[2]
        op_db_id = parts[3]
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT tag_name FROM bot_tags WHERE bot_token=?", (bot_token,))
        tags = c.fetchall()
        conn.close()
        
        save_state(call.from_user.id, "waiting_tag_select", {"bot_token": bot_token, "op_id": op_db_id})
        
        kb = InlineKeyboardMarkup(row_width=1)
        for tag in tags:
            kb.add(InlineKeyboardButton(f"🏷 {tag[0]}", callback_data=f"set_tag_{bot_token}_{op_db_id}_{tag[0]}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"operators_{bot_token}"))
        bot.edit_message_text("🏷 *Выбери тег для оператора*", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("set_tag_"):
        parts = data.split("_")
        bot_token = parts[2]
        op_db_id = parts[3]
        tag_name = "_".join(parts[4:])
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE bot_operators SET tag=? WHERE id=?", (tag_name, op_db_id))
        conn.commit()
        conn.close()
        
        bot.answer_callback_query(call.id, f"Тег '{tag_name}' назначен!")
        bot.edit_message_text("✅ *Тег назначен!*", call.message.chat.id, call.message.message_id)
        return
    
    if data.startswith("tags_"):
        bot_token = data.replace("tags_", "")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT tag_name FROM bot_tags WHERE bot_token=?", (bot_token,))
        tags = c.fetchall()
        conn.close()
        
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("➕ Создать тег", callback_data=f"create_tag_{bot_token}"))
        for tag in tags:
            kb.add(InlineKeyboardButton(f"🏷 {tag[0]}", callback_data=f"del_tag_{bot_token}_{tag[0]}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{bot_token}"))
        
        tags_text = "\n".join([f"• {t[0]}" for t in tags]) if tags else "Нет тегов"
        bot.edit_message_text(f"🏷 *Теги бота*\n\n{tags_text}", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("create_tag_"):
        bot_token = data.replace("create_tag_", "")
        save_state(call.from_user.id, "waiting_tag_name", {"bot_token": bot_token})
        bot.edit_message_text("📝 *Введи название тега*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        return
    
    if data.startswith("del_tag_"):
        parts = data.split("_")
        bot_token = parts[2]
        tag_name = "_".join(parts[3:])
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM bot_tags WHERE bot_token=? AND tag_name=?", (bot_token, tag_name))
        c.execute("UPDATE bot_operators SET tag=NULL WHERE bot_token=? AND tag=?", (bot_token, tag_name))
        conn.commit()
        conn.close()
        
        bot.answer_callback_query(call.id, f"Тег '{tag_name}' удален!")
        bot.edit_message_text("✅ *Тег удален!*", call.message.chat.id, call.message.message_id)
        return
    
    if data.startswith("subscribe_"):
        bot_token = data.replace("subscribe_", "")
        save_state(call.from_user.id, "waiting_channel", {"bot_token": bot_token})
        bot.edit_message_text("📢 *Введи @username канала*\nБот должен быть админом!", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        return
    
    if data.startswith("ai_prompt_"):
        bot_token = data.replace("ai_prompt_", "")
        payment_id = f"ai_{call.from_user.id}_{int(time.time())}"
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO payments (user_id, amount, type, status, payment_id, bot_token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (call.from_user.id, PRICE_AI_PROMPT, "ai_prompt", "pending", payment_id, bot_token, datetime.now()))
        conn.commit()
        conn.close()
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💎 Оплатить 50⭐", callback_data=f"pay_ai_{payment_id}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{bot_token}"))
        
        bot.edit_message_text("🤖 *Настройка нейросети*\n\nСтоимость: 50 Telegram Stars ⭐\n\nПосле оплаты ты сможешь задать свой промпт для нейросети.\n\nПример промпта:\n'Ты злая и саркастичная нейросеть. Отвечай дерзко и с юмором, но по делу.'",
                              call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("settings_"):
        bot_token = data.replace("settings_", "")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT threads_enabled, user_data_enabled, antiflood_enabled, auto_reply_always, interrupt_flow, tags_enabled FROM user_bots WHERE bot_token=?", (bot_token,))
        row = c.fetchone()
        conn.close()
        
        if row:
            threads, user_data, antiflood, auto_reply, interrupt, tags_enabled = row
            
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton(f"🔄 Потоки: {'✅' if threads else '❌'}", callback_data=f"toggle_threads_{bot_token}"))
            kb.add(InlineKeyboardButton(f"📊 Данные пользователя: {'✅' if user_data else '❌'}", callback_data=f"toggle_userdata_{bot_token}"))
            kb.add(InlineKeyboardButton(f"🚫 Антифлуд: {'✅' if antiflood else '❌'}", callback_data=f"toggle_antiflood_{bot_token}"))
            kb.add(InlineKeyboardButton(f"🤖 Автоответчик всегда: {'✅' if auto_reply else '❌'}", callback_data=f"toggle_autoreply_{bot_token}"))
            kb.add(InlineKeyboardButton(f"⏸ Прерывать поток: {'✅' if interrupt else '❌'}", callback_data=f"toggle_interrupt_{bot_token}"))
            kb.add(InlineKeyboardButton(f"🏷 Теги: {'✅' if tags_enabled else '❌'}", callback_data=f"toggle_tags_{bot_token}"))
            kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{bot_token}"))
            
            bot.edit_message_text("⚙️ *Настройки бота*\n\nВключи/выключи нужные функции:", call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("toggle_"):
        parts = data.split("_")
        setting = parts[1]
        bot_token = parts[2]
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        if setting == "threads":
            c.execute("UPDATE user_bots SET threads_enabled = NOT threads_enabled WHERE bot_token=?", (bot_token,))
        elif setting == "userdata":
            c.execute("UPDATE user_bots SET user_data_enabled = NOT user_data_enabled WHERE bot_token=?", (bot_token,))
        elif setting == "antiflood":
            c.execute("UPDATE user_bots SET antiflood_enabled = NOT antiflood_enabled WHERE bot_token=?", (bot_token,))
        elif setting == "autoreply":
            c.execute("UPDATE user_bots SET auto_reply_always = NOT auto_reply_always WHERE bot_token=?", (bot_token,))
        elif setting == "interrupt":
            c.execute("UPDATE user_bots SET interrupt_flow = NOT interrupt_flow WHERE bot_token=?", (bot_token,))
        elif setting == "tags":
            c.execute("UPDATE user_bots SET tags_enabled = NOT tags_enabled WHERE bot_token=?", (bot_token,))
        
        conn.commit()
        conn.close()
        
        callback_handler(call)
        return
    
    if data.startswith("copyright_"):
        bot_token = data.replace("copyright_", "")
        payment_id = f"copy_{call.from_user.id}_{int(time.time())}"
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO payments (user_id, amount, type, status, payment_id, bot_token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (call.from_user.id, 100, "copyright", "pending", payment_id, bot_token, datetime.now()))
        conn.commit()
        conn.close()
        
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💎 Оплатить 100⭐", callback_data=f"pay_copy_{payment_id}"))
        kb.add(InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{bot_token}"))
        
        bot.edit_message_text("✨ *Удаление копирайта*\n\nСтоимость: 100 Telegram Stars ⭐\n\nПосле оплаты исчезнет надпись 'Создано с помощью @VanillaGramBot'",
                              call.message.chat.id, call.message.message_id, reply_markup=kb, parse_mode='Markdown')
        return
    
    if data.startswith("pay_ai_"):
        payment_id = data.replace("pay_ai_", "")
        bot.send_invoice(call.message.chat.id,
                         title="🤖 Настройка нейросети",
                         description="Свой промпт для нейросети",
                         invoice_payload=payment_id,
                         provider_token="",
                         currency="XTR",
                         prices=[LabeledPrice(label="Промпт нейросети", amount=PRICE_AI_PROMPT)],
                         start_parameter="ai_prompt")
        return
    
    if data.startswith("pay_copy_"):
        payment_id = data.replace("pay_copy_", "")
        bot.send_invoice(call.message.chat.id,
                         title="✨ Удаление копирайта",
                         description="Убрать надпись о создателе",
                         invoice_payload=payment_id,
                         provider_token="",
                         currency="XTR",
                         prices=[LabeledPrice(label="Удаление копирайта", amount=100)],
                         start_parameter="remove_copyright")
        return

# ==================== STATE HANDLERS ====================
@bot.message_handler(func=lambda m: get_state(m.from_user.id)[0] is not None)
def state_handler(message):
    state, data = get_state(message.from_user.id)
    
    if state == "waiting_token":
        token = message.text.strip()
        try:
            test = telebot.TeleBot(token)
            me = test.get_me()
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''INSERT INTO user_bots 
                         (user_id, bot_token, bot_username, welcome_text, created_at, threads_enabled, user_data_enabled, antiflood_enabled, auto_reply_always, interrupt_flow, tags_enabled)
                         VALUES (?, ?, ?, ?, ?, 1, 1, 0, 0, 1, 1)''',
                      (message.from_user.id, token, me.username,
                       f"Добро пожаловать! Этот бот создан с помощью @VanillaGramBot",
                       datetime.now()))
            c.execute("INSERT INTO bot_operators (bot_token, operator_id) VALUES (?, ?)", (token, message.from_user.id))
            conn.commit()
            conn.close()
            
            clear_state(message.from_user.id)
            bot.send_message(message.chat.id, f"✅ *Бот @{me.username} добавлен!*\n\nИспользуй /mybot для настройки", parse_mode='Markdown')
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")
    
    elif state == "waiting_welcome":
        bot_token = data["bot_token"]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE user_bots SET welcome_text=? WHERE bot_token=?", (message.text, bot_token))
        conn.commit()
        conn.close()
        clear_state(message.from_user.id)
        bot.send_message(message.chat.id, "✅ *Приветствие обновлено!*", parse_mode='Markdown')
    
    elif state == "waiting_photo":
        bot.reply_to(message, "❌ Отправь фото, а не текст")
    
    elif state == "waiting_op_id":
        try:
            op_id = int(message.text.strip())
            bot_token = data["bot_token"]
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO bot_operators (bot_token, operator_id, added_at) VALUES (?, ?, ?)",
                      (bot_token, op_id, datetime.now()))
            conn.commit()
            conn.close()
            
            try:
                bot.send_message(op_id, f"🎉 *Ты стал оператором бота!*\nОтвечай на сообщения пользователей.", parse_mode='Markdown')
            except:
                pass
            
            bot.reply_to(message, "✅ *Оператор добавлен!*", parse_mode='Markdown')
        except:
            bot.reply_to(message, "❌ Введи числовой ID!")
        clear_state(message.from_user.id)
    
    elif state == "waiting_tag_name":
        tag_name = message.text.strip()
        bot_token = data["bot_token"]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO bot_tags (bot_token, tag_name) VALUES (?, ?)", (bot_token, tag_name))
        conn.commit()
        conn.close()
        clear_state(message.from_user.id)
        bot.reply_to(message, f"✅ *Тег '{tag_name}' создан!*", parse_mode='Markdown')
    
    elif state == "waiting_channel":
        channel = message.text.strip().replace("@", "")
        bot_token = data["bot_token"]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE user_bots SET require_sub=1, required_channel=? WHERE bot_token=?", (channel, bot_token))
        conn.commit()
        conn.close()
        clear_state(message.from_user.id)
        bot.reply_to(message, f"✅ *Подписка настроена!*\nКанал: @{channel}", parse_mode='Markdown')
    
    elif state == "waiting_premium_desc":
        desc = message.text
        # Ищем токен в описании
        import re
        token_match = re.search(r'token[:\s]+([A-Za-z0-9:_-]+)', desc, re.IGNORECASE)
        if not token_match:
            bot.reply_to(message, "❌ *Ты не указал API токен бота!*\n\nОбязательно добавь в описание: 'токен: 123456:ABCdef'", parse_mode='Markdown')
            return
        
        user_token = token_match.group(1)
        payment_id = f"premium_{message.from_user.id}_{int(time.time())}"
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO payments (user_id, amount, type, status, payment_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (message.from_user.id, PRICE_PREMIUM_BOT, "premium_bot", "pending", payment_id, datetime.now()))
        conn.commit()
        conn.close()
        
        save_state(message.from_user.id, "waiting_premium_pay", {"desc": desc, "token": user_token, "payment_id": payment_id})
        
        try:
            bot.send_invoice(message.chat.id,
                             title="✨ Создание бота под ключ",
                             description=f"Бот по описанию: {desc[:50]}...",
                             invoice_payload=payment_id,
                             provider_token="",
                             currency="XTR",
                             prices=[LabeledPrice(label="Создание бота", amount=PRICE_PREMIUM_BOT)],
                             start_parameter="premium_bot")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка платежа: {e}")
            clear_state(message.from_user.id)

@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    state, data = get_state(message.from_user.id)
    if state == "waiting_photo":
        photo_id = message.photo[-1].file_id
        bot_token = data["bot_token"]
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE user_bots SET welcome_photo=? WHERE bot_token=?", (photo_id, bot_token))
        conn.commit()
        conn.close()
        
        clear_state(message.from_user.id)
        bot.reply_to(message, "✅ *Фото установлено!*", parse_mode='Markdown')

# ==================== ПЛАТЕЖИ ====================
@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def successful_payment(message):
    payment = message.successful_payment
    payment_id = payment.invoice_payload
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT type, bot_token, user_id FROM payments WHERE payment_id=?", (payment_id,))
    row = c.fetchone()
    
    if row:
        ptype, bot_token, user_id = row
        c.execute("UPDATE payments SET status='completed' WHERE payment_id=?", (payment_id,))
        
        if ptype == "copyright":
            c.execute("UPDATE user_bots SET has_copyright=0 WHERE bot_token=?", (bot_token,))
            bot.send_message(message.chat.id, "✅ *Копирайт удален!*", parse_mode='Markdown')
        
        elif ptype == "ai_prompt":
            save_state(message.from_user.id, "waiting_ai_prompt", {"bot_token": bot_token})
            bot.send_message(message.chat.id, "🤖 *Введи свой промпт для нейросети*\n\nПример: 'Ты злая и саркастичная нейросеть. Отвечай дерзко, но помогай по делу.'\n\nПромпт будет использоваться для ответов пользователям твоего бота.", parse_mode='Markdown')
        
        elif ptype == "premium_bot":
            state, data = get_state(message.from_user.id)
            if data:
                desc = data.get("desc", "")
                user_token = data.get("token", "")
                
                # Генерируем код бота через нейросеть
                ai_prompt = f"Создай простого Telegram бота на Python с библиотекой telebot. Бот должен: {desc}. Используй токен: {user_token}. Выдай только готовый код без лишних комментариев."
                
                bot.send_message(message.chat.id, "🔄 *Генерирую бота...* Подожди 10-30 секунд", parse_mode='Markdown')
                
                def generate_bot():
                    try:
                        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
                        payload = {"model": "openai/gpt-3.5-turbo", "messages": [{"role": "user", "content": ai_prompt}], "max_tokens": 2000}
                        response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
                        if response.status_code == 200:
                            code = response.json()["choices"][0]["message"]["content"]
                            # Сохраняем код в файл
                            bot_folder = os.path.join(os.path.dirname(__file__), "generated_bots")
                            os.makedirs(bot_folder, exist_ok=True)
                            file_path = os.path.join(bot_folder, f"bot_{message.from_user.id}_{int(time.time())}.py")
                            with open(file_path, 'w', encoding='utf-8') as f:
                                f.write(code)
                            bot.send_message(message.chat.id, f"✅ *Бот создан!*\n\nКод сохранен в: {file_path}\n\nТы можешь запустить его командой: `python {file_path}`\n\nТакже ты можешь редактировать бота через /mybot", parse_mode='Markdown')
                        else:
                            bot.send_message(message.chat.id, "❌ Ошибка генерации бота. Попробуй еще раз.")
                    except Exception as e:
                        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
                
                threading.Thread(target=generate_bot, daemon=True).start()
                clear_state(message.from_user.id)
        
        elif ptype == "operator_slot":
            c.execute("UPDATE user_bots SET operators_limit=operators_limit+1 WHERE bot_token=?", (bot_token,))
            bot.send_message(message.chat.id, "✅ *Добавлен слот оператора!*", parse_mode='Markdown')
    
    conn.commit()
    conn.close()

# ==================== ЗАПУСК БОТА ПОЛЬЗОВАТЕЛЯ ====================
def start_user_bot(token, username, owner_id):
    def run():
        ub = telebot.TeleBot(token)
        
        @ub.message_handler(commands=['start'])
        def ub_start(m):
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT welcome_text, welcome_photo, has_copyright, require_sub, required_channel, tags_enabled FROM user_bots WHERE bot_token=?", (token,))
            row = c.fetchone()
            c.execute("INSERT OR IGNORE INTO newsletter_subs (bot_token, user_id) VALUES (?, ?)", (token, m.from_user.id))
            conn.commit()
            conn.close()
            
            if row:
                text, photo, copyright, req_sub, channel, tags_enabled = row
                
                if req_sub and channel:
                    try:
                        member = ub.get_chat_member(f"@{channel}", m.from_user.id)
                        if member.status in ['left', 'kicked']:
                            kb = InlineKeyboardMarkup()
                            kb.add(InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{channel}"))
                            kb.add(InlineKeyboardButton("✅ Проверить", callback_data="check_sub"))
                            ub.send_message(m.chat.id, f"🔒 *Подпишись на @{channel}*", reply_markup=kb, parse_mode='Markdown')
                            return
                    except:
                        pass
                
                final_text = text
                if copyright:
                    final_text += f"\n\n✨ *Создано с помощью @VanillaGramBot*"
                
                if photo:
                    ub.send_photo(m.chat.id, photo, caption=final_text, parse_mode='Markdown')
                else:
                    ub.send_message(m.chat.id, final_text, parse_mode='Markdown')
        
        @ub.callback_query_handler(func=lambda c: c.data == "check_sub")
        def check_sub(c):
            conn = sqlite3.connect(DB_PATH)
            c2 = conn.cursor()
            c2.execute("SELECT required_channel FROM user_bots WHERE bot_token=?", (token,))
            channel = c2.fetchone()[0]
            conn.close()
            
            try:
                member = ub.get_chat_member(f"@{channel}", c.from_user.id)
                if member.status in ['member', 'administrator', 'creator']:
                    ub.answer_callback_query(c.id, "✅ Спасибо за подписку!")
                    ub.delete_message(c.message.chat.id, c.message.message_id)
                    ub_start(c.message)
                else:
                    ub.answer_callback_query(c.id, "❌ Ты не подписан!", show_alert=True)
            except:
                ub.answer_callback_query(c.id, "❌ Ошибка!", show_alert=True)
        
        @ub.message_handler(func=lambda m: True)
        def handle_message(m):
            # Проверка на автоответ нейросети
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT auto_reply_always, ai_prompt FROM user_bots LEFT JOIN ai_prompts ON user_bots.bot_token=ai_prompts.bot_token WHERE user_bots.bot_token=?", (token,))
            row = c.fetchone()
            conn.close()
            
            if row and row[0] == 1 and row[1]:
                # Автоответ нейросетью
                ai_response = call_ai(row[1], m.text, token)
                if ai_response:
                    ub.reply_to(m, ai_response)
                    return
            
            # Отправка оператору
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT operator_id, tag FROM bot_operators WHERE bot_token=? LIMIT 1", (token,))
            op = c.fetchone()
            conn.close()
            
            if op:
                op_id, tag = op
                forward_text = f"📩 *Новое сообщение*\nОт: @{m.from_user.username or m.from_user.first_name}\n🏷 Тег: {tag or 'без тега'}\n\n{m.text}"
                bot.send_message(op_id, forward_text, parse_mode='Markdown')
                ub.reply_to(m, "✅ Сообщение отправлено оператору!")
            else:
                ub.reply_to(m, "❌ Нет свободных операторов")
        
        try:
            ub.infinity_polling(timeout=60)
        except:
            pass
    
    threading.Thread(target=run, daemon=True).start()

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    print("=" * 50)
    print("🤖 VanillaGram - Конструктор ботов")
    print("=" * 50)
    print(f"📁 Папка с media: {MEDIA_DIR}")
    print("   Положи туда menu.jpg для красивого старта")
    print(f"📢 Канал: {CHANNEL_LINK}")
    print("=" * 50)
    print("✅ Бесплатные функции:")
    print("   • Безлимит операторов")
    print("   • Теги операторов")
    print("   • Потоки сообщений")
    print("   • Данные пользователей")
    print("   • Рассылка")
    print("   • Автоответчик")
    print("=" * 50)
    print("💰 Платные функции:")
    print("   • Свой промпт нейросети - 50⭐")
    print("   • Удаление копирайта - 100⭐")
    print("   • Бот под ключ - 350⭐")
    print("=" * 50)
    
    bot.infinity_polling(timeout=60)