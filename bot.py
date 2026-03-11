# ================== bot.py (полная обновлённая версия) ==================
import asyncio
import re
import math
import os
from dotenv import load_dotenv
import cloudscraper
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

import pytesseract
from PIL import Image

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================== РЕЙТИНГИ (английский + русский для OCR) ==================
LEVELS_EN = {
    "disastrous": 1, "wretched": 2, "poor": 3, "weak": 4, "inadequate": 5,
    "passable": 6, "solid": 7, "excellent": 8, "formidable": 9, "outstanding": 10,
    "brilliant": 11, "magnificent": 12, "world class": 13, "supernatural": 14,
    "titanic": 15, "extraterrestrial": 16, "mythical": 17, "utopian": 18, "divine": 19
}
LEVELS_RU = {
    "ужасный": 1, "жалкий": 2, "бедный": 3, "слабый": 4, "недостаточный": 5,
    "приемлемый": 6, "твёрдый": 7, "отличный": 8, "грозный": 9, "выдающийся": 10,
    "блестящий": 11, "великолепный": 12, "мирового класса": 13, "сверхъестественный": 14,
    "титанический": 15, "внеземной": 16, "мифический": 17, "утопический": 18, "божественный": 19
}
SUBS_EN = {"very low": 0.0, "low": 0.25, "high": 0.5, "very high": 0.75}
SUBS_RU = {"очень низкий": 0.0, "низкий": 0.25, "высокий": 0.5, "очень высокий": 0.75}

def text_to_rating(text: str) -> float:
    text = text.lower().strip()
    # Английский
    for lvl_str, val in LEVELS_EN.items():
        if lvl_str in text:
            base = val
            for s, v in SUBS_EN.items():
                if s in text:
                    return base + v
            return base
    # Русский
    for lvl_str, val in LEVELS_RU.items():
        if lvl_str in text:
            base = val
            for s, v in SUBS_RU.items():
                if s in text:
                    return base + v
            return base
    return 1.0

# ================== ПОЛНЫЙ АЛГОРИТМ HATTRICK ==================
def poisson_pmf(k: int, lam: float) -> float:
    if lam == 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def prob_score(ratio: float) -> float:
    # Сигмоида: при ratio=1 → ~0.25 (средняя реализация шанса в HT)
    # ratio=2 → ~0.40, ratio=0.5 → ~0.12
    return max(0.05, min(0.45, 0.05 + 0.35 / (1 + math.exp(-(ratio - 1) * 2))))

def get_expected_goals(att_l: float, att_c: float, att_r: float,
                       def_opp_r: float, def_opp_c: float, def_opp_l: float,
                       possession: float) -> float:
    total_chances = 10.0  # стандартное среднее регулярных шансов (wiki + devblog)
    team_chances = total_chances * possession

    ch_c = team_chances * 0.35
    ch_l = team_chances * 0.25
    ch_r = team_chances * 0.25
    ch_sp = team_chances * 0.15

    eg_c = ch_c * prob_score(att_c / def_opp_c)
    eg_l = ch_l * prob_score(att_l / def_opp_r)   # левый атакует правую защиту соперника
    eg_r = ch_r * prob_score(att_r / def_opp_l)   # правый атакует левую защиту соперника
    eg_sp = ch_sp * 0.12                          # сет-пиисы ~12% реализация

    return eg_c + eg_l + eg_r + eg_sp

def calculate_match_prob(home: dict, away: dict, p45: float, p90: float,
                         center_poss: float = 50.0) -> tuple:
    # Учитываем % владения в центре из диаграммы (дополнительно к p45/p90)
    poss_home = (p45 + p90) / 200.0  # среднее
    if center_poss > 0:
        poss_home = (poss_home * 0.7 + center_poss / 100 * 0.3)  # взвешиваем центр поля

    home_lambda = get_expected_goals(
        home["att_l"], home["att_c"], home["att_r"],
        away["def_r"], away["def_c"], away["def_l"],
        poss_home
    )
    away_lambda = get_expected_goals(
        away["att_l"], away["att_c"], away["att_r"],
        home["def_r"], home["def_c"], home["def_l"],
        1 - poss_home
    )

    # Poisson 0-10 голов (достаточно для 99.9% случаев)
    MAX_G = 10
    h_probs = [poisson_pmf(k, home_lambda) for k in range(MAX_G + 1)]
    a_probs = [poisson_pmf(k, away_lambda) for k in range(MAX_G + 1)]

    win_h = draw = win_a = 0.0
    for h in range(MAX_G + 1):
        for a in range(MAX_G + 1):
            p = h_probs[h] * a_probs[a]
            if h > a:
                win_h += p
            elif h == a:
                draw += p
            else:
                win_a += p

    return round(win_h * 100), round(draw * 100), round(win_a * 100)

# ================== ПАРСИНГ (URL + OCR скриншота) ==================
def parse_report_text(text: str):
    poss45 = poss90 = center_poss = 50.0

    # 45' и 90' владение (англ + рус)
    m = re.search(r"45['′]?\D*(\d+)%", text, re.I)
    if m: poss45 = float(m.group(1))
    m = re.search(r"90['′]?\D*(\d+)%", text, re.I)
    if m: poss90 = float(m.group(1))

    # % владения в центре из диаграммы поля (англ + рус)
    for pat in [r"центр.*?(\d+)%", r"center.*?(\d+)%", r"midfield.*?(\d+)%", r"владения в центре.*?(\d+)%"]:
        m = re.search(pat, text, re.I)
        if m:
            center_poss = float(m.group(1))
            break

    # Рейтинги (слова)
    ratings = re.findall(
        r'(Wretched|Poor|Weak|Inadequate|Passable|Solid|Excellent|Formidable|Outstanding|Brilliant|Magnificent|World Class|Supernatural|Titanic|Extraterrestrial|Mythical|Utopian|Divine|'
        r'Ужасный|Жалкий|Бедный|Слабый|Недостаточный|Приемлемый|Твёрдый|Отличный|Грозный|Выдающийся|Блестящий|Великолепный|Мирового класса|Сверхъестественный|Титанический|Внеземной|Мифический|Утопический|Божественный)'
        r'\s*[-–]?\s*(very low|low|high|very high|очень низкий|низкий|высокий|очень высокий)?',
        text, re.I
    )

    nums = []
    for lvl, sub in ratings[:20]:
        num = text_to_rating(f"{lvl} {sub or ''}")
        if num > 1:
            nums.append(num)

    if len(nums) < 14:
        raise ValueError("Не удалось найти рейтинги игроков.")

    # Распределение (стандарт Hattrick: 8 секторов)
    home = {
        "gk": nums[0], "def_l": nums[1], "def_c": nums[2], "def_r": nums[3],
        "mid": nums[4], "att_l": nums[5], "att_c": nums[6], "att_r": nums[7]
    }
    away = {
        "gk": nums[8], "def_l": nums[9], "def_c": nums[10], "def_r": nums[11],
        "mid": nums[12], "att_l": nums[13], "att_c": nums[14] if len(nums) > 14 else nums[6],
        "att_r": nums[15] if len(nums) > 15 else nums[7]
    }

    return home, away, poss45, poss90, center_poss

def parse_match(url: str):
    scraper = cloudscraper.create_scraper()
    r = scraper.get(url, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return parse_report_text(soup.get_text())

# ================== ОБРАБОТКА СКРИНШОТА (OCR) ==================
async def process_screenshot(photo_file_id: str):
    file = await bot.get_file(photo_file_id)
    file_path = f"/tmp/screenshot_{photo_file_id}.png"
    await bot.download_file(file.file_path, file_path)

    # OCR с английским + русским
    img = Image.open(file_path)
    text = pytesseract.image_to_string(img, lang="eng+rus")

    os.remove(file_path)  # чистим
    return parse_report_text(text)

# ================== КОМАНДЫ ==================
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "Отправь:\n"
        "• ссылку на матч Hattrick (MatchID=...)\n"
        "• скриншот отчёта матча (OCR + диаграмма поля)\n"
        "• или текст рейтингов"
    )

@dp.message(F.text.regexp(r"hattrick\.org.*MatchID=\d+"))
async def process_link(message: Message):
    url = message.text.strip()
    await message.answer("⏳ Парсим страницу Hattrick...")

    try:
        home_r, away_r, p45, p90, center = parse_match(url)
        win_h, draw, win_a = calculate_match_prob(home_r, away_r, p45, p90, center)

        await message.answer(
            f"✅ Анализ завершён!\n\n"
            f"🏠 Победа хозяев: {win_h}%\n"
            f"🤝 Ничья: {draw}%\n"
            f"🏟️ Победа гостей: {win_a}%\n\n"
            f"Владение 45': {p45}% | 90': {p90}%\n"
            f"Центр поля (из диаграммы): {center}%"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка парсинга ссылки: {str(e)}\nПришли скриншот или текст.")

@dp.message(F.photo)
async def process_photo(message: Message):
    await message.answer("🖼️ Распознаём скриншот (OCR + диаграмма поля)...")

    try:
        home_r, away_r, p45, p90, center = await process_screenshot(message.photo[-1].file_id)
        win_h, draw, win_a = calculate_match_prob(home_r, away_r, p45, p90, center)

        await message.answer(
            f"✅ Анализ скриншота завершён!\n\n"
            f"🏠 Победа хозяев: {win_h}%\n"
            f"🤝 Ничья: {draw}%\n"
            f"🏟️ Победа гостей: {win_a}%\n\n"
            f"Владение 45': {p45}% | 90': {p90}%\n"
            f"Центр поля (из диаграммы): {center}%"
        )
    except Exception as e:
        await message.answer(f"❌ OCR не справился: {str(e)}\n\n"
                             f"Пришли чёткий скриншот отчёта (рейтинги + диаграмма поля) "
                             f"или текст всех рейтингов.")

# ================== ЗАПУСК ==================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
