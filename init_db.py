"""
Скрипт для инициализации базы данных
Запуск: python init_db.py
"""
import asyncio
from bot.database import init_db


if __name__ == "__main__":
    asyncio.run(init_db())
