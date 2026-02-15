from datetime import datetime, timedelta
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from bot.models.models import Appointment, Notification, User, Company
from bot.database.database import get_session
from aiogram import Bot
from bot.config import Config


class NotificationService:
    def __init__(self, bot: Bot):
        self.bot = bot
    
    async def create_notification(self, appointment: Appointment, notification_type: str, send_at: datetime):
        """Создает уведомление в базе данных (если его еще нет)"""
        async with get_session() as session:
            # Проверяем, нет ли уже такого уведомления
            result = await session.execute(
                select(Notification).where(
                    and_(
                        Notification.appointment_id == appointment.id,
                        Notification.type == notification_type,
                        Notification.sent == False
                    )
                )
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                # Обновляем время отправки, если оно изменилось
                if existing.send_at != send_at:
                    existing.send_at = send_at
                    await session.commit()
                return
            
            notification = Notification(
                appointment_id=appointment.id,
                type=notification_type,
                send_at=send_at,
                sent=False
            )
            session.add(notification)
            await session.commit()
    
    def _should_skip_reminders(self, appointment: Appointment) -> bool:
        """Пропускать напоминания: визит завершён, отменён или удалён"""
        status = getattr(appointment, "visit_status", "") or ""
        s = status.lower()
        return (
            "завершен" in s or "завершён" in s
            or "отменена" in s or "отменено" in s
            or "удалена" in s
        )

    async def schedule_appointment_notifications(self, appointment: Appointment):
        """Планирует все уведомления для новой записи"""
        try:
            # 3. Уведомление об отмене/удалении — не требует парсинга даты
            if appointment.status == "canceled":
                await self.create_notification(
                    appointment, "canceled", datetime.now()
                )
                return
            
            skip_reminders = self._should_skip_reminders(appointment)

            # Парсим дату и время записи
            appointment_datetime = self._parse_appointment_datetime(appointment.date, appointment.time)
            
            if not appointment_datetime:
                return
            
            # 1. Уведомление о создании записи (сразу)
            if appointment.status == "created":
                await self.create_notification(
                    appointment, "created", datetime.now()
                )
            
            # 2. Уведомление об изменении записи (сразу, если статус changed)
            if appointment.status == "changed":
                await self.create_notification(
                    appointment, "changed", datetime.now()
                )
            
            # 4–6. Напоминания — только для активных записей (не завершён/отменён/удалён)
            if not skip_reminders:
                day_before_time = appointment_datetime - timedelta(days=1)
                if day_before_time > datetime.now():
                    await self.create_notification(
                        appointment, "day_before", day_before_time
                    )
                reminder_time = appointment_datetime - timedelta(hours=3)
                if reminder_time > datetime.now():
                    await self.create_notification(
                        appointment, "reminder", reminder_time
                    )
                confirmation_time = appointment_datetime - timedelta(days=14)
                if confirmation_time > datetime.now():
                    await self.create_notification(
                        appointment, "confirmation", confirmation_time
                    )
            
            # 7. Сообщение после визита (через несколько часов после времени записи)
            after_visit_time = appointment_datetime + timedelta(hours=2)
            await self.create_notification(
                appointment, "after_visit", after_visit_time
            )
            
        except Exception as e:
            print(f"Ошибка при планировании уведомлений: {e}")
    
    def _parse_appointment_datetime(self, date_str: str, time_str: str) -> datetime:
        """Парсит дату и время из строк в datetime"""
        try:
            # Пробуем разные форматы даты
            date_formats = [
                "%d.%m.%Y",
                "%d/%m/%Y",
                "%d.%m.%y",
                "%d/%m/%y",
                "%Y-%m-%d"
            ]
            
            parsed_date = None
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(date_str, fmt).date()
                    break
                except:
                    continue
            
            if not parsed_date:
                return None
            
            # Парсим время
            try:
                parsed_time = datetime.strptime(time_str, "%H:%M").time()
            except:
                return None
            
            return datetime.combine(parsed_date, parsed_time)
        except Exception as e:
            print(f"Ошибка при парсинге даты/времени: {e}")
            return None
    
    async def send_notification(self, notification: Notification):
        """Отправляет уведомление пользователю"""
        async with get_session() as session:
            try:
                # Перезагружаем уведомление из базы
                result = await session.execute(
                    select(Notification).where(Notification.id == notification.id)
                )
                notification = result.scalar_one_or_none()
                
                if not notification or notification.sent:
                    return
                
                # Получаем запись с связанными данными
                result = await session.execute(
                    select(Appointment, User, Company)
                    .join(User)
                    .join(Company)
                    .where(Appointment.id == notification.appointment_id)
                )
                row = result.first()
                
                if not row:
                    return
                
                appointment, user, company = row
                
                if not appointment or not user:
                    return
                # Не отправляем пользователям с telegram_id < 0 (ещё не зарегистрировались в боте)
                if user.telegram_id < 0:
                    notification.sent = True
                    await session.commit()
                    return

                # Формируем текст уведомления
                text = self._format_notification_text(
                    notification.type, appointment, company
                )
                
                # Отправляем сообщение
                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML"
                )
                
                # Помечаем уведомление как отправленное
                notification.sent = True
                await session.commit()
                
            except Exception as e:
                print(f"Ошибка при отправке уведомления: {e}")
    
    def _escape_html(self, s: str) -> str:
        """Экранирует HTML для parse_mode=HTML"""
        if not s:
            return ""
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _format_notification_text(self, notification_type: str, appointment: Appointment, company: Company) -> str:
        """Форматирует текст уведомления в зависимости от типа"""
        e = self._escape_html
        if notification_type == "created":
            return (
                f"✅ <b>Вы записаны!</b>\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(company.address)}\n\n"
                f"🔗 Посмотреть запись: {appointment.clientlink}\n\n"
                f"✨ Салон «MeowNoMeow»"
            )
        
        elif notification_type == "changed":
            return (
                f"❌ <b>Запись отменена или удалена</b>\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"📍 Адрес: {e(company.address)}\n\n"
                f"💬 Для новой записи свяжитесь с салоном «MeowNoMeow»"
            )
        
        elif notification_type == "canceled":
            return (
                f"❌ <b>Запись отменена или удалена</b>\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"📍 Адрес: {e(company.address)}\n\n"
                f"💬 Для новой записи свяжитесь с салоном «MeowNoMeow»"
            )
        
        elif notification_type == "day_before":
            return (
                f"📅 <b>Напоминание</b>\n\n"
                f"Завтра вас ждут в салоне!\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(company.address)}\n\n"
                f"✨ Салон «MeowNoMeow»"
            )
        
        elif notification_type == "reminder":
            return (
                f"⏰ <b>Напоминание</b>\n\n"
                f"Сегодня у вас запись:\n\n"
                f"🎯 {e(appointment.event)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(company.address)}\n\n"
                f"Ждём вас! ✨"
            )
        
        elif notification_type == "confirmation":
            return (
                f"📅 <b>Подтверждение записи</b>\n\n"
                f"Вы записаны на:\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(company.address)}\n\n"
                f"🔗 Подтвердите запись: {appointment.clientlink}\n\n"
                f"✨ Салон «MeowNoMeow»"
            )
        
        elif notification_type == "after_visit":
            return (
                f"🙏 <b>Спасибо за посещение!</b>\n\n"
                f"Будем рады видеть вас снова ✨\n\n"
                f"📝 <b>Оставьте отзыв</b> — нам будет приятно:\n\n"
                f"🗺 Яндекс.Карты:\n{Config.YANDEX_REVIEW_URL}\n\n"
                f"🗺 2GIS:\nhttps://2gis.ru/tomsk/reviews/70000001087746231/addReview?utm_source=lk\n\n"
                f"💙 ВКонтакте:\n{Config.VK_GROUP_URL}?w=app6326142_-224655267\n\n"
                f"📱 Dikidi:\nhttps://dikidi.net/1993359?p=0.pi\n\n"
                f"☕ Поддержать мастера чаевыми:\n{Config.VK_GROUP_URL}?w=app6326142_-224655267\n\n"
                f"✨ Салон «MeowNoMeow»"
            )
        
        return ""
    
    async def process_pending_notifications(self):
        """Обрабатывает все ожидающие уведомления"""
        async with get_session() as session:
            try:
                # Получаем все неотправленные уведомления, время которых наступило
                result = await session.execute(
                    select(Notification).where(
                        and_(
                            Notification.sent == False,
                            Notification.send_at <= datetime.now()
                        )
                    )
                )
                notifications = result.scalars().all()
                
                for notification in notifications:
                    await self.send_notification(notification)
                    
            except Exception as e:
                print(f"Ошибка при обработке уведомлений: {e}")
