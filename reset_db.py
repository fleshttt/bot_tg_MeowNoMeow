"""Сброс базы данных — удаление файла и пересоздание таблиц.
Запуск: python reset_db.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot.config import Config
from bot.database.database_init import init_db

def get_db_path():
    """Путь к файлу SQLite из DATABASE_URL"""
    url = Config.DATABASE_URL or ""
    if "sqlite" in url:
        path = url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        return path
    return None


async def reset():
    path = get_db_path()
    if path and os.path.exists(path):
        os.remove(path)
        print(f"База удалена: {path}")
    elif path:
        print(f"Файл БД не найден: {path}")
    await init_db()
    print("База пересоздана (пустая)")


if __name__ == "__main__":
    asyncio.run(reset())
