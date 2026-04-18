import asyncio
import logging
import aiosqlite
import re
import os
import time
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

load_dotenv()

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = [int(x) for x in os.environ["ADMIN_IDS"].split(",")]

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

album_buffer = {}
last_menu_sent = {}

class Form(StatesGroup):
    waiting_for_question = State()

class BattleReg(StatesGroup):
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_prefs = State()
    waiting_for_photos = State()
    waiting_for_sl_id = State()

class BattleEdit(StatesGroup):
    editing_date = State()
    editing_time = State()
    editing_prefs = State()
    editing_photos = State()
    editing_sl_id = State()

class BdayReg(StatesGroup):
    waiting_for_nick = State()
    waiting_for_id = State()
    waiting_for_date = State()
    waiting_for_photo = State()

class BdayEdit(StatesGroup):
    editing_nick = State()
    editing_id = State()
    editing_date = State()
    editing_photo = State()

class Broadcast(StatesGroup):
    waiting_for_post = State()

# --- БАЗА ДАННЫХ ---
DB_NAME = 'new_bot_database.db'

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS active_battles (user_id INTEGER PRIMARY KEY, date TEXT, time TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS active_bdays (user_id INTEGER PRIMARY KEY, nick TEXT, sl_id TEXT, date TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, text TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS payouts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER)''')
        await db.commit()

async def add_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?, ?, ?)', (user_id, username, full_name))
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT * FROM users') as cursor:
            return await cursor.fetchall()

async def add_battle(user_id: int, date: str, time: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO active_battles (user_id, date, time) VALUES (?, ?, ?)', (user_id, date, time))
        await db.commit()

async def get_battles():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''
            SELECT b.user_id, b.date, b.time, u.username, u.full_name
            FROM active_battles b JOIN users u ON b.user_id = u.user_id
        ''') as cursor:
            return await cursor.fetchall()

async def remove_battle(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM active_battles WHERE user_id = ?', (user_id,))
        await db.commit()

async def add_bday(user_id: int, nick: str, sl_id: str, date: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO active_bdays (user_id, nick, sl_id, date) VALUES (?, ?, ?, ?)', (user_id, nick, sl_id, date))
        await db.commit()

async def get_bdays():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''
            SELECT b.user_id, b.nick, b.sl_id, b.date, u.username, u.full_name
            FROM active_bdays b JOIN users u ON b.user_id = u.user_id
        ''') as cursor:
            return await cursor.fetchall()

async def remove_bday(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM active_bdays WHERE user_id = ?', (user_id,))
        await db.commit()

async def add_question(user_id: int, name: str, text: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('INSERT INTO questions (user_id, name, text) VALUES (?, ?, ?)', (user_id, name, text))
        await db.commit()
        return cursor.lastrowid

async def get_all_questions():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT id, user_id, name, text FROM questions') as cursor:
            return await cursor.fetchall()

async def delete_question(q_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM questions WHERE id = ?', (q_id,))
        await db.commit()

async def add_payout(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT INTO payouts (user_id, amount) VALUES (?, ?)', (user_id, 0))
        await db.commit()

async def get_payouts():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''
            SELECT p.id, p.user_id, p.amount, u.username, u.full_name
            FROM payouts p JOIN users u ON p.user_id = u.user_id
        ''') as cursor:
            return await cursor.fetchall()

async def delete_payout(p_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM payouts WHERE id = ?', (p_id,))
        await db.commit()

# --- ВАЛИДАЦИЯ ---
def validate_date(text: str) -> bool:
    return bool(re.match(r'^\d{1,2}\.\d{2}\s*-\s*\d{1,2}\.\d{2}$', text.strip()))

def validate_time(text: str) -> bool:
    return bool(re.match(r'^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$', text.strip()))

# --- КЛАВИАТУРЫ ---
def get_main_kb(user_id):
    if user_id in ADMIN_IDS:
        buttons = [
            [KeyboardButton(text="⚙️ Панель управления")],
            [KeyboardButton(text="📋 Список баттлов"), KeyboardButton(text="🎂 Список ДР")],
            [KeyboardButton(text="❓ База вопросов"), KeyboardButton(text="💰 Список выводов")],
            [KeyboardButton(text="📊 База данных"), KeyboardButton(text="📢 Рассылка")]
        ]
    else:
        buttons = [[KeyboardButton(text="🏠 Меню"), KeyboardButton(text="ℹ️ Информация")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_user_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Подать заявку", callback_data="open_apply")],
        [InlineKeyboardButton(text="💎 Запустить обработку", callback_data="open_payout")],
        [InlineKeyboardButton(text="❓ Задать вопрос", callback_data="open_question")],
        [InlineKeyboardButton(text="💰 Получить выплату", callback_data="request_payout")]
    ])

def get_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Закрыть", callback_data="cancel_action")]
    ])

def get_battle_summary_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="battle_confirm"),
         InlineKeyboardButton(text="✏️ Исправить", callback_data="battle_fix")]
    ])

def get_battle_fix_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Дата", callback_data="battle_edit_date")],
        [InlineKeyboardButton(text="⏰ Время", callback_data="battle_edit_time")],
        [InlineKeyboardButton(text="🌍 Нежелательные страны", callback_data="battle_edit_prefs")],
        [InlineKeyboardButton(text="📸 Фото", callback_data="battle_edit_photos")],
        [InlineKeyboardButton(text="🆔 SuperLive ID", callback_data="battle_edit_sl_id")],
        [InlineKeyboardButton(text="🔙 Назад к сводке", callback_data="battle_to_summary")]
    ])

def get_bday_summary_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="bday_confirm"),
         InlineKeyboardButton(text="✏️ Исправить", callback_data="bday_fix")]
    ])

def get_bday_fix_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎭 Ник", callback_data="bday_edit_nick")],
        [InlineKeyboardButton(text="🆔 SuperLive ID", callback_data="bday_edit_id")],
        [InlineKeyboardButton(text="📅 Дата", callback_data="bday_edit_date")],
        [InlineKeyboardButton(text="📸 Фото", callback_data="bday_edit_photo")],
        [InlineKeyboardButton(text="🔙 Назад к сводке", callback_data="bday_to_summary")]
    ])

def get_confirm_edit_kb(target: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить изменения", callback_data=f"{target}_to_summary")]
    ])

# --- ХЕЛПЕРЫ СВОДОК ---
async def send_battle_summary(target, state: FSMContext):
    data = await state.get_data()
    photos_count = len(data.get('photos', []))
    text = (
        "📋 <b>Проверь свою заявку на баттл:</b>\n\n"
        f"📅 Дата: <b>{data.get('date', '—')}</b>\n"
        f"⏰ Время: <b>{data.get('time', '—')}</b>\n"
        f"🌍 Нежел. страны: <b>{data.get('prefs', '—')}</b>\n"
        f"📸 Фото: <b>{photos_count} шт.</b>\n"
        f"🆔 SuperLive ID: <b>{data.get('sl_id', '—')}</b>\n\n"
        "Всё верно? 👇"
    )
    if isinstance(target, Message):
        await target.answer(text, reply_markup=get_battle_summary_kb(), parse_mode="HTML")
    else:
        await target.message.answer(text, reply_markup=get_battle_summary_kb(), parse_mode="HTML")
        await target.answer()

async def send_bday_summary(target, state: FSMContext):
    data = await state.get_data()
    photo_status = "✅ загружено" if data.get('photo_id') else "❌ не загружено"
    text = (
        "📋 <b>Проверь свою заявку на День Рождения:</b>\n\n"
        f"🎭 Ник: <b>{data.get('nick', '—')}</b>\n"
        f"🆔 SuperLive ID: <b>{data.get('sl_id', '—')}</b>\n"
        f"📅 Дата: <b>{data.get('date', '—')}</b>\n"
        f"📸 Фото: <b>{photo_status}</b>\n\n"
        "Всё верно? 👇"
    )
    if isinstance(target, Message):
        await target.answer(text, reply_markup=get_bday_summary_kb(), parse_mode="HTML")
    else:
        await target.message.answer(text, reply_markup=get_bday_summary_kb(), parse_mode="HTML")
        await target.answer()

# --- ХЕЛПЕР: отправить главное меню ---
async def send_main_menu(user_id: int, chat_id: int):
    now = time.time()
    if last_menu_sent.get(user_id, 0) > now - 2:
        return
    last_menu_sent[user_id] = now
    await bot.send_photo(
        chat_id=chat_id,
        photo="https://i.postimg.cc/5t7VGdMM/2147483648-231862.jpg",
        caption="<b>Добро пожаловать в SAGE!</b>\nЭтот бот — твой помощник, здесь есть всё что тебе нужно!\n\nВыбирай действие в меню ниже 👇",
        reply_markup=get_user_inline_kb() if user_id not in ADMIN_IDS else get_main_kb(user_id),
        parse_mode="HTML"
    )

# --- ОБЩИЕ КОЛЛБЕКИ ---
@dp.callback_query(F.data == "close_panel")
async def close_panel(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@dp.callback_query(F.data == "cancel_action")
async def cancel_action_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await send_main_menu(callback.from_user.id, callback.message.chat.id)
    await callback.answer("Отменено 🚫")

# --- МЕНЮ КОМАНД ---
async def setup_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help", description="Задать вопрос")
    ]
    await bot.set_my_commands(commands)

# --- СТАРТ ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    photo_url = "https://i.postimg.cc/5t7VGdMM/2147483648-231862.jpg"
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("👇", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🏠 Меню"), KeyboardButton(text="ℹ️ Информация")]], resize_keyboard=True))
        await message.answer_photo(photo=photo_url, caption="<b>Добро пожаловать в SAGE!</b>\nЭтот бот — твой помощник, здесь есть всё что тебе нужно!\n\nВыбирай действие в меню ниже 👇", reply_markup=get_user_inline_kb(), parse_mode="HTML")
    else:
        await message.answer_photo(photo=photo_url, caption="<b>Добро пожаловать в SAGE!</b>\nЭтот бот — твой помощник, здесь есть всё что тебе нужно!\n\nВыбирай действие в меню ниже 👇", reply_markup=get_main_kb(message.from_user.id), parse_mode="HTML")

@dp.message(F.text == "🏠 Меню")
async def menu_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer_photo(photo="https://i.postimg.cc/5t7VGdMM/2147483648-231862.jpg", caption="<b>Добро пожаловать в SAGE!</b>\nЭтот бот — твой помощник, здесь есть всё что тебе нужно!\n\nВыбирай действие в меню ниже 👇", reply_markup=get_user_inline_kb(), parse_mode="HTML")

@dp.message(F.text == "ℹ️ Информация")
async def handle_info(message: Message):
    await message.answer("<b>Бот агентства SAGE.</b>\nЗдесь ты можешь записаться на баттл, оформить ДР, поставить выплату и задать вопрос команде.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Закрыть", callback_data="close_panel")]]), parse_mode="HTML")

# --- ИНЛАЙН КНОПКИ ---
@dp.callback_query(F.data == "open_apply")
async def open_apply(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Заявка на батл", callback_data="apply_battle")],
        [InlineKeyboardButton(text="🎂 Заявка на День Рождения", callback_data="apply_bday")],
        [InlineKeyboardButton(text="🚫 Закрыть", callback_data="cancel_action")]
    ])
    await callback.message.answer("<b>Выбери, какую заявку хочешь подать:</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "open_payout")
async def open_payout(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Запустить обработку", callback_data="payout_request")],
        [InlineKeyboardButton(text="🚫 Закрыть", callback_data="cancel_action")]
    ])
    await callback.message.answer(
        "🚀 Нажми кнопку «Запустить обработку», и процесс автоматически начнётся в ближайшее время.\n\n"
        "💸 Сразу после завершения обработки деньги будут отправлены на реквизиты, которые ты указала менеджеру.\n\n"
        "⚠️ <b>Важно</b>\n"
        "Срок обработки: от 1 до 14 дней.\n"
        "К сожалению, он не зависит от нас, но мы делаем всё, чтобы ускорить процесс.\n\n"
        "🙏 Пожалуйста, дождись завершения — выплата гарантированно придёт в указанный срок.",
        reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "payout_request")
async def process_payout(callback: CallbackQuery):
    await add_payout(callback.from_user.id)
    report = (f"💰 <b>НОВАЯ ЗАЯВКА НА ВЫВОД!</b>\n\n"
              f"👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
              f"USER_ID:<code>{callback.from_user.id}</code>")
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report, parse_mode="HTML")
        except Exception:
            pass
    await callback.message.edit_text("✅ <b>Обработка запущена! Ожидай перевода.</b>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "open_question")
async def open_question(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("<b>Напиши свой вопрос:</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(Form.waiting_for_question)
    await callback.answer()

@dp.callback_query(F.data == "request_payout")
async def request_payout(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Получить выплату", callback_data="confirm_request_payout")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel")]
    ])
    await callback.message.answer(
        "⏳ Если выплата задерживается дольше обычного или ты заметила, что обработка уже завершилась — сообщи нам через эту кнопку.\n\n"
        "🙏 Мы проверим и решим вопрос как можно быстрее.",
        reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "confirm_request_payout")
async def confirm_request_payout(callback: CallbackQuery):
    report = (f"💰 <b>ЗАПРОС НА ПОЛУЧЕНИЕ ВЫПЛАТЫ!</b>\n\n"
              f"👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
              f"USER_ID:<code>{callback.from_user.id}</code>")
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report, parse_mode="HTML")
        except Exception:
            pass
    await callback.message.edit_text("✅ <b>Запрос отправлен! Ожидай выплаты.</b>", parse_mode="HTML")
    await callback.answer()

# --- АДМИН ДАШБОРД ---
@dp.message(F.text == "⚙️ Панель управления", F.from_user.id.in_(ADMIN_IDS))
async def admin_dashboard(message: Message):
    users = await get_all_users()
    battles = await get_battles()
    bdays = await get_bdays()
    questions = await get_all_questions()
    payouts = await get_payouts()
    text = (
        "🎛 <b>ГЛАВНАЯ ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n────────────────────\n"
        f"👥 Всего юзеров: <b>{len(users)}</b>\n"
        f"⚔️ Активных баттлов: <b>{len(battles)}</b> {'🔴' if battles else '🟢'}\n"
        f"🎂 Заявок на ДР: <b>{len(bdays)}</b> {'🔴' if bdays else '🟢'}\n"
        f"❓ Новых вопросов: <b>{len(questions)}</b> {'🔴' if questions else '🟢'}\n"
        f"💰 Ожидают выплаты: <b>{len(payouts)}</b> {'🔴' if payouts else '🟢'}\n"
        "────────────────────\n<i>Используй кнопки ниже для управления разделами.</i>"
    )
    await message.answer(text, parse_mode="HTML")

# ===== ЗАЯВКА НА БАТТЛ =====

@dp.callback_query(F.data == "apply_battle")
async def battle_start_cb(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Продолжить", callback_data="battle_continue")],
        [InlineKeyboardButton(text="🚫 Закрыть", callback_data="cancel_action")]
    ])
    await callback.message.answer(
        "🚀 <b>Международный Батл</b> — это соревнование между 2-мя стримерами из разных стран для дополнительного заработка и увеличения популярности!\n\n"
        "⏱ Длительность батла: ~15 минут\n"
        "🗓 Заявку необходимо подавать за 7-10 дней до желаемой даты\n\n"
        "• При подаче заявки тебе нужно указать дату и время которые запросит бот, делать это нужно строго в диапазонах по типу:\n\n"
        "📆 Дата: <code>1.01 - 5.01</code>\n"
        "⏰ Время: <code>20:00 - 23:00</code>\n\n"
        "Дата и время батла будут подобраны на основе указанных диапазонов.\n\n"
        "‼️ <b>Внимание:</b> Указав дату и/или время в точечном формате типа: <code>1.01 в 22:00</code> — заявка будет недействительна.",
        reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "battle_continue")
async def battle_step1(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📅 <b>Шаг 1:</b> Напиши диапазон дат\n\nФормат: <code>1.01 - 5.01</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleReg.waiting_for_date)
    await callback.answer()

@dp.message(BattleReg.waiting_for_date)
async def battle_date(message: Message, state: FSMContext):
    if not validate_date(message.text):
        await message.answer("❌ <b>Некорректный формат даты!</b>\n\nУкажи диапазон в формате: <code>1.01 - 5.01</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
        return
    await state.update_data(date=message.text.strip())
    await message.answer("⏰ <b>Шаг 2:</b> Напиши диапазон времени\n\nФормат: <code>20:00 - 23:00</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleReg.waiting_for_time)

@dp.message(BattleReg.waiting_for_time)
async def battle_time(message: Message, state: FSMContext):
    if not validate_time(message.text):
        await message.answer("❌ <b>Некорректный формат времени!</b>\n\nУкажи диапазон в формате: <code>20:00 - 23:00</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
        return
    await state.update_data(time=message.text.strip())
    await message.answer("🌍 <b>Шаг 3:</b> Укажи нежелательные страны\n\nНапример: <code>Индия</code>\n\nЕсли ограничений нет — напиши <code>нет</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleReg.waiting_for_prefs)

@dp.message(BattleReg.waiting_for_prefs)
async def battle_prefs(message: Message, state: FSMContext):
    await state.update_data(prefs=message.text)
    await message.answer("📸 <b>Шаг 4:</b> Отправь несколько своих лучших фото для баннера одним альбомом\n\nВыбери фото и отправь их все сразу одним сообщением.", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleReg.waiting_for_photos)

@dp.message(BattleReg.waiting_for_photos, F.photo)
async def battle_photos(message: Message, state: FSMContext):
    media_group_id = message.media_group_id
    if media_group_id:
        if media_group_id not in album_buffer:
            album_buffer[media_group_id] = []
            asyncio.create_task(process_battle_album(message, state, media_group_id, edit_mode=False))
        album_buffer[media_group_id].append(message.photo[-1].file_id)
    else:
        await state.update_data(photos=[message.photo[-1].file_id])
        await message.answer("🆔 <b>Шаг 5:</b> Напиши свой ID в SuperLive", reply_markup=get_cancel_kb(), parse_mode="HTML")
        await state.set_state(BattleReg.waiting_for_sl_id)

async def process_battle_album(message: Message, state: FSMContext, media_group_id: str, edit_mode: bool = False):
    await asyncio.sleep(1.5)
    photos = album_buffer.pop(media_group_id, [])
    await state.update_data(photos=photos)
    if edit_mode:
        await message.answer(f"📸 Фото обновлены! Загружено: <b>{len(photos)} шт.</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")
    else:
        await message.answer("🆔 <b>Шаг 5:</b> Напиши свой ID в SuperLive", reply_markup=get_cancel_kb(), parse_mode="HTML")
        await state.set_state(BattleReg.waiting_for_sl_id)

@dp.message(BattleReg.waiting_for_sl_id)
async def battle_sl_id(message: Message, state: FSMContext):
    await state.update_data(sl_id=message.text)
    await send_battle_summary(message, state)

# --- БАТТЛ: СВОДКА И ПОДТВЕРЖДЕНИЕ ---
@dp.callback_query(F.data == "battle_to_summary")
async def battle_to_summary(callback: CallbackQuery, state: FSMContext):
    await send_battle_summary(callback, state)

@dp.callback_query(F.data == "battle_fix")
async def battle_fix(callback: CallbackQuery):
    await callback.message.answer("✏️ <b>Что хочешь исправить?</b>", reply_markup=get_battle_fix_kb(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "battle_edit_date")
async def battle_edit_date(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📅 Введи новый диапазон дат\n\nФормат: <code>1.01 - 5.01</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_date)
    await callback.answer()

@dp.message(BattleEdit.editing_date)
async def battle_edit_date_input(message: Message, state: FSMContext):
    if not validate_date(message.text):
        await message.answer("❌ <b>Некорректный формат!</b>\n\nФормат: <code>1.01 - 5.01</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
        return
    await state.update_data(date=message.text.strip())
    await message.answer(f"✅ Дата обновлена: <b>{message.text.strip()}</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_edit_time")
async def battle_edit_time(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("⏰ Введи новый диапазон времени\n\nФормат: <code>20:00 - 23:00</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_time)
    await callback.answer()

@dp.message(BattleEdit.editing_time)
async def battle_edit_time_input(message: Message, state: FSMContext):
    if not validate_time(message.text):
        await message.answer("❌ <b>Некорректный формат!</b>\n\nФормат: <code>20:00 - 23:00</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
        return
    await state.update_data(time=message.text.strip())
    await message.answer(f"✅ Время обновлено: <b>{message.text.strip()}</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_edit_prefs")
async def battle_edit_prefs(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🌍 Введи новые нежелательные страны\n\nЕсли нет — напиши <code>нет</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_prefs)
    await callback.answer()

@dp.message(BattleEdit.editing_prefs)
async def battle_edit_prefs_input(message: Message, state: FSMContext):
    await state.update_data(prefs=message.text)
    await message.answer(f"✅ Страны обновлены: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_edit_photos")
async def battle_edit_photos(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Отправь новые фото одним альбомом", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_photos)
    await callback.answer()

@dp.message(BattleEdit.editing_photos, F.photo)
async def battle_edit_photos_input(message: Message, state: FSMContext):
    media_group_id = message.media_group_id
    if media_group_id:
        if media_group_id not in album_buffer:
            album_buffer[media_group_id] = []
            asyncio.create_task(process_battle_album(message, state, media_group_id, edit_mode=True))
        album_buffer[media_group_id].append(message.photo[-1].file_id)
    else:
        await state.update_data(photos=[message.photo[-1].file_id])
        await message.answer("✅ Фото обновлено: <b>1 шт.</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_edit_sl_id")
async def battle_edit_sl_id(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🆔 Введи новый SuperLive ID", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_sl_id)
    await callback.answer()

@dp.message(BattleEdit.editing_sl_id)
async def battle_edit_sl_id_input(message: Message, state: FSMContext):
    await state.update_data(sl_id=message.text)
    await message.answer(f"✅ SuperLive ID обновлён: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_confirm")
async def battle_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get('photos', [])
    report = (
        f"🔥 <b>НОВАЯ ЗАЯВКА НА БАТТЛ!</b>\n\n"
        f"<blockquote>👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
        f"📅 Дата: {data.get('date', '—')}\n"
        f"⏰ Время: {data.get('time', '—')}\n"
        f"🚫 Нежел. страны: {data.get('prefs', '—')}\n"
        f"🆔 SuperLive ID: {data.get('sl_id', '—')}</blockquote>\n\n"
        f"USER_ID:<code>{callback.from_user.id}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять заявку", callback_data=f"accept_battle_{callback.from_user.id}")]])
    for admin_id in ADMIN_IDS:
        try:
            if len(photos) == 1:
                await bot.send_photo(admin_id, photo=photos[0], caption=report, reply_markup=kb, parse_mode="HTML")
            elif len(photos) > 1:
                from aiogram.types import InputMediaPhoto
                media = [InputMediaPhoto(media=photos[0], caption=report, parse_mode="HTML")]
                for photo_id in photos[1:]:
                    media.append(InputMediaPhoto(media=photo_id))
                await bot.send_media_group(admin_id, media=media)
                await bot.send_message(admin_id, f"👆 Заявка от <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>", reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Ошибка отправки: {e}")
    await state.clear()
    await callback.message.edit_text("✅ <b>Заявка на баттл отправлена!</b> Ожидай ответа. 🎉", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("accept_battle_"), F.from_user.id.in_(ADMIN_IDS))
async def process_accept_battle(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    text = callback.message.caption or callback.message.text
    date_match = re.search(r'📅 Дата: (.*)', text)
    time_match = re.search(r'⏰ Время: (.*)', text)
    date_str = date_match.group(1).strip() if date_match else "Не указана"
    time_str = time_match.group(1).strip() if time_match else "Не указано"
    await add_battle(user_id, date_str, time_str)
    await bot.send_message(user_id, "✅ <b>Заявка на баттл принята!</b> Ожидай подробности.", parse_mode="HTML")
    new_text = (text or "").replace("🔥 <b>НОВАЯ ЗАЯВКА НА БАТТЛ!</b>", "✅ <b>БАТТЛ ПРИНЯТ И ДОБАВЛЕН В СПИСОК</b>")
    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=new_text, reply_markup=None, parse_mode="HTML")
        else:
            await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Одобрено ✅")

# ===== ЗАЯВКА НА ДЕНЬ РОЖДЕНИЯ =====

@dp.callback_query(F.data == "apply_bday")
async def bday_start_cb(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🎭 <b>Шаг 1:</b> Напиши свой ник в приложении Super Live.", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayReg.waiting_for_nick)
    await callback.answer()

@dp.message(BdayReg.waiting_for_nick)
async def bday_nick(message: Message, state: FSMContext):
    await state.update_data(nick=message.text)
    await message.answer("🆔 <b>Шаг 2:</b> Напиши свой ID в SuperLive.", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayReg.waiting_for_id)

@dp.message(BdayReg.waiting_for_id)
async def bday_id(message: Message, state: FSMContext):
    await state.update_data(sl_id=message.text)
    await message.answer("📅 <b>Шаг 3:</b> На какую дату планируем стрим в честь ДР?\n\nФормат: <code>1.01 - 5.01</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayReg.waiting_for_date)

@dp.message(BdayReg.waiting_for_date)
async def bday_date(message: Message, state: FSMContext):
    await state.update_data(date=message.text)
    await message.answer("📸 <b>Шаг 4:</b> Отправь <b>одно лучшее фото</b> для баннера.", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayReg.waiting_for_photo)

@dp.message(BdayReg.waiting_for_photo, F.photo)
async def bday_photo(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await send_bday_summary(message, state)

# --- ДР: СВОДКА И ПОДТВЕРЖДЕНИЕ ---
@dp.callback_query(F.data == "bday_to_summary")
async def bday_to_summary(callback: CallbackQuery, state: FSMContext):
    await send_bday_summary(callback, state)

@dp.callback_query(F.data == "bday_fix")
async def bday_fix(callback: CallbackQuery):
    await callback.message.answer("✏️ <b>Что хочешь исправить?</b>", reply_markup=get_bday_fix_kb(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "bday_edit_nick")
async def bday_edit_nick(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🎭 Введи новый ник в Super Live", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayEdit.editing_nick)
    await callback.answer()

@dp.message(BdayEdit.editing_nick)
async def bday_edit_nick_input(message: Message, state: FSMContext):
    await state.update_data(nick=message.text)
    await message.answer(f"✅ Ник обновлён: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.callback_query(F.data == "bday_edit_id")
async def bday_edit_id(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🆔 Введи новый SuperLive ID", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayEdit.editing_id)
    await callback.answer()

@dp.message(BdayEdit.editing_id)
async def bday_edit_id_input(message: Message, state: FSMContext):
    await state.update_data(sl_id=message.text)
    await message.answer(f"✅ ID обновлён: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.callback_query(F.data == "bday_edit_date")
async def bday_edit_date(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📅 Введи новую дату\n\nФормат: <code>1.01 - 5.01</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayEdit.editing_date)
    await callback.answer()

@dp.message(BdayEdit.editing_date)
async def bday_edit_date_input(message: Message, state: FSMContext):
    await state.update_data(date=message.text)
    await message.answer(f"✅ Дата обновлена: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.callback_query(F.data == "bday_edit_photo")
async def bday_edit_photo(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Отправь новое фото", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayEdit.editing_photo)
    await callback.answer()

@dp.message(BdayEdit.editing_photo, F.photo)
async def bday_edit_photo_input(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await message.answer("✅ Фото обновлено!", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.callback_query(F.data == "bday_confirm")
async def bday_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photo_id = data.get('photo_id')
    report = (
        f"🎂 <b>НОВАЯ ЗАЯВКА НА ДЕНЬ РОЖДЕНИЯ!</b>\n\n"
        f"<blockquote>👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
        f"🎭 Ник: {data.get('nick', '—')}\n"
        f"🆔 ID: <code>{data.get('sl_id', '—')}</code>\n"
        f"📅 Дата: {data.get('date', '—')}</blockquote>\n\n"
        f"USER_ID:<code>{callback.from_user.id}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять ДР", callback_data=f"accept_bday_{callback.from_user.id}")]])
    for admin_id in ADMIN_IDS:
        try:
            if photo_id:
                await bot.send_photo(admin_id, photo=photo_id, caption=report, reply_markup=kb, parse_mode="HTML")
            else:
                await bot.send_message(admin_id, report, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Ошибка отправки: {e}")
    await state.clear()
    await callback.message.edit_text("✅ <b>Заявка на День Рождения отправлена!</b> 🎉", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("accept_bday_"), F.from_user.id.in_(ADMIN_IDS))
async def process_accept_bday(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    text = callback.message.caption
    nick_match = re.search(r'🎭 Ник: (.*)', text)
    id_match = re.search(r'🆔 ID: <code>(.*)</code>', text)
    date_match = re.search(r'📅 Дата: (.*)', text)
    nick_str = nick_match.group(1).strip() if nick_match else "Неизвестно"
    id_str = id_match.group(1).strip() if id_match else "0"
    date_str = date_match.group(1).strip() if date_match else "Не указана"
    await add_bday(user_id, nick_str, id_str, date_str)
    await bot.send_message(user_id, "✅ <b>Твоя заявка на День Рождения одобрена!</b>", parse_mode="HTML")
    new_caption = text.replace("🎂 <b>НОВАЯ ЗАЯВКА НА ДЕНЬ РОЖДЕНИЯ!</b>", "✅ <b>ДР ПРИНЯТ И ДОБАВЛЕН В СПИСОК</b>")
    await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
    await callback.answer("Одобрено ✅")

# --- СПИСКИ АДМИНА ---
@dp.message(F.text == "💰 Список выводов", F.from_user.id.in_(ADMIN_IDS))
async def show_payouts(message: Message):
    payouts = await get_payouts()
    if not payouts: return await message.answer("Заявок на вывод сейчас нет.")
    text = "💰 <b>АКТИВНЫЕ ЗАЯВКИ НА ВЫВОД:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for idx, row in enumerate(payouts, 1):
        p_id, u_id, amount, username, full_name = row
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a>\n"
        builder.button(text=f"✅ Выплачено: {full_name}", callback_data=f"del_payout_{p_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("del_payout_"), F.from_user.id.in_(ADMIN_IDS))
async def process_del_payout(callback: CallbackQuery):
    p_id = int(callback.data.split("_")[2])
    await delete_payout(p_id)
    await callback.answer("Выплата отмечена как успешная ✅")
    payouts = await get_payouts()
    if not payouts: return await callback.message.edit_text("<b>Все выплаты сделаны! Список пуст.</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel")]]), parse_mode="HTML")
    text = "💰 <b>АКТИВНЫЕ ЗАЯВКИ НА ВЫВОД:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for idx, row in enumerate(payouts, 1):
        p_id, u_id, amount, username, full_name = row
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a>\n"
        builder.button(text=f"✅ Выплачено: {full_name}", callback_data=f"del_payout_{p_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.message(F.text == "📋 Список баттлов", F.from_user.id.in_(ADMIN_IDS))
async def show_battles(message: Message):
    battles = await get_battles()
    if not battles: return await message.answer("Сейчас нет активных записей на баттл.")
    text = "⚔️ <b>АКТИВНЫЕ ЗАПИСИ НА БАТТЛ:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for idx, row in enumerate(battles, 1):
        u_id, b_date, b_time, username, full_name = row
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a> — {b_date} в {b_time}\n"
        builder.button(text=f"❌ Завершить: {full_name}", callback_data=f"del_battle_{u_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("del_battle_"), F.from_user.id.in_(ADMIN_IDS))
async def process_del_battle(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    await remove_battle(user_id)
    await callback.answer("Удалено из списка ⚔️")
    battles = await get_battles()
    if not battles: return await callback.message.edit_text("<b>Все баттлы завершены! Список пуст.</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel")]]), parse_mode="HTML")
    text = "⚔️ <b>АКТИВНЫЕ ЗАПИСИ НА БАТТЛ:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for idx, row in enumerate(battles, 1):
        u_id, b_date, b_time, username, full_name = row
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a> — {b_date} в {b_time}\n"
        builder.button(text=f"❌ Завершить: {full_name}", callback_data=f"del_battle_{u_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.message(F.text == "🎂 Список ДР", F.from_user.id.in_(ADMIN_IDS))
async def show_bdays(message: Message):
    bdays = await get_bdays()
    if not bdays: return await message.answer("Сейчас нет активных записей на ДР.")
    text = "🎂 <b>АКТИВНЫЕ ЗАПИСИ НА ДЕНЬ РОЖДЕНИЯ:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for idx, row in enumerate(bdays, 1):
        u_id, nick, sl_id, b_date, username, full_name = row
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a> (Ник: {nick}) — Дата: {b_date}\n"
        builder.button(text=f"❌ Завершить ДР: {nick}", callback_data=f"del_bday_{u_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("del_bday_"), F.from_user.id.in_(ADMIN_IDS))
async def process_del_bday(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    await remove_bday(user_id)
    await callback.answer("Удалено из списка 🎂")
    bdays = await get_bdays()
    if not bdays: return await callback.message.edit_text("<b>Все ДР завершены! Список пуст.</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel")]]), parse_mode="HTML")
    text = "🎂 <b>АКТИВНЫЕ ЗАПИСИ НА ДЕНЬ РОЖДЕНИЯ:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for idx, row in enumerate(bdays, 1):
        u_id, nick, sl_id, b_date, username, full_name = row
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a> (Ник: {nick}) — Дата: {b_date}\n"
        builder.button(text=f"❌ Завершить ДР: {nick}", callback_data=f"del_bday_{u_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- ВОПРОСЫ ---
async def send_question_page(message_or_callback, page: int):
    qs = await get_all_questions()
    markup_close = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel")]])
    if not qs:
        text = "<b>База вопросов пуста. Все отвечено.</b>"
        if isinstance(message_or_callback, Message): await message_or_callback.answer(text, reply_markup=markup_close, parse_mode="HTML")
        else: await message_or_callback.message.edit_text(text, reply_markup=markup_close, parse_mode="HTML")
        return
    if page >= len(qs): page = 0
    if page < 0: page = len(qs) - 1
    q_id, u_id, name, q_text = qs[page]
    text = (f"📩 <b>ВОПРОС {page + 1} из {len(qs)}</b>\n\nUSER_ID:<code>{u_id}</code> | Q_ID:<code>{q_id}</code>\n"
            f"👤 От: <a href='tg://user?id={u_id}'>{name}</a>\n\n<blockquote>{q_text}</blockquote>\n\n<i>(Ответь на это сообщение через Reply)</i>")
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️", callback_data=f"q_page_{page - 1}")
    builder.button(text="❌ Удалить", callback_data=f"del_q_{q_id}_{page}")
    builder.button(text="➡️", callback_data=f"q_page_{page + 1}")
    builder.adjust(3)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    if isinstance(message_or_callback, Message): await message_or_callback.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else: await message_or_callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.message(F.text == "❓ База вопросов", F.from_user.id.in_(ADMIN_IDS))
async def show_questions_db(message: Message): await send_question_page(message, 0)

@dp.callback_query(F.data.startswith("q_page_"), F.from_user.id.in_(ADMIN_IDS))
async def process_q_page(callback: CallbackQuery):
    await send_question_page(callback, int(callback.data.split("_")[2]))
    await callback.answer()

@dp.callback_query(F.data.startswith("del_q_"), F.from_user.id.in_(ADMIN_IDS))
async def process_del_q(callback: CallbackQuery):
    parts = callback.data.split("_")
    await delete_question(int(parts[2]))
    await callback.answer("Удалено 🗑")
    await send_question_page(callback, int(parts[3]))

# --- БАЗА И РАССЫЛКА ---
@dp.message(F.text == "📊 База данных", F.from_user.id.in_(ADMIN_IDS))
async def show_database(message: Message):
    users = await get_all_users()
    if not users: return await message.answer("База пока пуста.")
    text = f"📊 <b>БАЗА ПОЛЬЗОВАТЕЛЕЙ ({len(users)} чел.):</b>\n\n"
    for idx, row in enumerate(users, 1):
        u_id, username, name = row
        text += f"{idx}. 🔹 <a href='tg://user?id={u_id}'>{name}</a> — ID: <code>{u_id}</code>\n"
    if len(text) > 4096: text = text[:4000] + "\n...список слишком длинный"
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📢 Рассылка", F.from_user.id.in_(ADMIN_IDS))
async def start_broadcast(message: Message, state: FSMContext):
    await message.answer("<b>Пришли пост для рассылки.</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(Broadcast.waiting_for_post)

@dp.message(Broadcast.waiting_for_post, F.from_user.id.in_(ADMIN_IDS))
async def perform_broadcast(message: Message, state: FSMContext):
    users = await get_all_users()
    count = 0
    for row in users:
        user_id = row[0]
        try:
            if message.photo: await bot.send_photo(user_id, photo=message.photo[-1].file_id, caption=message.caption, caption_entities=message.caption_entities)
            elif message.video: await bot.send_video(user_id, video=message.video.file_id, caption=message.caption, caption_entities=message.caption_entities)
            else: await bot.send_message(user_id, text=message.text, entities=message.entities)
            count += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await message.answer(f"✅ <b>Рассылка завершена! Получили: {count} чел.</b>", reply_markup=get_main_kb(message.from_user.id), parse_mode="HTML")
    await state.clear()

# --- ВОПРОСЫ И ОТВЕТЫ ---
@dp.message(Command("help"))
async def ask_question_start(message: Message, state: FSMContext):
    await message.answer("<b>Напиши свой вопрос:</b>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(Form.waiting_for_question)

@dp.message(Form.waiting_for_question)
async def process_question(message: Message, state: FSMContext):
    q_id = await add_question(message.from_user.id, message.from_user.full_name, message.text)
    report = (f"📩 <b>НОВЫЙ ВОПРОС!</b>\n\nUSER_ID:<code>{message.from_user.id}</code> | Q_ID:<code>{q_id}</code>\n"
              f"👤 От: <a href='tg://user?id={message.from_user.id}'>{message.from_user.full_name}</a>\n\n<blockquote>{message.text}</blockquote>")
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report, parse_mode="HTML")
        except Exception:
            pass
    await message.answer("✅ <b>Отправлено!</b>", parse_mode="HTML")
    await state.clear()

@dp.message(F.reply_to_message & F.from_user.id.in_(ADMIN_IDS))
async def admin_reply(message: Message):
    reply_text = message.reply_to_message.caption or message.reply_to_message.text
    if not reply_text or "USER_ID:" not in reply_text:
        return
    user_id_match = re.search(r'USER_ID:\s*<code>(\d+)</code>', reply_text) or re.search(r'USER_ID:(\d+)', reply_text)
    if user_id_match:
        user_id = int(user_id_match.group(1))
        await bot.send_message(user_id, f"<b>Ответ админа:</b>\n\n<blockquote>{message.text}</blockquote>", parse_mode="HTML")
        q_id_match = re.search(r'Q_ID:\s*<code>(\d+)</code>', reply_text) or re.search(r'Q_ID:(\d+)', reply_text)
        if q_id_match:
            await delete_question(int(q_id_match.group(1)))
            await message.answer("✅ <b>Ответ отправлен, вопрос удален из базы.</b>", parse_mode="HTML")
        else:
            await message.answer("✅ <b>Отправлено!</b>", parse_mode="HTML")

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await setup_bot_commands(bot)
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())