import asyncio
import re
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
import random
import math
import os  # ← НОВОЕ: для переменной окружения

# ================== НАСТРОЙКИ (ПЕРЕМЕННАЯ НА БОТУ) ==================
# На BotHost зайди в настройки бота → "Environment variables" и добавь:
# Имя: BOT_TOKEN
# Значение: твой токен от @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Добавь переменную окружения на BotHost.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# (весь остальной код без изменений)

# Таблица конвертации рейтингов...
LEVELS = {
    "wretched": 1.0, "poor": 2.0, "weak": 3.0, "inadequate": 4.0,
    "passable": 5.0, "solid": 6.0, "excellent": 7.0, "formidable": 8.0,
    "outstanding": 9.0, "brilliant": 10.0, "magnificent": 11.0,
    "world class": 12.0, "supernatural": 13.0, "titanic": 14.0,
    "extraterrestrial": 15.0, "mythical": 16.0, "utopian": 17.0, "divine": 18.0
}
SUBS = {"very low": -0.375, "low": -0.125, "high": 0.125, "very high": 0.375}

def text_to_rating(text: str) -> float:
    text = text.lower().strip()
    for lvl_name, base in LEVELS.items():
        if lvl_name in text:
            val = base
            for sub_name, add in SUBS.items():
                if sub_name in text:
                    val += add
                    break
            if "high" in text and "very" not in text:
                val += 0.25
            elif "low" in text and "very" not in text:
                val -= 0.25
            return round(val, 2)
    return 5.0

# ================== ОСНОВНАЯ ФУНКЦИЯ РАСЧЁТА (без изменений) ==================
def calculate_match_prob(home_ratings: dict, away_ratings: dict, poss45: float, poss90: float):
    mid_h = home_ratings["mid"]
    mid_a = away_ratings["mid"]
    poss_calc = mid_h ** 1.5 / (mid_h ** 1.5 + mid_a ** 1.5)
    poss_avg = (poss45 + poss90) / 200 + poss_calc / 2
    poss_avg = max(0.3, min(0.7, poss_avg))

    chances_home = 15 * poss_avg
    chances_away = 15 * (1 - poss_avg)

    att_h = (home_ratings["att_l"] + home_ratings["att_c"] + home_ratings["att_r"]) / 3
    def_a = (away_ratings["def_l"] + away_ratings["def_c"] + away_ratings["def_r"]) / 3
    adv_h = att_h - def_a

    att_a = (away_ratings["att_l"] + away_ratings["att_c"] + away_ratings["att_r"]) / 3
    def_h = (home_ratings["def_l"] + home_ratings["def_c"] + home_ratings["def_r"]) / 3
    adv_a = att_a - def_h

    conv_h = max(0.15, min(0.75, 0.425 + 0.075 * adv_h))
    conv_a = max(0.15, min(0.75, 0.425 + 0.075 * adv_a))

    exp_goals_h = chances_home * conv_h
    exp_goals_a = chances_away * conv_a

    def poisson_pmf(k, lam):
        if lam == 0: return 1.0 if k == 0 else 0.0
        return math.exp(-lam) * (lam ** k) / math.factorial(k)

    p_win_h = p_draw = p_win_a = 0.0
    for h in range(11):
        for a in range(11):
            prob = poisson_pmf(h, exp_goals_h) * poisson_pmf(a, exp_goals_a)
            if h > a: p_win_h += prob
            elif h == a: p_draw += prob
            else: p_win_a += prob

    total = p_win_h + p_draw + p_win_a
    return round(p_win_h / total * 100, 1), round(p_draw / total * 100, 1), round(p_win_a / total * 100, 1)

# ================== ПАРСИНГ (без изменений) ==================
def parse_match(url: str):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    poss_text = soup.get_text()
    poss45 = poss90 = 50.0
    match = re.search(r"45'\D*(\d+)%", poss_text)
    if match: poss45 = float(match.group(1))
    match = re.search(r"90'\D*(\d+)%", poss_text)
    if match: poss90 = float(match.group(1))

    ratings = re.findall(r'(Wretched|Poor|Weak|Inadequate|Passable|Solid|Excellent|Formidable|Outstanding|Brilliant|Magnificent|World Class|Supernatural|Titanic|Extraterrestrial|Mythical|Utopian|Divine)\s*[-–]?\s*(very low|low|high|very high)?', poss_text, re.I)

    nums = []
    for lvl, sub in ratings[:20]:
        num = text_to_rating(f"{lvl} {sub or ''}")
        if num > 1: nums.append(num)

    if len(nums) < 14:
        raise ValueError("Не удалось распарсить рейтинги.")

    home = {"gk": nums[0], "def_l": nums[1], "def_c": nums[2], "def_r": nums[3],
            "mid": nums[4], "att_l": nums[5], "att_c": nums[6], "att_r": nums[7]}
    away = {"gk": nums[8], "def_l": nums[9], "def_c": nums[10], "def_r": nums[11],
            "mid": nums[12], "att_l": nums[13], "att_c": nums[14] if len(nums)>14 else nums[6],
            "att_r": nums[15] if len(nums)>15 else nums[7]}

    return home, away, poss45, poss90

# ================== КОМАНДЫ БОТА (без изменений) ==================
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("Отправь ссылку на матч Hattrick\nЯ посчитаю точные % на основе рейтингов 1-й и последней минуты + possession.")

@dp.message(F.text.regexp(r"hattrick\.org.*MatchID=\d+"))
async def process_match(message: Message):
    url = message.text.strip()
    await message.answer("⏳ Парсю матч...")

    try:
        home_r, away_r, p45, p90 = parse_match(url)
        win_h, draw, win_a = calculate_match_prob(home_r, away_r, p45, p90)

        text = (f"✅ Анализ завершён!\n\n"
                f"🏠 Победа хозяев: {win_h}%\n"
                f"🤝 Ничья: {draw}%\n"
                f"🏟️ Победа гостей: {win_a}%\n\n"
                f"Использовал: рейтинги (1-я минута → конец) + possession 45'/90'.")
        await message.answer(text)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

# ================== ЗАПУСК ==================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())        
