import asyncio
import logging
import os
import sys
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import Config
from bot.handlers import router
from bot.services.scheduler import SchedulerService
from bot.database import init_db

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

_LOCK_FILE = None


def _acquire_single_instance_lock():
    """Блокировка: только один экземпляр бота на этой машине."""
    global _LOCK_FILE
    lock_path = Path(__file__).resolve().parent / ".bot_instance.lock"
    try:
        _LOCK_FILE = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(_LOCK_FILE.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_LOCK_FILE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError) as e:
        logger.error(
            "Бот уже запущен! Закройте другой терминал/процесс с main.py и попробуйте снова."
        )
        sys.exit(1)


async def main():
    # Проверяем наличие токена
    if not Config.BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен в переменных окружения!")
        return
    
    # Инициализируем базу данных
    try:
        await init_db()
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка при инициализации БД: {e}")
        return
    
    # Создаем бота и диспетчер
    bot = Bot(token=Config.BOT_TOKEN)
    
    # Убираем webhook (если был) — иначе конфликт с polling
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook сброшен, запуск polling")
    except Exception as e:
        logger.warning(f"Не удалось сбросить webhook: {e}")
    
    dp = Dispatcher(storage=MemoryStorage())
    
    # Регистрируем роутеры
    dp.include_router(router)
    
    # Запускаем планировщик
    scheduler_service = SchedulerService(bot)
    scheduler_service.start()
    
    try:
        logger.info("Бот запущен!")
        # Запускаем бота
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        logger.error(f"Ошибка при работе бота: {e}")
    finally:
        scheduler_service.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    _acquire_single_instance_lock()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
