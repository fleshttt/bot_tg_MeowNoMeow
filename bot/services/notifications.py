from datetime import datetime, timedelta
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from bot.models.models import Appointment, Notification, User, Company
from bot.database.database import get_session
from aiogram import Bot
from bot.config import Config


class NotificationService:
    def __init__(self, bot: Bot):
        self.bot = bot
    
    async def create_notification(self, appointment: Appointment, notification_type: str, send_at: datetime, once_only: bool = False):
        """
        Создает уведомление в базе данных (если его еще нет).
        once_only: для canceled/created/changed — не создавать, если уже было когда-либо.
        """
        async with get_session() as session:
            # Для «разовых» типов — не дублировать, если уже было
            if once_only:
                r = await session.execute(
                    select(Notification).where(
                        and_(
                            Notification.appointment_id == appointment.id,
                            Notification.type == notification_type
                        )
                    )
                )
                if r.scalars().first():
                    return
            # Проверяем, нет ли уже ожидающего уведомления
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
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return  # Дубликат — уникальный индекс (appointment_id, type)
    
    def _should_skip_reminders(self, appointment: Appointment) -> bool:
        """Пропускать напоминания: визит завершён, отменён или удалён"""
        status = getattr(appointment, "visit_status", "") or ""
        s = status.lower()
        return (
            "завершен" in s or "завершён" in s
            or "отменена" in s or "отменено" in s
            or "удалена" in s
        )

    def _is_visit_completed(self, appointment: Appointment) -> bool:
        """Визит завершён по visit_status"""
        status = getattr(appointment, "visit_status", "") or ""
        s = status.lower()
        return "завершен" in s or "завершён" in s

    async def schedule_appointment_notifications(self, appointment: Appointment):
        """Планирует уведомления. Не уведомляет о старых записях (дата уже прошла)."""
        try:
            # 1. Отменена — только уведомление об отмене
            if appointment.status == "canceled":
                await self.create_notification(
                    appointment, "canceled", datetime.now(), once_only=True
                )
                return

            appointment_datetime = self._parse_appointment_datetime(appointment.date, appointment.time)
            if not appointment_datetime:
                return

            now = datetime.now()
            is_past = appointment_datetime < now

            # 2. Новая запись — только если дата в будущем (не старые записи)
            if appointment.status == "created":
                if not is_past:
                    await self.create_notification(
                        appointment, "created", datetime.now(), once_only=True
                    )
                return

            # 3. Визит завершён (changed) — after_visit (2ч после, только для недавних) + rebook_14 (через 14 дней)
            # Не уведомляем о старых записях — только если визит был не более 7 дней назад
            if appointment.status == "changed" and self._is_visit_completed(appointment):
                days_since_visit = (now - appointment_datetime).days
                # after_visit — только для недавних визитов, один раз
                if days_since_visit <= 7:
                    after_visit_time = max(
                        appointment_datetime + timedelta(hours=2),
                        now
                    )
                    await self.create_notification(
                        appointment, "after_visit", after_visit_time, once_only=True
                    )
                # rebook_14 — через 14 дней после визита, напоминание записаться снова
                rebook_time = appointment_datetime + timedelta(days=14)
                if rebook_time > now:
                    await self.create_notification(
                        appointment, "rebook_14", rebook_time, once_only=True
                    )
                return

            # 4. Активные записи (не завершённые) — напоминания, только если дата в будущем
            if is_past:
                return

            if self._should_skip_reminders(appointment):
                return

            day_before_time = appointment_datetime - timedelta(days=1)
            if day_before_time > now:
                await self.create_notification(
                    appointment, "day_before", day_before_time
                )
            reminder_time = appointment_datetime - timedelta(hours=3)
            if reminder_time > now:
                await self.create_notification(
                    appointment, "reminder", reminder_time
                )
            confirmation_time = appointment_datetime - timedelta(days=14)
            if confirmation_time > now:
                await self.create_notification(
                    appointment, "confirmation", confirmation_time
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
                # Не отправляем пользователям с telegram_id < 0
                if user.telegram_id < 0:
                    notification.sent = True
                    await session.commit()
                    return

                # Не отправлять, если запись отменена
                app_status = getattr(appointment, "status", "") or ""
                if app_status == "canceled" and notification.type in ("after_visit", "rebook_14", "day_before", "reminder", "confirmation"):
                    notification.sent = True
                    await session.commit()
                    return
                # Напоминания (day_before, reminder, confirmation) — пропускать для завершённых/отменённых
                if notification.type in ("day_before", "reminder", "confirmation"):
                    if self._should_skip_reminders(appointment):
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
        address = Config.COMPANY_ADDRESS or (company.address if company else "")
        if notification_type == "created":
            return (
                f"✅ <b>Вы записаны!</b>\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(address)}\n\n"
                f"🔗 Посмотреть запись: {appointment.clientlink}\n\n"
                f"✨ Салон «{e(company.name)}»"
            )
        
        elif notification_type == "changed":
            return (
                f"⚠️ <b>Ваша запись изменена</b>\n\n"
                f"Новые данные:\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(address)}\n\n"
                f"🔗 Посмотреть запись: {appointment.clientlink}\n\n"
                f"✨ Салон «{e(company.name)}»"
            )
        
        elif notification_type == "canceled":
            return (
                f"❌ <b>Запись отменена или удалена</b>\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"📍 Адрес: {e(address)}\n\n"
                f"💬 Для новой записи свяжитесь с салоном «{e(company.name)}»"
            )
        
        elif notification_type == "day_before":
            return (
                f"📅 <b>Напоминание</b>\n\n"
                f"Завтра вас ждут в салоне!\n\n"
                f"🎯 Услуга: {e(appointment.event)}\n"
                f"📅 Дата: {e(appointment.date)}\n"
                f"⏰ Время: {e(appointment.time)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(address)}\n\n"
                f"✨ Салон «{e(company.name)}»"
            )
        
        elif notification_type == "reminder":
            return (
                f"⏰ <b>Напоминание</b>\n\n"
                f"Сегодня у вас запись:\n\n"
                f"🎯 {e(appointment.event)}\n"
                f"👤 Мастер: {e(appointment.master)}\n"
                f"📍 Адрес: {e(address)}\n\n"
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
                f"📍 Адрес: {e(address)}\n\n"
                f"🔗 Подтвердите запись: {appointment.clientlink}\n\n"
                f"✨ Салон «{e(company.name)}»"
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
                f"✨ Салон «{e(company.name)}»"
            )

        elif notification_type == "rebook_14":
            return (
                f"📅 <b>Время записаться снова!</b>\n\n"
                f"Прошло уже 2 недели с вашего последнего визита.\n\n"
                f"Ждём вас в салоне «{e(company.name)}» ✨\n\n"
                f"🔗 Записаться: {Config.BOOKING_URL}"
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
