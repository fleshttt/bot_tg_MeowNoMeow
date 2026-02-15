# MeowNoMeow — Telegram-бот с парсером Dikidi
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Браузеры уже в образе — не скачиваем повторно
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY main.py .
COPY init_db.py .

# Директория для SQLite (монтируется volume)
RUN mkdir -p /app/data

CMD ["python", "main.py"]
