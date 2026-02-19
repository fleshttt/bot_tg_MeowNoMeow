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


def _update_company_name_to_meownomeow(conn):
    """Обновляет название компании Meow → MeowNoMeow"""
    try:
        conn.execute(text("UPDATE companies SET name = 'MeowNoMeow' WHERE name = 'Meow'"))
        conn.commit()
    except Exception:
        pass


def _update_company_address_full(conn):
    """Обновляет адрес компании на полный из Config.COMPANY_ADDRESS (адрес один везде)"""
    try:
        from bot.config import Config
        full_addr = Config.COMPANY_ADDRESS
        conn.execute(text("UPDATE companies SET address = :addr"), {"addr": full_addr})
        conn.commit()
    except Exception:
        pass


def _add_notification_unique_constraint(conn):
    """Добавляет UNIQUE(appointment_id, type) для предотвращения дублей уведомлений"""
    try:
        result = conn.execute(text("PRAGMA index_list(notifications)"))
        indexes = [row[1] for row in result.fetchall()]
        if "uq_notification_appointment_type" not in indexes:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_appointment_type "
                "ON notifications(appointment_id, type)"
            ))
            conn.commit()
    except Exception:
        pass


async def init_db():
    """Инициализация базы данных - создание всех таблиц"""
    from bot.models.models import Company, User, Appointment, Notification  # noqa: F401 — регистрация в Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_visit_status_if_missing)
        await conn.run_sync(_update_company_name_to_meownomeow)
        await conn.run_sync(_update_company_address_full)
        await conn.run_sync(_add_notification_unique_constraint)
    print("База данных инициализирована!")


if __name__ == "__main__":
    asyncio.run(init_db())
