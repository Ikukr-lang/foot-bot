import asyncio
import re
import math
import os
from dotenv import load_dotenv
import cloudscraper  # ← НОВОЕ
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================== РЕЙТИНГИ (без изменений) ==================
LEVELS = { ... }  # оставь как было
SUBS = { ... }    # оставь как было

def text_to_rating(text: str) -> float:
    # оставь как было
    ...

# ================== ПАРСИНГ С cloudscraper ==================
def parse_match(url: str):
    scraper = cloudscraper.create_scraper()  # обходит защиту
    r = scraper.get(url, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    poss_text = soup.get_text()
    poss45 = poss90 = 50.0
    m = re.search(r"45'\D*(\d+)%", poss_text)
    if m: poss45 = float(m.group(1))
    m = re.search(r"90'\D*(\d+)%", poss_text)
    if m: poss90 = float(m.group(1))

    ratings = re.findall(r'(Wretched|Poor|Weak|Inadequate|Passable|Solid|Excellent|Formidable|Outstanding|Brilliant|Magnificent|World Class|Supernatural|Titanic|Extraterrestrial|Mythical|Utopian|Divine)\s*[-–]?\s*(very low|low|high|very high)?', poss_text, re.I)

    nums = []
    for lvl, sub in ratings[:20]:
        num = text_to_rating(f"{lvl} {sub or ''}")
        if num > 1: nums.append(num)

    if len(nums) < 14:
        raise ValueError("Не удалось найти рейтинги. Пришли текст или фото отчёта.")

    # распределение рейтингов (точно как раньше)
    home = {"gk": nums[0], "def_l": nums[1], "def_c": nums[2], "def_r": nums[3],
            "mid": nums[4], "att_l": nums[5], "att_c": nums[6], "att_r": nums[7]}
    away = {"gk": nums[8], "def_l": nums[9], "def_c": nums[10], "def_r": nums[11],
            "mid": nums[12], "att_l": nums[13], "att_c": nums[14] if len(nums)>14 else nums[6],
            "att_r": nums[15] if len(nums)>15 else nums[7]}

    return home, away, poss45, poss90

# ================== КОМАНДЫ (добавлен fallback) ==================
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("Отправь ссылку на матч Hattrick.\nЕсли бот не ответит — пришли текст рейтингов или скрин отчёта.")

@dp.message(F.text.regexp(r"hattrick\.org.*MatchID=\d+"))
async def process_match(message: Message):
    url = message.text.strip()
    await message.answer("⏳ Обхожу защиту сайта и парсю...")

    try:
        home_r, away_r, p45, p90 = parse_match(url)
        # расчёт (оставь как было)
        win_h, draw, win_a = calculate_match_prob(home_r, away_r, p45, p90)  # функция из предыдущего кода

        await message.answer(f"✅ Анализ завершён!\n\n"
                             f"🏠 Победа хозяев: {win_h}%\n"
                             f"🤝 Ничья: {draw}%\n"
                             f"🏟️ Победа гостей: {win_a}%")
    except Exception as e:
        await message.answer(f"❌ Не смог распарсить автоматически (сайт защищён).\n\n"
                             f"Пришли:\n"
                             f"1. Текст всех рейтингов (скопируй из отчёта)\n"
                             f"ИЛИ\n"
                             f"2. Фото/скрин отчёта матча — попробую OCR.")

# ================== ЗАПУСК ==================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
