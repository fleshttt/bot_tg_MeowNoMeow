"""
Скрипт проверки: загружает ли парсер данные из Dikidi в базу данных.
Запуск: python check_parser_db.py

1. Парсит Dikidi (без БД) — сколько записей получено
2. Показывает пользователей в БД и их телефоны
3. Выполняет синхронизацию
4. Выводит результат
"""
import asyncio
import sys
import os

if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select, func
from bot.database.database import get_session
from bot.database.database_init import init_db
from bot.models.models import User, Appointment, Company
from bot.services.dikidi_parser import DikidiParser


def norm_phone(p: str) -> str:
    """Нормализация телефона для сравнения"""
    if not p:
        return ""
    p = p.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if p.startswith("8"):
        p = "+7" + p[1:]
    elif p and not p.startswith("+"):
        p = "+7" + p
    return p


async def check():
    print("=" * 60)
    print("  ДИАГНОСТИКА ПАРСЕРА И БАЗЫ ДАННЫХ")
    print("=" * 60)

    await init_db()

    # 1. Пользователи в БД
    async with get_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        users_with_phone = [u for u in users if u.phone and u.phone.strip()]

        print(f"\n📋 Пользователи в БД: {len(users)} (с телефоном: {len(users_with_phone)})")
        for u in users_with_phone[:10]:
            norm = norm_phone(u.phone)
            print(f"   telegram_id={u.telegram_id}  phone={u.phone!r}  → norm={norm!r}")
        if len(users_with_phone) > 10:
            print(f"   ... и ещё {len(users_with_phone) - 10}")

        result = await session.execute(select(func.count(Appointment.id)))
        count_before = result.scalar()

    # 2. Парсер — сколько записей получает с Dikidi
    print("\n⏳ Запуск парсера Dikidi (логин, переход, парсинг)...")
    parser = DikidiParser()
    async with get_session() as parse_session:
        parsed = await parser.parse_appointments(parse_session)
    print(f"\n📥 Парсер получил с сайта: {len(parsed)} записей")

    if parsed:
        print("\n   Примеры распарсенных телефонов:")
        seen_phones = set()
        for i, app in enumerate(parsed[:10]):
            ph = app.get("phone", "")
            norm = norm_phone(ph)
            if norm and norm not in seen_phones:
                seen_phones.add(norm)
                st = app.get('visit_status', '') or '—'
                print(f"   {i+1}. {ph!r}  → norm={norm!r}  ({app.get('date')} {app.get('time')}) состояние: {st}")
    else:
        print("\n   ⚠ Парсер вернул 0 записей! Возможные причины:")
        print("      - Ошибка авторизации на Dikidi")
        print("      - Нет записей в выбранном периоде")
        print("      - Изменились селекторы на сайте")
        print("   Запустите: python test_parser.py — для отладки с браузером")

    # 3. Проверка сопоставления
    if parsed and users_with_phone:
        users_norm = {norm_phone(u.phone): u for u in users_with_phone}
        matched = 0
        for app in parsed:
            ph = norm_phone(app.get("phone", ""))
            if ph and ph in users_norm:
                matched += 1
        print(f"\n🔗 Сопоставление: {matched} из {len(parsed)} записей Dikidi совпадают с пользователями бота")
        if matched == 0:
            print("   ⚠ Ни одна запись не совпадает! Проверьте формат телефонов.")

    # 4. Синхронизация
    print("\n⏳ Синхронизация с БД...")
    async with get_session() as session:
        stats = await parser.sync_appointments(session)

        print(f"\n📥 Результат синхронизации:")
        print(f"   Создано: {stats['created']}")
        print(f"   Изменено: {stats['changed']}")
        print(f"   Отменено: {stats['canceled']}")

        result = await session.execute(
            select(Appointment).where(Appointment.status != "canceled").order_by(Appointment.date, Appointment.time)
        )
        appointments = result.scalars().all()
        print(f"\n📊 Активных записей в БД: {len(appointments)}")

        if appointments:
            print("\n📋 Записи в БД:")
            for i, app in enumerate(appointments[:8], 1):
                st = getattr(app, 'visit_status', None) or '—'
                print(f"   {i}. {app.event} | {app.date} {app.time} | {app.master} | состояние: {st} | user_id={app.user_id}")
        else:
            print("\n⚠ Записей в БД нет.")
            if len(parsed) > 0 and len(users_with_phone) == 0:
                print("   Телефоны из Dikidi не сопоставлены — в боте нет пользователей с номерами.")
                print("   Решение: пользователь должен нажать /start и отправить свой номер.")
            elif len(parsed) > 0:
                print("   Записи сохраняются только для пользователей, зарегистрированных в боте (/start + номер).")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(check())
