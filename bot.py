    import asyncio
import logging
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.client.default import DefaultBotProperties

# ====================== НАСТРОЙКИ justrunmy.app ======================
BOT_TOKEN = os.getenv("BOT_TOKEN")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")          # пример: -1001234567890
CHANNEL_LINK = "https://t.me/goal90stat"
LIVE_LINK = "http://t.me/Sp0rtplusbot/sp0rt"
ADMIN_PASSWORD = "123"

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан в переменных окружения justrunmy.app!")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()
DB_NAME = "bot.db"

# ====================== FSM ======================
class AdminStates(StatesGroup):
    waiting_password = State()
    waiting_match_text = State()
    waiting_match_file = State()
    waiting_support_reply = State()
    waiting_review_action = State()
    waiting_gift_user = State()

class UserStates(StatesGroup):
    waiting_review = State()
    waiting_support = State()

# ====================== КЛАВИАТУРЫ ======================
def main_keyboard():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Матчи"), KeyboardButton(text="Канал")],
        [KeyboardButton(text="Поддержка"), KeyboardButton(text="Отзывы")],
        [KeyboardButton(text="Live футбол"), KeyboardButton(text="Лимит")],
        [KeyboardButton(text="Подписка")],
        [KeyboardButton(text="Политика и согласие")]
    ], resize_keyboard=True)
    return kb

def payment_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Silver 2 недели — 100₽", callback_data="sub_silver_14")],
        [InlineKeyboardButton(text="Silver месяц — 200₽", callback_data="sub_silver_28")],
        [InlineKeyboardButton(text="Gold 2 недели — 150₽", callback_data="sub_gold_14")],
        [InlineKeyboardButton(text="Gold месяц — 300₽", callback_data="sub_gold_28")]
    ])

def policy_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Политика конфиденциальности", url="https://telegra.ph/Politika-konfidencialnosti-08-15-17")],
        [InlineKeyboardButton(text="Пользовательское соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-08-15-10")]
    ])

def admin_keyboard():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Добавить матч"), KeyboardButton(text="📢 Опубликовать матчи")],
        [KeyboardButton(text="💬 Поддержка"), KeyboardButton(text="⭐ Отзывы")],
        [KeyboardButton(text="💰 Платные подписки"), KeyboardButton(text="🎁 Подарок")]
    ], resize_keyboard=True)
    return kb

# ====================== БАЗА ДАННЫХ ======================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                subscription TEXT DEFAULT 'free',
                sub_end TEXT,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_text TEXT,
                file_id TEXT,
                is_published INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS user_match_access (
                telegram_id INTEGER,
                match_id INTEGER,
                PRIMARY KEY (telegram_id, match_id)
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                telegram_id INTEGER,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (telegram_id, date)
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                text TEXT,
                status TEXT DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                text TEXT,
                status TEXT DEFAULT 'new',
                admin_reply TEXT
            );
        ''')
        await db.commit()

async def get_users_count():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            return (await cur.fetchone())[0]

async def add_or_update_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        await db.commit()

async def get_subscription(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT subscription, sub_end FROM users WHERE telegram_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return "free", None
            sub, end = row
            if end and datetime.fromisoformat(end) < datetime.now():
                await db.execute(
                    "UPDATE users SET subscription='free', sub_end=NULL WHERE telegram_id=?",
                    (user_id,)
                )
                await db.commit()
                return "free", None
            return sub, end

# ====================== ЛИМИТЫ ======================
def get_max_matches(sub_type: str, weekday: int) -> int:
    if sub_type == "gold_28": return 999
    if sub_type == "gold_14": return [5,5,5,5,8,17,17][weekday]
    if sub_type == "silver_28": return [3,3,3,3,5,12,12][weekday]
    if sub_type == "silver_14": return [3,3,3,3,5,10,10][weekday]
    return [1,1,1,1,2,2,2][weekday]  # free

async def get_daily_count(user_id: int) -> int:
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT count FROM daily_usage WHERE telegram_id=? AND date=?", (user_id, today)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def increment_daily(user_id: int):
    today = datetime.now().date().isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO daily_usage (telegram_id, date, count)
            VALUES (?, ?, COALESCE((SELECT count + 1 FROM daily_usage WHERE telegram_id=? AND date=?), 1))
        """, (user_id, today, user_id, today))
        await db.commit()

# ====================== СТАРТ ======================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await add_or_update_user(message.from_user.id, message.from_user.username)
    count = await get_users_count()

    text = f"""👋 Привет я Нейроаналитик 🤖

Собираю футбольную статистику из спортивных ресурсов. 
Травмы, офиц. матчи, положение в турнирной таблице, забитые - пропущенные мячи, моменты xG. 
Анализируя всю статистику предлагаю варианты с наиболее успешным исходом.

Тебе не нужно тратить огромное количество времени для поиска статистики и анализа матча, так как это я уже сделал за тебя. 

⛳️ Добро пожаловать! 
Пользователей: <b>{count}</b>

Все матчи и статистика уже доступны ~ ниже кнопка: «Матчи»
Подпишись на наш канал чтобы быть в курсе событий ~ ниже кнопка: «Канал»
Посмотри какие лимиты доступны ~ ниже кнопка: «Лимит»"""

    await message.answer(text, reply_markup=main_keyboard())

# ====================== АДМИН ======================
@dp.message(Command("gol"))
async def admin_login(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_password)
    await message.answer("Введите пароль администратора:")

@dp.message(AdminStates.waiting_password)
async def check_admin_pass(message: Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        await state.clear()
        await message.answer("✅ Добро пожаловать в админ-панель!", reply_markup=admin_keyboard())
    else:
        await message.answer("❌ Неверный пароль!")

@dp.message(F.text == "➕ Добавить матч")
async def admin_add_match(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_match_text)
    await message.answer("Отправьте текст события (матч):")

@dp.message(AdminStates.waiting_match_text)
async def save_match_text(message: Message, state: FSMContext):
    await state.update_data(event_text=message.text)
    await state.set_state(AdminStates.waiting_match_file)
    await message.answer("Теперь отправьте файл (документ) для этого матча:")

@dp.message(AdminStates.waiting_match_file, F.document)
async def save_match_file(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id = message.document.file_id

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO matches (event_text, file_id, is_published) VALUES (?, ?, 0)",
            (data["event_text"], file_id)
        )
        await db.commit()

    await state.clear()
    await message.answer("✅ Матч добавлен (не опубликован).")

@dp.message(F.text == "📢 Опубликовать матчи")
async def publish_matches(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE matches SET is_published = 1 WHERE is_published = 0")
        await db.commit()
    await message.answer("✅ Все матчи опубликованы!")

@dp.message(F.text == "💬 Поддержка")
async def admin_support(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, telegram_id, text FROM support_tickets WHERE status = 'new'") as cur:
            tickets = await cur.fetchall()

    if not tickets:
        await message.answer("Нет новых сообщений в поддержке.")
        return

    kb = []
    for tid, uid, text in tickets:
        kb.append([InlineKeyboardButton(text=f"Ticket {tid} from {uid}", callback_data=f"support_{tid}")])

    await message.answer("Новые тикеты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("support_"))
async def admin_reply_support(callback: CallbackQuery, state: FSMContext):
    tid = int(callback.data.split("_")[1])
    await state.set_state(AdminStates.waiting_support_reply)
    await state.update_data(ticket_id=tid)
    await callback.message.edit_text("Отправьте ответ для тикета:")

@dp.message(AdminStates.waiting_support_reply)
async def save_support_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    tid = data["ticket_id"]
    reply = message.text
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE support_tickets SET admin_reply = ?, status = 'replied' WHERE id = ?", (reply, tid))
        await db.commit()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id FROM support_tickets WHERE id = ?", (tid,)) as cur:
            uid = (await cur.fetchone())[0]

    await bot.send_message(uid, f"Ответ от поддержки: {reply}")
    await state.clear()
    await message.answer("✅ Ответ отправлен!")

@dp.message(F.text == "⭐ Отзывы")
async def admin_reviews(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, telegram_id, text FROM reviews WHERE status = 'pending'") as cur:
            reviews = await cur.fetchall()

    if not reviews:
        await message.answer("Нет отзывов на модерации.")
        return

    kb = []
    for rid, uid, text in reviews:
        kb.append([InlineKeyboardButton(text=f"Review {rid} from {uid}", callback_data=f"review_{rid}")])

    await message.answer("Отзывы на модерации:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("review_"))
async def admin_review_action(callback: CallbackQuery):
    rid = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT text FROM reviews WHERE id = ?", (rid,)) as cur:
            text = (await cur.fetchone())[0]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Approve", callback_data=f"approve_{rid}"),
         InlineKeyboardButton(text="Reject", callback_data=f"reject_{rid}")]
    ])
    await callback.message.edit_text(f"Отзыв: {text}", reply_markup=kb)

@dp.callback_query(F.data.startswith("approve_"))
async def approve_review(callback: CallbackQuery):
    rid = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE reviews SET status = 'approved' WHERE id = ?", (rid,))
        await db.commit()
    await callback.message.edit_text("✅ Отзыв одобрен!")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_review(callback: CallbackQuery):
    rid = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE reviews SET status = 'rejected' WHERE id = ?", (rid,))
        await db.commit()
    await callback.message.edit_text("❌ Отзыв отклонен!")

@dp.message(F.text == "💰 Платные подписки")
async def admin_paid_subs(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT telegram_id, username, subscription, sub_end FROM users WHERE subscription != 'free'") as cur:
            subs = await cur.fetchall()

    if not subs:
        await message.answer("Нет платных подписок.")
        return

    text = "Платные подписки:\n"
    for uid, uname, sub, end in subs:
        text += f"{uname} ({uid}): {sub} до {end}\n"
    await message.answer(text)

@dp.message(F.text == "🎁 Подарок")
async def admin_gift(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_gift_user)
    await message.answer("Отправьте telegram_id пользователя и тип подписки (например: 123456 gold_28)")

@dp.message(AdminStates.waiting_gift_user)
async def save_gift(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Неверный формат.")
        return
    try:
        uid = int(parts[0])
    except:
        await message.answer("Неверный id.")
        return
    sub_type = parts[1]
    days = 14 if "14" in sub_type else 28 if "28" in sub_type else 0
    if days == 0:
        await message.answer("Неверный тип.")
        return
    until = (datetime.now() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET subscription=?, sub_end=? WHERE telegram_id=?",
            (sub_type, until, uid)
        )
        await db.commit()
    await state.clear()
    await message.answer("✅ Подарок отправлен!")

# ====================== МАТЧИ ======================
@dp.message(F.text == "Матчи")
async def show_matches(message: Message):
    sub, _ = await get_subscription(message.from_user.id)
    try:
        member = await bot.get_chat_member(int(CHANNEL_ID), message.from_user.id)
        subscribed = member.status in ["member", "administrator", "creator"]
    except:
        subscribed = False

    if sub == "free" and not subscribed:
        await message.answer(f"Чтобы смотреть матчи — подпишись на канал 👇\n{CHANNEL_LINK}")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, event_text FROM matches WHERE is_published=1") as cur:
            rows = await cur.fetchall()

    if not rows:
        await message.answer("Пока нет опубликованных матчей.")
        return

    kb = []
    for mid, text in rows:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                "SELECT 1 FROM user_match_access WHERE telegram_id=? AND match_id=?",
                (message.from_user.id, mid)
            ) as cur:
                already = await cur.fetchone()
        kb.append([InlineKeyboardButton(
            text=f"✅ {text}" if already else text,
            callback_data=f"match_{mid}" if not already else f"already_{mid}"
        )])

    await message.answer("Выберите матч:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("match_"))
async def give_match_file(callback: CallbackQuery):
    match_id = int(callback.data.split("_")[1])
    sub, _ = await get_subscription(callback.from_user.id)
    weekday = datetime.now().weekday()
    max_m = get_max_matches(sub, weekday)
    opened = await get_daily_count(callback.from_user.id)

    if opened >= max_m:
        await callback.answer("Лимит на сегодня исчерпан!", show_alert=True)
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT file_id FROM matches WHERE id=?", (match_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await callback.answer("Файл не найден")
            return
        file_id = row[0]

        await db.execute(
            "INSERT OR IGNORE INTO user_match_access (telegram_id, match_id) VALUES (?, ?)",
            (callback.from_user.id, match_id)
        )
        await db.commit()

    await increment_daily(callback.from_user.id)
    await callback.message.answer_document(file_id, caption="📊 Анализ и прогноз от Нейроаналитика")
    await callback.answer("✅ Файл отправлен!")

@dp.callback_query(F.data.startswith("already_"))
async def already_opened(callback: CallbackQuery):
    await callback.answer("Вы уже открыли этот матч.", show_alert=True)

# ====================== ПОДПИСКА И ПЛАТЕЖИ ======================
@dp.message(F.text == "Подписка")
async def show_sub_menu(message: Message):
    await message.answer(
        "Выберите подписку ниже 👇\n\nПосле оплаты лимиты увеличатся автоматически!",
        reply_markup=payment_keyboard()
    )

@dp.callback_query(F.data.startswith("sub_"))
async def create_invoice(callback: CallbackQuery):
    plan = callback.data
    prices = {
        "sub_silver_14": ("Silver 2 недели", 10000),
        "sub_silver_28": ("Silver месяц", 20000),
        "sub_gold_14": ("Gold 2 недели", 15000),
        "sub_gold_28": ("Gold месяц", 30000)
    }
    title, amount = prices.get(plan, ("Подписка", 10000))

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=f"Подписка {title}",
        payload=plan,
        provider_token=PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label=title, amount=amount)]
    )

@dp.message(F.successful_payment)
async def payment_success(message: Message):
    payload = message.successful_payment.invoice_payload
    sub_type = payload.replace("sub_", "")
    days = 14 if "14" in sub_type else 28

    until = (datetime.now() + timedelta(days=days)).isoformat()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET subscription=?, sub_end=? WHERE telegram_id=?",
            (sub_type, until, message.from_user.id)
        )
        await db.commit()

    await message.answer(f"✅ Подписка {sub_type} активирована на {days} дней!\nТеперь у тебя повышенные лимиты 🔥")

# ====================== ОСТАЛЬНЫЕ КНОПКИ ======================
@dp.message(F.text == "Канал")
async def send_channel(message: Message):
    await message.answer("Подписывайся на канал:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Канал", url=CHANNEL_LINK)]]))

@dp.message(F.text == "Live футбол")
async def send_live(message: Message):
    await message.answer("Live футбол:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Смотреть Live", url=LIVE_LINK)]]))

@dp.message(F.text == "Лимит")
async def send_limit_info(message: Message):
    sub, end = await get_subscription(message.from_user.id)
    weekday = datetime.now().weekday()
    max_m = get_max_matches(sub, weekday)
    used = await get_daily_count(message.from_user.id)
    text = f"Ваша подписка: {sub}"
    if end:
        text += f" до {end}"
    text += f"\nЛимит на сегодня: {max_m} матчей"
    text += f"\nИспользовано: {used}"
    await message.answer(text)

@dp.message(F.text == "Политика и согласие")
async def policy(message: Message):
    await message.answer("Выберите документ:", reply_markup=policy_keyboard())

# ====================== ОТЗЫВЫ ======================
@dp.message(F.text == "Отзывы")
async def start_review(message: Message, state: FSMContext):
    await state.set_state(UserStates.waiting_review)
    await message.answer("Напишите свой отзыв:")

@dp.message(UserStates.waiting_review)
async def save_review(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO reviews (telegram_id, text) VALUES (?, ?)", (message.from_user.id, message.text))
        await db.commit()
    await state.clear()
    await message.answer("✅ Отзыв отправлен на модерацию!")

# ====================== ПОДДЕРЖКА ======================
@dp.message(F.text == "Поддержка")
async def start_support(message: Message, state: FSMContext):
    await state.set_state(UserStates.waiting_support)
    await message.answer("Напишите сообщение в поддержку:")

@dp.message(UserStates.waiting_support)
async def save_support(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO support_tickets (telegram_id, text) VALUES (?, ?)", (message.from_user.id, message.text))
        await db.commit()
    await state.clear()
    await message.answer("✅ Сообщение отправлено в поддержку!")

# ====================== ЗАПУСК ======================
async def main():
    await init_db()
    scheduler.start()
    logging.info("🚀 Бот запущен на justrunmy.app")
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
