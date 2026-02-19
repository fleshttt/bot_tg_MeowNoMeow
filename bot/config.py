import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Салон / компания
    COMPANY_NAME = os.getenv("COMPANY_NAME", "MeowNoMeow")
    COMPANY_ADDRESS = os.getenv("COMPANY_ADDRESS", "г.Томск, ул.Фрунзе 11Б")
    
    # Ссылки
    BOOKING_URL = os.getenv("BOOKING_URL", "https://dikidi.net/1993359")
    YANDEX_REVIEW_URL = os.getenv("YANDEX_REVIEW_URL", "https://yandex.ru/maps/org/meownomeow/229631800295/")
    VK_GROUP_URL = os.getenv("VK_GROUP_URL", "https://vk.ru/meownomeow_tsk")
    
    # Telegram Bot
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    
    # Database: DATABASE_URL из env, иначе USE_SQLITE=1 → SQLite в папке TEMP (надёжная запись)
    _use_sqlite = os.getenv("USE_SQLITE", "").lower() in ("1", "true", "yes")
    _db_path = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "meownomeow.db")
    if os.name == "nt":
        _db_path = _db_path.replace("\\", "/")
    DATABASE_URL = os.getenv("DATABASE_URL") or (
        f"sqlite+aiosqlite:///{_db_path}" if _use_sqlite else f"sqlite+aiosqlite:///{_db_path}"
    )
    
    # Dikidi
    DIKIDI_COMPANY_ID = os.getenv("DIKIDI_COMPANY_ID", "1993359")
    DIKIDI_JOURNAL_URL = os.getenv(
        "DIKIDI_JOURNAL_URL",
        "https://dikidi.ru/ru/owner/journal/?company=1993359"
    )
    # Журнал в виде списка за неделю (start/end подставляются парсером)
    DIKIDI_JOURNAL_LIST_BASE = (
        "https://dikidi.ru/ru/owner/journal/?company=1993359&view=list&limit=50&period=week"
    )
    # Неделя для списка (если заданы — используются вместо текущей недели)
    DIKIDI_JOURNAL_START = os.getenv("DIKIDI_JOURNAL_START", "")  # например 2026-02-09
    DIKIDI_JOURNAL_END = os.getenv("DIKIDI_JOURNAL_END", "")    # например 2026-02-15
    DIKIDI_LOGIN_PHONE = os.getenv("DIKIDI_LOGIN_PHONE", "89526834874")
    DIKIDI_LOGIN_PASSWORD = os.getenv("DIKIDI_LOGIN_PASSWORD", "281076zxc")
    
    # Admin
    ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
