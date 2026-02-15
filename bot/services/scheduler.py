from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession
from bot.database.database import get_session
from bot.services.dikidi_parser import DikidiParser
from bot.services.notifications import NotificationService
from bot.models.models import Appointment
from sqlalchemy import select
from aiogram import Bot
import logging

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, bot: Bot):
        self.scheduler = AsyncIOScheduler()
        self.bot = bot
        self.parser = DikidiParser()
        self.notification_service = NotificationService(bot)
    
    async def sync_and_schedule(self):
        """Синхронизирует записи с Dikidi и планирует уведомления"""
        async with get_session() as session:
            try:
                # Синхронизируем записи
                stats = await self.parser.sync_appointments(session)
                logger.info(f"Синхронизация с Dikidi: создано {stats['created']}, изменено {stats['changed']}, отменено {stats['canceled']}")
                
                # Получаем новые, изменённые и отменённые записи
                result = await session.execute(
                    select(Appointment).where(
                        Appointment.status.in_(["created", "changed", "canceled"])
                    )
                )
                appointments = result.scalars().all()
                
                # Планируем уведомления для каждой записи
                for appointment in appointments:
                    await self.notification_service.schedule_appointment_notifications(appointment)
                    # Сбрасываем статус (кроме canceled — отмена остаётся)
                    if appointment.status in ("created", "changed"):
                        appointment.status = "active"
                if appointments:
                    await session.commit()
                
            except Exception as e:
                logger.error(f"Ошибка при синхронизации с Dikidi: {e}", exc_info=True)
    
    async def process_notifications(self):
        """Обрабатывает ожидающие уведомления"""
        await self.notification_service.process_pending_notifications()
    
    def start(self):
        """Запускает планировщик"""
        # Синхронизация каждые 10 минут (первый запуск — сразу при старте)
        self.scheduler.add_job(
            self.sync_and_schedule,
            IntervalTrigger(minutes=10),
            id="sync_appointments",
            replace_existing=True,
            next_run_time=datetime.now(),  # Запуск сразу, не ждать 15 минут
            misfire_grace_time=300,
        )
        
        # Обработка уведомлений каждую минуту
        self.scheduler.add_job(
            self.process_notifications,
            IntervalTrigger(minutes=1),
            id="process_notifications",
            replace_existing=True,
            next_run_time=datetime.now(),
        )
        
        self.scheduler.start()
        logger.info("Планировщик запущен! Первая синхронизация с Dikidi запущена сразу.")
    
    def shutdown(self):
        """Останавливает планировщик"""
        self.scheduler.shutdown(wait=False)
