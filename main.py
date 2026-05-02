import asyncio
import logging
import aiosqlite
import re
import os
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import (Message, ReplyKeyboardMarkup, KeyboardButton,
                            InlineKeyboardMarkup, InlineKeyboardButton,
                            CallbackQuery, BotCommand, InputMediaPhoto)
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
Q_PAGE_SIZE = 5
P_PAGE_SIZE = 5

START_PHOTO = "https://i.postimg.cc/5t7VGdMM/2147483648-231862.jpg"
MENU_PHOTO  = "https://i.postimg.cc/cCTShgqb/image-3.jpg"

# ========== FSM ==========

class Form(StatesGroup):
    waiting_for_question = State()

class BattleReg(StatesGroup):
    waiting_for_date   = State()
    waiting_for_time   = State()
    waiting_for_prefs  = State()
    waiting_for_photos = State()
    waiting_for_sl_id  = State()
    reviewing          = State()

class BattleEdit(StatesGroup):
    editing_date   = State()
    editing_time   = State()
    editing_prefs  = State()
    editing_photos = State()
    editing_sl_id  = State()

class BdayReg(StatesGroup):
    waiting_for_nick  = State()
    waiting_for_id    = State()
    waiting_for_date  = State()
    waiting_for_photo = State()
    reviewing         = State()

class BdayEdit(StatesGroup):
    editing_nick  = State()
    editing_id    = State()
    editing_date  = State()
    editing_photo = State()

class Broadcast(StatesGroup):
    waiting_for_post = State()

class AdminAnswer(StatesGroup):
    waiting_for_answer = State()

# ========== БАЗА ДАННЫХ ==========

DB_NAME = 'new_bot_database.db'

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users
            (user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS active_battles
            (user_id INTEGER PRIMARY KEY, date TEXT, time TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS active_bdays
            (user_id INTEGER PRIMARY KEY, nick TEXT, sl_id TEXT, date TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS questions
            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, text TEXT,
             assigned_to INTEGER DEFAULT NULL, created_at TEXT DEFAULT NULL)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS payouts
            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER DEFAULT 0,
             status INTEGER DEFAULT 0, created_at TEXT DEFAULT NULL, confirmed_at TEXT DEFAULT NULL)''')
        await db.commit()
        for table, col, definition in [
            ("questions", "assigned_to",  "INTEGER DEFAULT NULL"),
            ("questions", "created_at",   "TEXT DEFAULT NULL"),
            ("payouts",   "status",       "INTEGER DEFAULT 0"),
            ("payouts",   "created_at",   "TEXT DEFAULT NULL"),
            ("payouts",   "confirmed_at", "TEXT DEFAULT NULL"),
        ]:
            try:
                await db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {definition}')
                await db.commit()
            except Exception:
                pass

async def add_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES (?,?,?)',
                         (user_id, username, full_name))
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT * FROM users') as cur:
            return await cur.fetchall()

async def add_battle(user_id: int, date: str, time: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO active_battles (user_id, date, time) VALUES (?,?,?)',
                         (user_id, date, time))
        await db.commit()

async def get_battles():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''SELECT b.user_id, b.date, b.time, u.username, u.full_name
            FROM active_battles b JOIN users u ON b.user_id=u.user_id''') as cur:
            return await cur.fetchall()

async def remove_battle(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM active_battles WHERE user_id=?', (user_id,))
        await db.commit()

async def add_bday(user_id: int, nick: str, sl_id: str, date: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT OR REPLACE INTO active_bdays (user_id, nick, sl_id, date) VALUES (?,?,?,?)',
                         (user_id, nick, sl_id, date))
        await db.commit()

async def get_bdays():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''SELECT b.user_id, b.nick, b.sl_id, b.date, u.username, u.full_name
            FROM active_bdays b JOIN users u ON b.user_id=u.user_id''') as cur:
            return await cur.fetchall()

async def remove_bday(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM active_bdays WHERE user_id=?', (user_id,))
        await db.commit()

async def add_question(user_id: int, name: str, text: str):
    ts = datetime.now().strftime('%H:%M, %d.%m.%Y')
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute('INSERT INTO questions (user_id,name,text,created_at) VALUES (?,?,?,?)',
                               (user_id, name, text, ts))
        await db.commit()
        return cur.lastrowid

async def get_all_questions():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT id, user_id, name, text FROM questions') as cur:
            return await cur.fetchall()

async def get_unassigned_questions():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            'SELECT id,user_id,name,text,created_at FROM questions WHERE assigned_to IS NULL ORDER BY id'
        ) as cur:
            return await cur.fetchall()

async def get_assigned_questions(admin_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            'SELECT id,user_id,name,text,created_at FROM questions WHERE assigned_to=? ORDER BY id',
            (admin_id,)
        ) as cur:
            return await cur.fetchall()

async def get_question_by_id(q_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            'SELECT id,user_id,name,text,assigned_to,created_at FROM questions WHERE id=?', (q_id,)
        ) as cur:
            return await cur.fetchone()

async def assign_question(q_id: int, admin_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE questions SET assigned_to=? WHERE id=?', (admin_id, q_id))
        await db.commit()

async def delete_question(q_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM questions WHERE id=?', (q_id,))
        await db.commit()

async def add_payout(user_id: int) -> int:
    # [3] возвращаем ID чтобы использовать в инлайн кнопке уведомления
    ts = datetime.now().strftime('%H:%M, %d.%m.%Y')
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute('INSERT INTO payouts (user_id, amount, status, created_at) VALUES (?,0,0,?)',
                               (user_id, ts))
        await db.commit()
        return cur.lastrowid

async def get_pending_payouts():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''SELECT p.id, p.user_id, p.created_at, u.username, u.full_name
            FROM payouts p JOIN users u ON p.user_id=u.user_id
            WHERE p.status=0 ORDER BY p.id''') as cur:
            return await cur.fetchall()

async def get_confirmed_payouts():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''SELECT p.id, p.user_id, p.confirmed_at, u.username, u.full_name
            FROM payouts p JOIN users u ON p.user_id=u.user_id
            WHERE p.status=1 ORDER BY p.id''') as cur:
            return await cur.fetchall()

async def get_payout_by_id(p_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('''SELECT p.id, p.user_id, p.status, p.created_at, p.confirmed_at, u.full_name
            FROM payouts p JOIN users u ON p.user_id=u.user_id WHERE p.id=?''', (p_id,)) as cur:
            return await cur.fetchone()

async def confirm_payout(p_id: int):
    ts = datetime.now().strftime('%H:%M, %d.%m.%Y')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('UPDATE payouts SET status=1, confirmed_at=? WHERE id=?', (ts, p_id))
        await db.commit()

async def delete_payout(p_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM payouts WHERE id=?', (p_id,))
        await db.commit()

# [1] Новые функции для раздела «История»
async def get_user_battles(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            'SELECT date, time FROM active_battles WHERE user_id=?', (user_id,)
        ) as cur:
            return await cur.fetchall()

async def get_user_bdays(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            'SELECT nick, sl_id, date FROM active_bdays WHERE user_id=?', (user_id,)
        ) as cur:
            return await cur.fetchall()

async def get_user_payouts(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            'SELECT id, status, created_at, confirmed_at FROM payouts WHERE user_id=? ORDER BY id DESC',
            (user_id,)
        ) as cur:
            return await cur.fetchall()

# ========== ВАЛИДАЦИЯ ==========

def validate_date(text: str) -> bool:
    return bool(re.match(r'^\d{1,2}\.\d{2}\s*-\s*\d{1,2}\.\d{2}$', text.strip()))

def validate_time(text: str) -> bool:
    return bool(re.match(r'^\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}$', text.strip()))

# ========== КЛАВИАТУРЫ ==========

def get_main_kb(user_id):
    if user_id in ADMIN_IDS:
        # [2] Первые 2 кнопки после панели управления — выводы/выплаты и база вопросов
        buttons = [
            [KeyboardButton(text="⚙️ Панель управления")],
            [KeyboardButton(text="💰 Выводы/выплаты"), KeyboardButton(text="❓ База вопросов")],
            [KeyboardButton(text="📋 Список баттлов"), KeyboardButton(text="🎂 Список ДР")],
            [KeyboardButton(text="📊 База данных"),    KeyboardButton(text="📢 Рассылка")]
        ]
    else:
        buttons = [
            [KeyboardButton(text="🏠 Меню")],
            [KeyboardButton(text="📜 История"), KeyboardButton(text="ℹ️ Информация")]
        ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_user_inline_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Подать заявку",       callback_data="open_apply")],
        [InlineKeyboardButton(text="💎 Запустить обработку", callback_data="open_payout")],
        [InlineKeyboardButton(text="❓ Задать вопрос",       callback_data="open_question")],
        [InlineKeyboardButton(text="💰 Получить выплату",    callback_data="request_payout")]
    ])

def get_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Закрыть", callback_data="cancel_action")]
    ])

def get_battle_summary_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="battle_confirm"),
         InlineKeyboardButton(text="✏️ Исправить",  callback_data="battle_fix")]
    ])

def get_battle_fix_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Дата",                 callback_data="battle_edit_date")],
        [InlineKeyboardButton(text="⏰ Время",                callback_data="battle_edit_time")],
        [InlineKeyboardButton(text="🌍 Нежелательные страны", callback_data="battle_edit_prefs")],
        [InlineKeyboardButton(text="📸 Фото",                 callback_data="battle_edit_photos")],
        [InlineKeyboardButton(text="🆔 SuperLive ID",         callback_data="battle_edit_sl_id")],
        [InlineKeyboardButton(text="🔙 Назад к сводке",       callback_data="battle_to_summary")]
    ])

def get_bday_summary_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="bday_confirm"),
         InlineKeyboardButton(text="✏️ Исправить",  callback_data="bday_fix")]
    ])

def get_bday_fix_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎭 Ник",            callback_data="bday_edit_nick")],
        [InlineKeyboardButton(text="🆔 SuperLive ID",   callback_data="bday_edit_id")],
        [InlineKeyboardButton(text="📅 Дата",           callback_data="bday_edit_date")],
        [InlineKeyboardButton(text="📸 Фото",           callback_data="bday_edit_photo")],
        [InlineKeyboardButton(text="🔙 Назад к сводке", callback_data="bday_to_summary")]
    ])

def get_confirm_edit_kb(target: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить изменения", callback_data=f"{target}_to_summary")]
    ])

# ========== ХЕЛПЕРЫ СВОДОК ==========

async def send_battle_summary(target, state: FSMContext):
    data = await state.get_data()
    photos_count = len(data.get('photos', []))
    text = (
        "📋 <b>Проверь свою заявку на баттл:</b>\n\n"
        f"📅 Дата: <b>{data.get('date','—')}</b>\n"
        f"⏰ Время: <b>{data.get('time','—')}</b>\n"
        f"🌍 Нежел. страны: <b>{data.get('prefs','—')}</b>\n"
        f"📸 Фото: <b>{photos_count} шт.</b>\n"
        f"🆔 SuperLive ID: <b>{data.get('sl_id','—')}</b>\n\n"
        "Всё верно? 👇"
    )
    await state.set_state(BattleReg.reviewing)
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
        f"🎭 Ник: <b>{data.get('nick','—')}</b>\n"
        f"🆔 SuperLive ID: <b>{data.get('sl_id','—')}</b>\n"
        f"📅 Дата: <b>{data.get('date','—')}</b>\n"
        f"📸 Фото: <b>{photo_status}</b>\n\n"
        "Всё верно? 👇"
    )
    await state.set_state(BdayReg.reviewing)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=get_bday_summary_kb(), parse_mode="HTML")
    else:
        await target.message.answer(text, reply_markup=get_bday_summary_kb(), parse_mode="HTML")
        await target.answer()

# ========== ХЕЛПЕР: ГЛАВНОЕ МЕНЮ (после cancel) ==========

async def send_main_menu(user_id: int, chat_id: int):
    caption = (
        "🚀 <b>Добро пожаловать в SAGE!</b>\n\n"
        "Это твой персональный помощник, который всегда рядом и готов помочь в любой момент 🤝\n\n"
        "⏰Обращайся сюда 24/7 — мы быстро подскажем, поможем и решим любой непонятный вопрос.\n\n"
        "Здесь есть всё, что тебе нужно для удобной работы и комфорта⚡️\n\n"
        "Выбирай действие в меню ниже👇"
    )
    await bot.send_photo(
        chat_id=chat_id,
        photo=START_PHOTO,
        caption=caption,
        reply_markup=get_user_inline_kb() if user_id not in ADMIN_IDS else get_main_kb(user_id),
        parse_mode="HTML"
    )

# ========== ХЕЛПЕРЫ ПАГИНАЦИИ ВЫВОДОВ ==========

async def render_payouts_menu(target):
    pending   = await get_pending_payouts()
    confirmed = await get_confirmed_payouts()
    text = (
        "💰 <b>ВЫВОДЫ / ВЫПЛАТЫ</b>\n"
        "────────────────\n\n"
        f"⏳ Ожидают подтверждения: <b>{len(pending)}</b>\n"
        f"✅ Ожидают выплаты: <b>{len(confirmed)}</b>\n\n"
        "<i>Выбери раздел:</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Подтвердить вывод  •  {len(pending)}", callback_data="pc_list_0")],
        [InlineKeyboardButton(text=f"💸 Выплатить вывод  •  {len(confirmed)}",  callback_data="pp_list_0")],
        [InlineKeyboardButton(text="🔙 Закрыть", callback_data="close_panel")]
    ])
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        try:
            await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()

async def render_pending_payouts(callback: CallbackQuery, page: int):
    payouts = await get_pending_payouts()
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в меню", callback_data="pout_menu")]])
    if not payouts:
        try:
            await callback.message.edit_text("📋 <b>Подтвердить вывод</b>\n\n✅ Нет новых заявок.", reply_markup=back_kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer("📋 <b>Подтвердить вывод</b>\n\n✅ Нет новых заявок.", reply_markup=back_kb, parse_mode="HTML")
        return

    total_pages = (len(payouts) + P_PAGE_SIZE - 1) // P_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * P_PAGE_SIZE
    page_ps = payouts[start:start + P_PAGE_SIZE]

    text = (
        f"📋 <b>Активные заявки на вывод</b>  •  {len(payouts)} шт.  •  стр. {page+1}/{total_pages}\n"
        "────────────────\n\n"
    )
    for li, (p_id, u_id, created_at, username, full_name) in enumerate(page_ps, 1):
        text += f"<b>{start+li}.</b>  <a href='tg://user?id={u_id}'><b>{full_name}</b></a>\n🕐 {created_at or '—'}\n\n"
    text += "<i>Нажми номер чтобы открыть:</i>"

    builder = InlineKeyboardBuilder()
    builder.row(*[InlineKeyboardButton(text=str(start+li), callback_data=f"pc_det_{p_id}_{page}")
                  for li, (p_id, u_id, created_at, username, full_name) in enumerate(page_ps, 1)])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"pc_list_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"· {page+1}/{total_pages} ·", callback_data="pout_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"pc_list_{page+1}"))
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🔙 Назад в меню", callback_data="pout_menu"))
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

async def render_confirmed_payouts(callback: CallbackQuery, page: int):
    payouts = await get_confirmed_payouts()
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в меню", callback_data="pout_menu")]])
    if not payouts:
        try:
            await callback.message.edit_text("💸 <b>Выплатить вывод</b>\n\n✅ Нет подтверждённых выводов.", reply_markup=back_kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer("💸 <b>Выплатить вывод</b>\n\n✅ Нет подтверждённых выводов.", reply_markup=back_kb, parse_mode="HTML")
        return

    total_pages = (len(payouts) + P_PAGE_SIZE - 1) // P_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * P_PAGE_SIZE
    page_ps = payouts[start:start + P_PAGE_SIZE]

    text = (
        f"💸 <b>Выводы к выплате</b>  •  {len(payouts)} шт.  •  стр. {page+1}/{total_pages}\n"
        "────────────────\n\n"
    )
    for li, (p_id, u_id, confirmed_at, username, full_name) in enumerate(page_ps, 1):
        text += f"<b>{start+li}.</b>  <a href='tg://user?id={u_id}'><b>{full_name}</b></a>\n✅ Подтверждён: {confirmed_at or '—'}\n\n"
    text += "<i>Нажми номер чтобы открыть:</i>"

    builder = InlineKeyboardBuilder()
    builder.row(*[InlineKeyboardButton(text=str(start+li), callback_data=f"pp_det_{p_id}_{page}")
                  for li, (p_id, u_id, confirmed_at, username, full_name) in enumerate(page_ps, 1)])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"pp_list_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"· {page+1}/{total_pages} ·", callback_data="pout_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"pp_list_{page+1}"))
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🔙 Назад в меню", callback_data="pout_menu"))
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# ========== ХЕЛПЕРЫ БАЗЫ ВОПРОСОВ ==========

async def render_q_menu(target):
    unassigned = await get_unassigned_questions()
    count = len(unassigned)
    text = (
        f"❓ <b>БАЗА ВОПРОСОВ</b> {'🔴' if count else '🟢'}\n"
        "────────────────\n\n"
        f"📬 Ожидают ответа: <b>{count}</b>\n\n"
        "<i>Выбери раздел:</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Все вопросы  •  {count} шт.", callback_data="qa_all_0")],
        [InlineKeyboardButton(text="📌 Мои вопросы", callback_data="qa_mine_0")],
        [InlineKeyboardButton(text="🔙 Закрыть", callback_data="close_panel")]
    ])
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        try:
            await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await target.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()

async def render_all_questions(callback: CallbackQuery, page: int):
    questions = await get_unassigned_questions()
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в меню", callback_data="qa_menu")]])
    if not questions:
        try:
            await callback.message.edit_text("📋 <b>Все вопросы</b>\n\n✅ Вопросов нет — всё отвечено!", reply_markup=back_kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer("📋 <b>Все вопросы</b>\n\n✅ Вопросов нет — всё отвечено!", reply_markup=back_kb, parse_mode="HTML")
        return

    total_pages = (len(questions) + Q_PAGE_SIZE - 1) // Q_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * Q_PAGE_SIZE
    page_qs = questions[start:start + Q_PAGE_SIZE]

    text = (
        f"📋 <b>Все вопросы</b>  •  {len(questions)} шт.  •  стр. {page+1}/{total_pages}\n"
        "────────────────\n\n"
    )
    for li, (q_id, u_id, name, q_text, created_at) in enumerate(page_qs, 1):
        preview = (q_text[:80] + "…") if len(q_text) > 80 else q_text
        text += f"<b>{start+li}.</b>  <a href='tg://user?id={u_id}'><b>{name}</b></a>\n🕐 {created_at or '—'}\n<blockquote>{preview}</blockquote>\n\n"
    text += "<i>Нажми номер вопроса чтобы открыть:</i>"

    builder = InlineKeyboardBuilder()
    builder.row(*[InlineKeyboardButton(text=str(start+li), callback_data=f"qa_detail_{q_id}_a_{page}")
                  for li, (q_id, u_id, name, q_text, created_at) in enumerate(page_qs, 1)])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"qa_all_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"· {page+1}/{total_pages} ·", callback_data="qa_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"qa_all_{page+1}"))
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🔙 Назад в меню", callback_data="qa_menu"))
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

async def render_my_questions(callback: CallbackQuery, page: int):
    questions = await get_assigned_questions(callback.from_user.id)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в меню", callback_data="qa_menu")]])
    if not questions:
        try:
            await callback.message.edit_text(
                "📌 <b>Мои вопросы</b>\n\nУ тебя нет взятых вопросов.\n\n<i>Перейди в «Все вопросы» и выбери вопрос.</i>",
                reply_markup=back_kb, parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer("📌 <b>Мои вопросы</b>\n\nУ тебя нет взятых вопросов.", reply_markup=back_kb, parse_mode="HTML")
        return

    total_pages = (len(questions) + Q_PAGE_SIZE - 1) // Q_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * Q_PAGE_SIZE
    page_qs = questions[start:start + Q_PAGE_SIZE]

    text = (
        f"📌 <b>Мои вопросы</b>  •  {len(questions)} шт.  •  стр. {page+1}/{total_pages}\n"
        "────────────────\n\n"
    )
    for li, (q_id, u_id, name, q_text, created_at) in enumerate(page_qs, 1):
        preview = (q_text[:80] + "…") if len(q_text) > 80 else q_text
        text += f"<b>{start+li}.</b>  <a href='tg://user?id={u_id}'><b>{name}</b></a>\n🕐 {created_at or '—'}\n<blockquote>{preview}</blockquote>\n\n"
    text += "<i>Нажми номер вопроса чтобы открыть:</i>"

    builder = InlineKeyboardBuilder()
    builder.row(*[InlineKeyboardButton(text=str(start+li), callback_data=f"qa_detail_{q_id}_m_{page}")
                  for li, (q_id, u_id, name, q_text, created_at) in enumerate(page_qs, 1)])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"qa_mine_{page-1}"))
        nav.append(InlineKeyboardButton(text=f"· {page+1}/{total_pages} ·", callback_data="qa_noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"qa_mine_{page+1}"))
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🔙 Назад в меню", callback_data="qa_menu"))
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

async def render_question_detail(callback: CallbackQuery, q_id: int, src: str, page: int):
    question = await get_question_by_id(q_id)
    if not question:
        await callback.answer("❌ Вопрос уже удалён.", show_alert=True)
        if src == 'a':
            await render_all_questions(callback, page)
        else:
            await render_my_questions(callback, page)
        return

    q_id_val, u_id, name, q_text, assigned_to, created_at = question
    is_mine = (assigned_to == callback.from_user.id)
    text = (
        "📩 <b>ВОПРОС</b>\n"
        "────────────────\n\n"
        f"👤 <a href='tg://user?id={u_id}'><b>{name}</b></a>\n"
        f"🕐 {created_at or '—'}\n\n"
        f"<blockquote>{q_text}</blockquote>"
    )
    builder = InlineKeyboardBuilder()
    if src == 'a' and not is_mine:
        builder.row(InlineKeyboardButton(text="✍️ Взять в работу", callback_data=f"qa_take_{q_id}_a_{page}"))
    elif src == 'm' or is_mine:
        builder.row(InlineKeyboardButton(text="💬 Ответить", callback_data=f"qa_ans_{q_id}_{src}_{page}"))
    builder.row(InlineKeyboardButton(text="🗑 Удалить вопрос", callback_data=f"qa_dc_{q_id}_{src}_{page}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад к списку", callback_data=f"qa_all_{page}" if src == 'a' else f"qa_mine_{page}"))
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# ========== ОБЩИЕ КОЛЛБЕКИ ==========

@dp.callback_query(F.data == "close_panel")
async def close_panel(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
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

# ========== МЕНЮ КОМАНД ==========

async def setup_bot_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="help",  description="Задать вопрос")
    ])

# ========== СТАРТ ==========

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await add_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    caption = (
        "🚀 <b>Добро пожаловать в SAGE!</b>\n\n"
        "Это твой персональный помощник, который всегда рядом и готов помочь в любой момент 🤝\n\n"
        "⏰Обращайся сюда 24/7 — мы быстро подскажем, поможем и решим любой непонятный вопрос.\n\n"
        "Здесь есть всё, что тебе нужно для удобной работы и комфорта⚡️\n\n"
        "Выбирай действие в меню ниже👇"
    )
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("👇", reply_markup=get_main_kb(message.from_user.id))
        await message.answer_photo(photo=START_PHOTO, caption=caption, reply_markup=get_user_inline_kb(), parse_mode="HTML")
    else:
        await message.answer_photo(photo=START_PHOTO, caption=caption, reply_markup=get_main_kb(message.from_user.id), parse_mode="HTML")

@dp.message(F.text == "🏠 Меню")
async def menu_handler(message: Message, state: FSMContext):
    await state.clear()
    first_name = message.from_user.first_name or message.from_user.username or "красотка"
    await message.answer_photo(
        photo=MENU_PHOTO,
        caption=f"🌸 Привет, {first_name}!\n\nЧем могу помочь?🧐",
        reply_markup=get_user_inline_kb(),
        parse_mode="HTML"
    )

# [1] Раздел «История» — полноценная реализация
@dp.message(F.text == "📜 История")
async def history_handler(message: Message):
    user_id = message.from_user.id
    battles  = await get_user_battles(user_id)
    bdays    = await get_user_bdays(user_id)
    payouts  = await get_user_payouts(user_id)

    has_anything = battles or bdays or payouts

    if not has_anything:
        await message.answer(
            "📜 <b>История</b>\n"
            "────────────────\n\n"
            "У тебя пока нет активных заявок или выплат.\n\n"
            "<i>Подай заявку через главное меню 👇</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Закрыть", callback_data="close_panel")]]),
            parse_mode="HTML"
        )
        return

    text = "📜 <b>ИСТОРИЯ ЗАЯВОК</b>\n────────────────\n\n"

    if battles:
        text += "⚔️ <b>Баттлы:</b>\n"
        for date, time in battles:
            text += f"  • 📅 {date}  ⏰ {time}\n  🟡 Статус: активный\n\n"

    if bdays:
        text += "🎂 <b>Дни рождения:</b>\n"
        for nick, sl_id, date in bdays:
            text += f"  • 🎭 {nick}  📅 {date}\n  🟡 Статус: активный\n\n"

    if payouts:
        text += "💰 <b>Выводы:</b>\n"
        for p_id, status, created_at, confirmed_at in payouts:
            if status == 0:
                status_text = "⏳ Ожидает подтверждения"
            elif status == 1:
                status_text = "✅ Подтверждён, ожидает выплаты"
            else:
                status_text = "💸 Выплачено"
            text += f"  • 🕐 {created_at or '—'}\n  {status_text}\n\n"

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Закрыть", callback_data="close_panel")]]),
        parse_mode="HTML"
    )

@dp.message(F.text == "ℹ️ Информация")
async def handle_info(message: Message):
    await message.answer(
        "🔧 <b>Раздел в разработке</b>\n\nСкоро здесь появится полезная информация об агентстве SAGE.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Закрыть", callback_data="close_panel")]]),
        parse_mode="HTML"
    )

# ========== ИНЛАЙН КНОПКИ ПОЛЬЗОВАТЕЛЯ ==========

@dp.callback_query(F.data == "open_apply")
async def open_apply(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Заявка на батл",          callback_data="apply_battle")],
        [InlineKeyboardButton(text="🎂 Заявка на День Рождения", callback_data="apply_bday")],
        [InlineKeyboardButton(text="🚫 Закрыть",                 callback_data="cancel_action")]
    ])
    await callback.message.answer("<b>Выбери, какую заявку хочешь подать:</b>", reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "open_payout")
async def open_payout(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Запустить обработку", callback_data="payout_request")],
        [InlineKeyboardButton(text="🚫 Закрыть",             callback_data="cancel_action")]
    ])
    await callback.message.answer(
        "🚀 Нажми кнопку «Запустить обработку», и процесс автоматически начнётся в ближайшее время.\n\n"
        "💸 Сразу после завершения обработки деньги будут отправлены на реквизиты, которые ты указала менеджеру.\n\n"
        "⚠️ <b>Важно</b>\nСрок обработки: от 1 до 14 дней.\nК сожалению, он не зависит от нас, но мы делаем всё, чтобы ускорить процесс.\n\n"
        "🙏 Пожалуйста, дождись завершения — выплата гарантированно придёт в указанный срок.",
        reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "payout_request")
async def process_payout(callback: CallbackQuery):
    # [3] add_payout теперь возвращает ID, используем для кнопки «Подтвердить заявку»
    p_id = await add_payout(callback.from_user.id)
    report = (
        f"💰 <b>НОВАЯ ЗАЯВКА НА ВЫВОД!</b>\n\n"
        f"👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
        f"USER_ID:<code>{callback.from_user.id}</code>"
    )
    # [3] Кнопка быстрого подтверждения прямо из уведомления
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить заявку", callback_data=f"quick_confirm_{p_id}")]
    ])
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report, reply_markup=admin_kb, parse_mode="HTML")
        except Exception:
            pass
    await callback.message.edit_text("✅ <b>Обработка запущена! Ожидай перевода.</b>", parse_mode="HTML")
    await callback.answer()

# [3] Быстрое подтверждение вывода прямо из уведомления
@dp.callback_query(F.data.startswith("quick_confirm_"), F.from_user.id.in_(ADMIN_IDS))
async def quick_confirm_payout(callback: CallbackQuery):
    p_id = int(callback.data.split("_")[2])
    payout = await get_payout_by_id(p_id)
    if not payout:
        await callback.answer("❌ Заявка не найдена.", show_alert=True)
        return
    p_id_val, u_id, status, created_at, confirmed_at, full_name = payout
    if status == 1:
        await callback.answer("⚠️ Эта заявка уже подтверждена!", show_alert=True)
        return
    await confirm_payout(p_id)
    try:
        await bot.send_message(u_id, "✅ <b>Твой вывод подтверждён!</b>\n\nВыплата обрабатывается и скоро придёт на твои реквизиты.", parse_mode="HTML")
    except Exception:
        pass
    # Убираем кнопку из уведомления, чтобы не нажимали повторно
    try:
        new_text = (
            f"💰 <b>ЗАЯВКА НА ВЫВОД ПОДТВЕРЖДЕНА</b>\n\n"
            f"👤 <a href='tg://user?id={u_id}'>{full_name}</a>\n"
            f"✅ Подтверждено: {datetime.now().strftime('%H:%M, %d.%m.%Y')}\n\n"
            f"<i>Вывод добавлен в раздел «Выплатить вывод»</i>"
        )
        await callback.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(
        "✅ Вывод подтверждён и автоматически добавлен в раздел «Выплатить вывод»!",
        show_alert=True
    )

@dp.callback_query(F.data == "open_question")
async def open_question(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши свой вопрос или проблему 💬\n\nМы постараемся помочь тебе как можно быстрее ❤️",
        reply_markup=get_cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_question)
    await callback.answer()

@dp.callback_query(F.data == "request_payout")
async def request_payout(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Получить выплату", callback_data="confirm_request_payout")],
        [InlineKeyboardButton(text="🔙 Назад",            callback_data="close_panel")]
    ])
    await callback.message.answer(
        "⏳ Если выплата задерживается дольше обычного или ты заметила, что обработка уже завершилась — сообщи нам через эту кнопку.\n\n"
        "🙏 Мы проверим и решим вопрос как можно быстрее.",
        reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "confirm_request_payout")
async def confirm_request_payout(callback: CallbackQuery):
    report = (
        f"💰 <b>ЗАПРОС НА ПОЛУЧЕНИЕ ВЫПЛАТЫ!</b>\n\n"
        f"👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
        f"USER_ID:<code>{callback.from_user.id}</code>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report, parse_mode="HTML")
        except Exception:
            pass
    await callback.message.edit_text("✅ <b>Запрос отправлен! Ожидай выплаты.</b>", parse_mode="HTML")
    await callback.answer()

# ========== АДМИН ДАШБОРД ==========

@dp.message(F.text == "⚙️ Панель управления", F.from_user.id.in_(ADMIN_IDS))
async def admin_dashboard(message: Message):
    users     = await get_all_users()
    battles   = await get_battles()
    bdays     = await get_bdays()
    questions = await get_all_questions()
    pending   = await get_pending_payouts()
    confirmed = await get_confirmed_payouts()
    total_p   = len(pending) + len(confirmed)

    text = (
        "🎛<b>ПАНЕЛЬ АДМИНИСТРАТОРА</b>\n"
        "────────────────\n\n"
        f"{'🔴' if questions else '🟢'} — <b>{len(questions)}</b> — Новых вопросов\n"
        f"{'🔴' if battles   else '🟢'} — <b>{len(battles)}</b> — Активных баттлов\n"
        f"{'🔴' if bdays     else '🟢'} — <b>{len(bdays)}</b> — Заявок на ДР\n"
        f"{'🔴' if total_p   else '🟢'} — <b>{total_p}</b> — Ожидают выплаты\n\n"
        "────────────────\n"
        f"👥 Всего юзеров: <b>{len(users)}</b>"
    )
    await message.answer(text, parse_mode="HTML")

# ========== ЗАЯВКА НА БАТТЛ ==========

@dp.callback_query(F.data == "apply_battle")
async def battle_start_cb(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Продолжить", callback_data="battle_continue")],
        [InlineKeyboardButton(text="🚫 Закрыть",    callback_data="cancel_action")]
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
    await state.clear()
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
    await message.answer(
        "🌍 <b>Шаг 3:</b> Укажи нежелательные страны (исключаются при подборе соперницы)\n\n"
        "Например: <code>Индия</code>\n\n"
        "Если ограничений нет и ты готова к любой сопернице — напиши <code>нет</code>",
        reply_markup=get_cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(BattleReg.waiting_for_prefs)

@dp.message(BattleReg.waiting_for_prefs)
async def battle_prefs(message: Message, state: FSMContext):
    await state.update_data(prefs=message.text)
    await message.answer("📸 <b>Шаг 4:</b> Отправь несколько своих лучших фото для баннера одним альбомом\n\nВыбери фото и отправь их все сразу одним сообщением.", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleReg.waiting_for_photos)

@dp.message(BattleReg.waiting_for_photos, F.photo)
async def battle_photos(message: Message, state: FSMContext):
    mgid = message.media_group_id
    if mgid:
        if mgid not in album_buffer:
            album_buffer[mgid] = []
            asyncio.create_task(process_battle_album(message, state, mgid, edit_mode=False))
        album_buffer[mgid].append(message.photo[-1].file_id)
    else:
        await state.update_data(photos=[message.photo[-1].file_id])
        await message.answer("🆔 <b>Шаг 5:</b> Напиши свой ID в SuperLive", reply_markup=get_cancel_kb(), parse_mode="HTML")
        await state.set_state(BattleReg.waiting_for_sl_id)

@dp.message(BattleReg.waiting_for_photos, ~F.photo)
async def battle_photos_wrong(message: Message):
    await message.answer("📸 Пожалуйста, отправь <b>фото</b> (одно или альбомом).", reply_markup=get_cancel_kb(), parse_mode="HTML")

async def process_battle_album(message: Message, state: FSMContext, mgid: str, edit_mode: bool = False):
    await asyncio.sleep(1.5)
    photos = album_buffer.pop(mgid, [])
    if await state.get_state() is None:
        return
    await state.update_data(photos=photos)
    if edit_mode:
        await state.set_state(BattleReg.reviewing)
        await message.answer(f"📸 Фото обновлены! Загружено: <b>{len(photos)} шт.</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")
    else:
        await message.answer("🆔 <b>Шаг 5:</b> Напиши свой ID в SuperLive", reply_markup=get_cancel_kb(), parse_mode="HTML")
        await state.set_state(BattleReg.waiting_for_sl_id)

@dp.message(BattleReg.waiting_for_sl_id)
async def battle_sl_id(message: Message, state: FSMContext):
    await state.update_data(sl_id=message.text)
    await send_battle_summary(message, state)

@dp.callback_query(F.data == "battle_to_summary")
async def battle_to_summary(callback: CallbackQuery, state: FSMContext):
    await send_battle_summary(callback, state)

@dp.callback_query(F.data == "battle_fix")
async def battle_fix(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BattleReg.reviewing)
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
    await state.set_state(BattleReg.reviewing)
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
    await state.set_state(BattleReg.reviewing)
    await message.answer(f"✅ Время обновлено: <b>{message.text.strip()}</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_edit_prefs")
async def battle_edit_prefs(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🌍 Введи новые нежелательные страны\n\nЕсли нет — напиши <code>нет</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_prefs)
    await callback.answer()

@dp.message(BattleEdit.editing_prefs)
async def battle_edit_prefs_input(message: Message, state: FSMContext):
    await state.update_data(prefs=message.text)
    await state.set_state(BattleReg.reviewing)
    await message.answer(f"✅ Страны обновлены: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_edit_photos")
async def battle_edit_photos(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Отправь новые фото одним альбомом", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_photos)
    await callback.answer()

@dp.message(BattleEdit.editing_photos, F.photo)
async def battle_edit_photos_input(message: Message, state: FSMContext):
    mgid = message.media_group_id
    if mgid:
        if mgid not in album_buffer:
            album_buffer[mgid] = []
            asyncio.create_task(process_battle_album_edit(message, state, mgid))
        album_buffer[mgid].append(message.photo[-1].file_id)
    else:
        await state.update_data(photos=[message.photo[-1].file_id])
        await state.set_state(BattleReg.reviewing)
        await message.answer("✅ Фото обновлено: <b>1 шт.</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.message(BattleEdit.editing_photos, ~F.photo)
async def battle_edit_photos_wrong(message: Message):
    await message.answer("📸 Пожалуйста, отправь <b>фото</b>.", reply_markup=get_cancel_kb(), parse_mode="HTML")

async def process_battle_album_edit(message: Message, state: FSMContext, mgid: str):
    await asyncio.sleep(1.5)
    photos = album_buffer.pop(mgid, [])
    if await state.get_state() is None:
        return
    await state.update_data(photos=photos)
    await state.set_state(BattleReg.reviewing)
    await message.answer(f"📸 Фото обновлены! Загружено: <b>{len(photos)} шт.</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_edit_sl_id")
async def battle_edit_sl_id(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🆔 Введи новый SuperLive ID", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BattleEdit.editing_sl_id)
    await callback.answer()

@dp.message(BattleEdit.editing_sl_id)
async def battle_edit_sl_id_input(message: Message, state: FSMContext):
    await state.update_data(sl_id=message.text)
    await state.set_state(BattleReg.reviewing)
    await message.answer(f"✅ SuperLive ID обновлён: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("battle"), parse_mode="HTML")

@dp.callback_query(F.data == "battle_confirm")
async def battle_confirm(callback: CallbackQuery, state: FSMContext):
    data   = await state.get_data()
    photos = data.get('photos', [])
    report = (
        f"🔥 <b>НОВАЯ ЗАЯВКА НА БАТТЛ!</b>\n\n"
        f"<blockquote>👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
        f"📅 Дата: {data.get('date','—')}\n"
        f"⏰ Время: {data.get('time','—')}\n"
        f"🚫 Нежел. страны: {data.get('prefs','—')}\n"
        f"🆔 SuperLive ID: {data.get('sl_id','—')}</blockquote>\n\n"
        f"USER_ID:<code>{callback.from_user.id}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять заявку", callback_data=f"accept_battle_{callback.from_user.id}")]])
    for admin_id in ADMIN_IDS:
        try:
            if len(photos) == 1:
                await bot.send_photo(admin_id, photo=photos[0], caption=report, reply_markup=kb, parse_mode="HTML")
            elif len(photos) > 1:
                media = [InputMediaPhoto(media=photos[0], caption=report, parse_mode="HTML")]
                for pid in photos[1:]:
                    media.append(InputMediaPhoto(media=pid))
                await bot.send_media_group(admin_id, media=media)
                await bot.send_message(admin_id, f"👆 Заявка от <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>", reply_markup=kb, parse_mode="HTML")
            else:
                await bot.send_message(admin_id, report, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Ошибка отправки: {e}")
    await state.clear()
    await callback.message.edit_text("✅ <b>Заявка на баттл отправлена!</b> Ожидай ответа. 🎉", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("accept_battle_"), F.from_user.id.in_(ADMIN_IDS))
async def process_accept_battle(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[2])
    text    = callback.message.caption or callback.message.text or ""
    dm = re.search(r'📅 Дата: (.*)', text)
    tm = re.search(r'⏰ Время: (.*)', text)
    await add_battle(user_id, dm.group(1).strip() if dm else "Не указана", tm.group(1).strip() if tm else "Не указано")
    await bot.send_message(user_id, "✅ <b>Заявка на баттл принята!</b> Ожидай подробности.", parse_mode="HTML")
    new = text.replace("🔥 <b>НОВАЯ ЗАЯВКА НА БАТТЛ!</b>", "✅ <b>БАТТЛ ПРИНЯТ И ДОБАВЛЕН В СПИСОК</b>")
    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=new, reply_markup=None, parse_mode="HTML")
        else:
            await callback.message.edit_text(new, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Одобрено ✅")

# ========== ЗАЯВКА НА ДЕНЬ РОЖДЕНИЯ ==========

@dp.callback_query(F.data == "apply_bday")
async def bday_start_cb(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Продолжить", callback_data="bday_continue")],
        [InlineKeyboardButton(text="🚫 Закрыть",    callback_data="cancel_action")]
    ])
    await callback.message.answer(
        "🚀 <b>День рождения (ДР)</b> — это специальный ивент для стримеров, созданный для увеличения активности, заработка и роста аудитории 📈\n\n"
        "Во время ДР ты получаешь больше внимания зрителей👀, активности в чате💬 и шанс собрать больше кристаллов💎 и подписчиков🔥\n\n"
        "Заявку необходимо подавать за 7-10 дней до желаемой даты.\n\n"
        "🗓 ДР не обязательно проводить в реальную дату рождения — ты можешь выбрать дату за несколько дней или недель до настоящего ДР📅\n\n"
        "• При подаче заявки укажи диапазон дат и времени, чтобы бот подобрал оптимальный момент проведения.\n\n"
        "📆 Дата: <code>1.01 - 5.01</code>\n"
        "⏰ Время: <code>20:00 - 23:00</code>\n\n"
        "Дата и время будут подобраны на основе указанных диапазонов.",
        reply_markup=kb, parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "bday_continue")
async def bday_step1(callback: CallbackQuery, state: FSMContext):
    await state.clear()
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

@dp.message(BdayReg.waiting_for_photo, ~F.photo)
async def bday_photo_wrong(message: Message):
    await message.answer("📸 Пожалуйста, отправь <b>фото</b>.", reply_markup=get_cancel_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "bday_to_summary")
async def bday_to_summary(callback: CallbackQuery, state: FSMContext):
    await send_bday_summary(callback, state)

@dp.callback_query(F.data == "bday_fix")
async def bday_fix(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BdayReg.reviewing)
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
    await state.set_state(BdayReg.reviewing)
    await message.answer(f"✅ Ник обновлён: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.callback_query(F.data == "bday_edit_id")
async def bday_edit_id(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🆔 Введи новый SuperLive ID", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayEdit.editing_id)
    await callback.answer()

@dp.message(BdayEdit.editing_id)
async def bday_edit_id_input(message: Message, state: FSMContext):
    await state.update_data(sl_id=message.text)
    await state.set_state(BdayReg.reviewing)
    await message.answer(f"✅ ID обновлён: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.callback_query(F.data == "bday_edit_date")
async def bday_edit_date(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📅 Введи новую дату\n\nФормат: <code>1.01 - 5.01</code>", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayEdit.editing_date)
    await callback.answer()

@dp.message(BdayEdit.editing_date)
async def bday_edit_date_input(message: Message, state: FSMContext):
    await state.update_data(date=message.text)
    await state.set_state(BdayReg.reviewing)
    await message.answer(f"✅ Дата обновлена: <b>{message.text}</b>", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.callback_query(F.data == "bday_edit_photo")
async def bday_edit_photo(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📸 Отправь новое фото", reply_markup=get_cancel_kb(), parse_mode="HTML")
    await state.set_state(BdayEdit.editing_photo)
    await callback.answer()

@dp.message(BdayEdit.editing_photo, F.photo)
async def bday_edit_photo_input(message: Message, state: FSMContext):
    await state.update_data(photo_id=message.photo[-1].file_id)
    await state.set_state(BdayReg.reviewing)
    await message.answer("✅ Фото обновлено!", reply_markup=get_confirm_edit_kb("bday"), parse_mode="HTML")

@dp.message(BdayEdit.editing_photo, ~F.photo)
async def bday_edit_photo_wrong(message: Message):
    await message.answer("📸 Пожалуйста, отправь <b>фото</b>.", reply_markup=get_cancel_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "bday_confirm")
async def bday_confirm(callback: CallbackQuery, state: FSMContext):
    data     = await state.get_data()
    photo_id = data.get('photo_id')
    report = (
        f"🎂 <b>НОВАЯ ЗАЯВКА НА ДЕНЬ РОЖДЕНИЯ!</b>\n\n"
        f"<blockquote>👤 От: <a href='tg://user?id={callback.from_user.id}'>{callback.from_user.full_name}</a>\n"
        f"🎭 Ник: {data.get('nick','—')}\n"
        f"🆔 ID: <code>{data.get('sl_id','—')}</code>\n"
        f"📅 Дата: {data.get('date','—')}</blockquote>\n\n"
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
    text    = callback.message.caption or callback.message.text or ""
    nm = re.search(r'🎭 Ник: (.*)', text)
    im = re.search(r'🆔 ID: <code>(.*)</code>', text)
    dm = re.search(r'📅 Дата: (.*)', text)
    await add_bday(user_id,
                   nm.group(1).strip() if nm else "Неизвестно",
                   im.group(1).strip() if im else "0",
                   dm.group(1).strip() if dm else "Не указана")
    await bot.send_message(user_id, "✅ <b>Твоя заявка на День Рождения одобрена!</b>", parse_mode="HTML")
    new = text.replace("🎂 <b>НОВАЯ ЗАЯВКА НА ДЕНЬ РОЖДЕНИЯ!</b>", "✅ <b>ДР ПРИНЯТ И ДОБАВЛЕН В СПИСОК</b>")
    try:
        if callback.message.caption:
            await callback.message.edit_caption(caption=new, reply_markup=None, parse_mode="HTML")
        else:
            await callback.message.edit_text(new, reply_markup=None, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Одобрено ✅")

# ========== СПИСКИ БАТТЛОВ И ДР ==========

@dp.message(F.text == "📋 Список баттлов", F.from_user.id.in_(ADMIN_IDS))
async def show_battles(message: Message):
    battles = await get_battles()
    if not battles: return await message.answer("Сейчас нет активных записей на баттл.")
    text = "⚔️ <b>АКТИВНЫЕ ЗАПИСИ НА БАТТЛ:</b>\n────────────────\n\n"
    builder = InlineKeyboardBuilder()
    for idx, (u_id, b_date, b_time, username, full_name) in enumerate(battles, 1):
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
    text = "⚔️ <b>АКТИВНЫЕ ЗАПИСИ НА БАТТЛ:</b>\n────────────────\n\n"
    builder = InlineKeyboardBuilder()
    for idx, (u_id, b_date, b_time, username, full_name) in enumerate(battles, 1):
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a> — {b_date} в {b_time}\n"
        builder.button(text=f"❌ Завершить: {full_name}", callback_data=f"del_battle_{u_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.message(F.text == "🎂 Список ДР", F.from_user.id.in_(ADMIN_IDS))
async def show_bdays(message: Message):
    bdays = await get_bdays()
    if not bdays: return await message.answer("Сейчас нет активных записей на ДР.")
    text = "🎂 <b>АКТИВНЫЕ ЗАПИСИ НА ДЕНЬ РОЖДЕНИЯ:</b>\n────────────────\n\n"
    builder = InlineKeyboardBuilder()
    for idx, (u_id, nick, sl_id, b_date, username, full_name) in enumerate(bdays, 1):
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
    text = "🎂 <b>АКТИВНЫЕ ЗАПИСИ НА ДЕНЬ РОЖДЕНИЯ:</b>\n────────────────\n\n"
    builder = InlineKeyboardBuilder()
    for idx, (u_id, nick, sl_id, b_date, username, full_name) in enumerate(bdays, 1):
        text += f"{idx}. <a href='tg://user?id={u_id}'>{full_name}</a> (Ник: {nick}) — Дата: {b_date}\n"
        builder.button(text=f"❌ Завершить ДР: {nick}", callback_data=f"del_bday_{u_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="close_panel"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# ========== ВЫВОДЫ/ВЫПЛАТЫ (ADMIN) ==========

@dp.message(F.text == "💰 Выводы/выплаты", F.from_user.id.in_(ADMIN_IDS))
async def show_payouts_menu(message: Message):
    await render_payouts_menu(message)

@dp.callback_query(F.data == "pout_menu", F.from_user.id.in_(ADMIN_IDS))
async def pout_menu_cb(callback: CallbackQuery):
    await render_payouts_menu(callback)

@dp.callback_query(F.data == "pout_noop")
async def pout_noop(callback: CallbackQuery):
    await callback.answer()

@dp.callback_query(F.data.startswith("pc_list_"), F.from_user.id.in_(ADMIN_IDS))
async def pc_list_cb(callback: CallbackQuery):
    await render_pending_payouts(callback, int(callback.data.split("_")[2]))
    await callback.answer()

@dp.callback_query(F.data.startswith("pc_det_"), F.from_user.id.in_(ADMIN_IDS))
async def pc_detail_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    p_id, page = int(parts[2]), int(parts[3])
    payout = await get_payout_by_id(p_id)
    if not payout:
        await callback.answer("❌ Заявка не найдена.", show_alert=True)
        await render_pending_payouts(callback, page)
        return
    p_id_val, u_id, status, created_at, confirmed_at, full_name = payout
    if status != 0:
        await callback.answer("⚠️ Эта заявка уже подтверждена!", show_alert=True)
        await render_pending_payouts(callback, page)
        return
    text = (
        "💰 <b>ЗАЯВКА НА ВЫВОД</b>\n"
        "────────────────\n\n"
        f"👤 <a href='tg://user?id={u_id}'><b>{full_name}</b></a>\n"
        f"🕐 Подана: {created_at or '—'}\n\n"
        "<i>Нажми «Поставить вывод» чтобы подтвердить.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Поставить вывод", callback_data=f"pc_do_{p_id}_{page}")],
        [InlineKeyboardButton(text="🔙 Назад",           callback_data=f"pc_list_{page}")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("pc_do_"), F.from_user.id.in_(ADMIN_IDS))
async def pc_do_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    p_id, page = int(parts[2]), int(parts[3])
    payout = await get_payout_by_id(p_id)
    if not payout:
        await callback.answer("❌ Заявка не найдена.", show_alert=True)
        await render_pending_payouts(callback, page)
        return
    p_id_val, u_id, status, created_at, confirmed_at, full_name = payout
    await confirm_payout(p_id)
    try:
        await bot.send_message(u_id, "✅ <b>Твой вывод подтверждён!</b>\n\nВыплата обрабатывается и скоро придёт на твои реквизиты.", parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("✅ Вывод поставлен! Переведён в «Выплатить вывод».", show_alert=True)
    await render_pending_payouts(callback, page)

@dp.callback_query(F.data.startswith("pp_list_"), F.from_user.id.in_(ADMIN_IDS))
async def pp_list_cb(callback: CallbackQuery):
    await render_confirmed_payouts(callback, int(callback.data.split("_")[2]))
    await callback.answer()

@dp.callback_query(F.data.startswith("pp_det_"), F.from_user.id.in_(ADMIN_IDS))
async def pp_detail_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    p_id, page = int(parts[2]), int(parts[3])
    payout = await get_payout_by_id(p_id)
    if not payout:
        await callback.answer("❌ Заявка не найдена.", show_alert=True)
        await render_confirmed_payouts(callback, page)
        return
    p_id_val, u_id, status, created_at, confirmed_at, full_name = payout
    if status != 1:
        await callback.answer("⚠️ Статус заявки изменился.", show_alert=True)
        await render_confirmed_payouts(callback, page)
        return
    text = (
        "💸 <b>ВЫПЛАТА</b>\n"
        "────────────────\n\n"
        f"👤 <a href='tg://user?id={u_id}'><b>{full_name}</b></a>\n"
        f"🕐 Подана: {created_at or '—'}\n"
        f"✅ Подтверждена: {confirmed_at or '—'}\n\n"
        "<i>Нажми «ВЫПЛАЧЕНО» после отправки денег.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 ВЫПЛАЧЕНО", callback_data=f"pp_do_{p_id}_{page}")],
        [InlineKeyboardButton(text="🔙 Назад",     callback_data=f"pp_list_{page}")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("pp_do_"), F.from_user.id.in_(ADMIN_IDS))
async def pp_do_cb(callback: CallbackQuery):
    parts = callback.data.split("_")
    p_id, page = int(parts[2]), int(parts[3])
    payout = await get_payout_by_id(p_id)
    if not payout:
        await callback.answer("❌ Заявка не найдена.", show_alert=True)
        await render_confirmed_payouts(callback, page)
        return
    p_id_val, u_id, status, created_at, confirmed_at, full_name = payout
    await delete_payout(p_id)
    try:
        await bot.send_message(u_id, "💸 <b>Выплата отправлена!</b>\n\nДеньги отправлены на твои реквизиты. Если не получила — обратись к менеджеру.", parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(f"💸 Выплата для {full_name} выполнена!", show_alert=True)
    await render_confirmed_payouts(callback, page)

# ========== БАЗА ВОПРОСОВ (ADMIN) ==========

@dp.message(F.text == "❓ База вопросов", F.from_user.id.in_(ADMIN_IDS))
async def show_questions_db(message: Message):
    await render_q_menu(message)

@dp.callback_query(F.data == "qa_menu", F.from_user.id.in_(ADMIN_IDS))
async def q_menu_cb(callback: CallbackQuery):
    await render_q_menu(callback)

@dp.callback_query(F.data == "qa_noop")
async def q_noop(callback: CallbackQuery):
    await callback.answer()

@dp.callback_query(F.data.startswith("qa_all_"), F.from_user.id.in_(ADMIN_IDS))
async def q_all_page(callback: CallbackQuery):
    await render_all_questions(callback, int(callback.data.split("_")[2]))
    await callback.answer()

@dp.callback_query(F.data.startswith("qa_mine_"), F.from_user.id.in_(ADMIN_IDS))
async def q_mine_page(callback: CallbackQuery):
    await render_my_questions(callback, int(callback.data.split("_")[2]))
    await callback.answer()

@dp.callback_query(F.data.startswith("qa_detail_"), F.from_user.id.in_(ADMIN_IDS))
async def q_detail(callback: CallbackQuery):
    parts = callback.data.split("_")
    await render_question_detail(callback, int(parts[2]), parts[3], int(parts[4]))
    await callback.answer()

@dp.callback_query(F.data.startswith("qa_take_"), F.from_user.id.in_(ADMIN_IDS))
async def q_take(callback: CallbackQuery):
    parts = callback.data.split("_")
    q_id, src, page = int(parts[2]), parts[3], int(parts[4])
    question = await get_question_by_id(q_id)
    if not question:
        await callback.answer("❌ Вопрос уже удалён.", show_alert=True)
        await render_all_questions(callback, page)
        return
    _, u_id, name, q_text, assigned_to, created_at = question
    if assigned_to is not None:
        await callback.answer("⚠️ Этот вопрос уже взял другой администратор!", show_alert=True)
        await render_all_questions(callback, page)
        return
    await assign_question(q_id, callback.from_user.id)
    await callback.answer("✅ Вопрос взят! Он теперь в «Мои вопросы».", show_alert=True)
    await render_question_detail(callback, q_id, 'm', 0)

@dp.callback_query(F.data.startswith("qa_dc_"), F.from_user.id.in_(ADMIN_IDS))
async def q_delete_confirm(callback: CallbackQuery):
    parts = callback.data.split("_")
    q_id, src, page = int(parts[2]), parts[3], int(parts[4])
    question = await get_question_by_id(q_id)
    if not question:
        await callback.answer("❌ Вопрос уже удалён.", show_alert=True)
        if src == 'a': await render_all_questions(callback, page)
        else:          await render_my_questions(callback, page)
        return
    _, u_id, name, q_text, assigned_to, created_at = question
    preview = (q_text[:120] + "…") if len(q_text) > 120 else q_text
    text = (
        "⚠️ <b>Подтвердите удаление:</b>\n\n"
        f"👤 <a href='tg://user?id={u_id}'><b>{name}</b></a>\n"
        f"<blockquote>{preview}</blockquote>\n\n"
        "❗ Это действие нельзя отменить!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"qa_dy_{q_id}_{src}_{page}"),
         InlineKeyboardButton(text="❌ Отмена",      callback_data=f"qa_detail_{q_id}_{src}_{page}")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("qa_dy_"), F.from_user.id.in_(ADMIN_IDS))
async def q_delete_yes(callback: CallbackQuery):
    parts = callback.data.split("_")
    q_id, src, page = int(parts[2]), parts[3], int(parts[4])
    await delete_question(q_id)
    await callback.answer("🗑 Вопрос удалён!")
    if src == 'a': await render_all_questions(callback, page)
    else:          await render_my_questions(callback, page)

@dp.callback_query(F.data.startswith("qa_ans_"), F.from_user.id.in_(ADMIN_IDS))
async def q_start_answer(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    q_id, src, page = int(parts[2]), parts[3], int(parts[4])
    question = await get_question_by_id(q_id)
    if not question:
        await callback.answer("❌ Вопрос не найден.", show_alert=True)
        await render_my_questions(callback, page)
        return
    q_id_val, u_id, name, q_text, assigned_to, created_at = question
    await state.update_data(answer_q_id=q_id_val, answer_u_id=u_id, answer_name=name, answer_src=src, answer_page=page)
    await state.set_state(AdminAnswer.waiting_for_answer)
    await callback.message.answer(
        f"💬 <b>Ответ для</b> <a href='tg://user?id={u_id}'><b>{name}</b></a>:\n\n"
        f"<blockquote>{q_text}</blockquote>\n\n✏️ Напиши текст ответа:",
        reply_markup=get_cancel_kb(), parse_mode="HTML"
    )
    await callback.answer()

@dp.message(AdminAnswer.waiting_for_answer, F.from_user.id.in_(ADMIN_IDS))
async def q_process_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    q_id, u_id, name = data.get('answer_q_id'), data.get('answer_u_id'), data.get('answer_name')
    try:
        await bot.send_message(u_id, f"<b>Ответ от команды SAGE:</b>\n\n<blockquote>{message.text}</blockquote>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ <b>Не удалось доставить ответ.</b>\nОшибка: {e}", parse_mode="HTML")
        await state.clear()
        return
    if q_id:
        await delete_question(q_id)
    await state.clear()
    await message.answer(f"✅ <b>Ответ отправлен пользователю {name}. Вопрос закрыт.</b>", parse_mode="HTML")

# ========== БАЗА ДАННЫХ И РАССЫЛКА ==========

@dp.message(F.text == "📊 База данных", F.from_user.id.in_(ADMIN_IDS))
async def show_database(message: Message):
    users = await get_all_users()
    if not users: return await message.answer("База пока пуста.")
    text = f"📊 <b>БАЗА ПОЛЬЗОВАТЕЛЕЙ ({len(users)} чел.):</b>\n────────────────\n\n"
    for idx, (u_id, username, name) in enumerate(users, 1):
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
        uid = row[0]
        try:
            if message.photo:
                await bot.send_photo(uid, photo=message.photo[-1].file_id, caption=message.caption, caption_entities=message.caption_entities)
            elif message.video:
                await bot.send_video(uid, video=message.video.file_id, caption=message.caption, caption_entities=message.caption_entities)
            else:
                await bot.send_message(uid, text=message.text, entities=message.entities)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ <b>Рассылка завершена! Получили: {count} чел.</b>", reply_markup=get_main_kb(message.from_user.id), parse_mode="HTML")
    await state.clear()

# ========== ВОПРОСЫ ПОЛЬЗОВАТЕЛЕЙ ==========

@dp.message(Command("help"))
async def ask_question_start(message: Message, state: FSMContext):
    await message.answer(
        "Напиши свой вопрос или проблему 💬\n\nМы постараемся помочь тебе как можно быстрее ❤️",
        reply_markup=get_cancel_kb(), parse_mode="HTML"
    )
    await state.set_state(Form.waiting_for_question)

@dp.message(Form.waiting_for_question)
async def process_question(message: Message, state: FSMContext):
    q_id = await add_question(message.from_user.id, message.from_user.full_name, message.text)
    report = (
        f"📩 <b>НОВЫЙ ВОПРОС!</b>\n\n"
        f"USER_ID:<code>{message.from_user.id}</code> | Q_ID:<code>{q_id}</code>\n"
        f"👤 От: <a href='tg://user?id={message.from_user.id}'>{message.from_user.full_name}</a>\n\n"
        f"<blockquote>{message.text}</blockquote>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, report, parse_mode="HTML")
        except Exception:
            pass
    await message.answer("✅ <b>Отправлено! Ожидай ответа.</b>", parse_mode="HTML")
    await state.clear()

@dp.message(F.reply_to_message & F.from_user.id.in_(ADMIN_IDS))
async def admin_reply(message: Message):
    reply_text = message.reply_to_message.caption or message.reply_to_message.text
    if not reply_text or "USER_ID:" not in reply_text:
        return
    um = re.search(r'USER_ID:\s*<code>(\d+)</code>', reply_text) or re.search(r'USER_ID:(\d+)', reply_text)
    if um:
        user_id = int(um.group(1))
        await bot.send_message(user_id, f"<b>Ответ от команды SAGE:</b>\n\n<blockquote>{message.text}</blockquote>", parse_mode="HTML")
        qm = re.search(r'Q_ID:\s*<code>(\d+)</code>', reply_text) or re.search(r'Q_ID:(\d+)', reply_text)
        if qm:
            await delete_question(int(qm.group(1)))
            await message.answer("✅ <b>Ответ отправлен, вопрос удалён из базы.</b>", parse_mode="HTML")
        else:
            await message.answer("✅ <b>Отправлено!</b>", parse_mode="HTML")

# ========== ЗАПУСК ==========

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await setup_bot_commands(bot)
    print("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())