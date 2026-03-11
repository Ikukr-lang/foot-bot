# ================== Dockerfile ==================
# Сборка: docker build -t hattrick-bot .
# Запуск: docker run -d --name hattrick-bot -v ./.env:/app/.env hattrick-bot

FROM python:3.12-slim

# Устанавливаем Tesseract OCR + русско-английские языки
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY bot.py .

# Точка входа
CMD ["python", "bot.py"]
