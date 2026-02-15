import asyncio
from sqlalchemy import text
from .database import engine, Base


def _add_visit_status_if_missing(conn):
    """Добавляет колонку visit_status, если её нет (миграция)"""
    try:
        result = conn.execute(text("PRAGMA table_info(appointments)"))
        columns = [row[1] for row in result.fetchall()]
        if "visit_status" not in columns:
            conn.execute(text("ALTER TABLE appointments ADD COLUMN visit_status VARCHAR"))
            conn.commit()
    except Exception:
        pass


async def init_db():
    """Инициализация базы данных - создание всех таблиц"""
    from bot.models.models import Company, User, Appointment, Notification  # noqa: F401 — регистрация в Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_visit_status_if_missing)
    print("База данных инициализирована!")


if __name__ == "__main__":
    asyncio.run(init_db())
