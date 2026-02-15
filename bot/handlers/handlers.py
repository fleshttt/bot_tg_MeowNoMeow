from aiogram import Router, F
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, Contact, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
import time

from bot.config import Config

RECORDING_URL = "https://dikidi.net/1993359"

# Ограничение кнопок: не более 2 нажатий в минуту на одну кнопку
_BUTTON_LIMIT = 2
_BUTTON_WINDOW = 60  # секунд
_button_presses: dict[tuple[int, str], list[float]] = {}


def _check_button_rate_limit(user_id: int, button_key: str) -> bool:
    """Возвращает True если нажатие разрешено, False если превышен лимит (2 нажатия в минуту)."""
    key = (user_id, button_key)
    now = time.monotonic()
    if key not in _button_presses:
        _button_presses[key] = []
    cutoff = now - _BUTTON_WINDOW
    _button_presses[key] = [t for t in _button_presses[key] if t > cutoff]
    if len(_button_presses[key]) >= _BUTTON_LIMIT:
        return False
    _button_presses[key].append(now)
    return True
from sqlalchemy import select
from bot.models.models import User, Appointment
from bot.database.database import get_session
import re

router = Router()

# Клавиатура для незарегистрированных
KEYBOARD_REGISTER = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📞 Отправить номер", request_contact=True)]],
    resize_keyboard=True,
)

# Клавиатура для зарегистрированных (заменяет «Отправить номер»)
KEYBOARD_LOGGED_IN = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Записаться"), KeyboardButton(text="📋 Мои записи")],
    ],
    resize_keyboard=True,
)


def normalize_phone(phone: str) -> str:
    """Нормализует номер телефона к формату +7XXXXXXXXXX"""
    # Убираем все символы кроме цифр
    digits = re.sub(r'\D', '', phone)
    
    # Если начинается с 8, заменяем на +7
    if digits.startswith('8'):
        digits = '7' + digits[1:]
    
    # Если не начинается с 7, добавляем 7
    if not digits.startswith('7'):
        digits = '7' + digits
    
    return '+' + digits


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start — сохраняет пользователя при первом заходе"""
    if not message.from_user:
        return
    async with get_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalars().first()
        if not user:
            # Сохраняем нового пользователя при первом заходе (phone="" до отправки контакта)
            user = User(telegram_id=message.from_user.id, phone="")
            session.add(user)
            await session.commit()
            await session.refresh(user)
        if user.phone and user.phone.strip():
            await message.answer(
                "👋 Вы уже зарегистрированы!\n\n"
                "Используйте кнопки меню или команды.",
                reply_markup=KEYBOARD_LOGGED_IN
            )
            # Сразу показываем записи пользователя из БД
            result = await session.execute(
                select(Appointment)
                .where(Appointment.user_id == user.id)
                .where(Appointment.status != "canceled")
                .order_by(Appointment.date, Appointment.time)
            )
            appointments = result.scalars().all()
            if appointments:
                text = "📅 Ваши записи:\n\n"
                for app in appointments:
                    status_line = f"📌 {app.visit_status}\n" if getattr(app, "visit_status", None) else ""
                    text += (
                        f"🎯 {app.event}\n"
                        f"📅 Дата: {app.date}\n"
                        f"⏰ Время: {app.time}\n"
                        f"👤 Мастер: {app.master}\n"
                        f"{status_line}"
                        f"📍 Адрес: г.Томск, ул. Фрунзе, 11Б\n"
                        f"🔗 {app.clientlink}\n\n"
                    )
                await message.answer(text)
            else:
                await message.answer("📅 У вас пока нет активных записей. Нажмите «Записаться» или «Мои записи» для просмотра.")
        else:
            await message.answer(
                f"👋 Здравствуйте! Я бот для уведомлений о записях в салоне {Config.COMPANY_NAME}.\n\n"
                "Для регистрации нажмите кнопку ниже или введите номер (например 89526834874):",
                reply_markup=KEYBOARD_REGISTER
            )


def _extract_phone_from_message(message: Message) -> str | None:
    """Извлекает телефон из Contact или из текста сообщения."""
    if message.contact:
        return normalize_phone(message.contact.phone_number)
    if message.text:
        digits = re.sub(r'\D', '', message.text)
        if 10 <= len(digits) <= 11:
            return normalize_phone(message.text)
    return None


@router.message(F.contact)
async def handle_contact(message: Message):
    """Обработчик: кнопка «Отправить номер» (Contact)"""
    phone = _extract_phone_from_message(message)
    if phone:
        await _register_phone(message, phone)


@router.message(F.text, F.text.regexp(r'^[\d\s\+\-\(\)]{10,18}$'))
async def handle_phone_text(message: Message):
    """Обработчик: пользователь ввёл номер текстом (например 89526834874)"""
    phone = _extract_phone_from_message(message)
    if phone and len(re.sub(r'\D', '', phone)) >= 10:
        await _register_phone(message, phone)
    else:
        await message.answer(
            "📱 Введите номер в формате: 89526834874 или +7 952 683 4874"
        )


async def _register_phone(message: Message, phone: str):
    """Регистрация/обновление номера телефона"""
    telegram_id = message.from_user.id if message.from_user else None
    if not telegram_id:
        await message.answer("❌ Не удалось определить пользователя.")
        return
    phone_norm = normalize_phone(phone)
    async with get_session() as session:
        try:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalars().first()
            inline_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📅 Записаться", url=RECORDING_URL)]
            ])

            if user:
                if user.phone != phone_norm:
                    user.phone = phone_norm
                    await session.commit()
                await message.answer(
                    f"✅ Номер телефона обновлён: {phone}\n\n"
                    "🔔 Уведомления подключены!",
                    reply_markup=KEYBOARD_LOGGED_IN
                )
                await message.answer("🔗 Записаться на процедуру:", reply_markup=inline_kb)
            else:
                new_user = User(telegram_id=telegram_id, phone=phone_norm)
                session.add(new_user)
                await session.commit()
                await session.refresh(new_user)
                user = new_user

                await message.answer(
                    f"✅ Регистрация успешна!\n"
                    f"Ваш номер: {phone}\n\n"
                    "🔔 Уведомления подключены!",
                    reply_markup=KEYBOARD_LOGGED_IN
                )
                await message.answer("🔗 Записаться на процедуру:", reply_markup=inline_kb)

            # Отправляем уведомление о записях, если есть
            result = await session.execute(
                select(Appointment)
                .where(Appointment.user_id == user.id)
                .where(Appointment.status != "canceled")
                .order_by(Appointment.date, Appointment.time)
            )
            appointments = result.scalars().all()
            if appointments:
                text = "📅 Ваши записи:\n\n"
                for app in appointments:
                    status_line = f"📌 {app.visit_status}\n" if getattr(app, "visit_status", None) else ""
                    text += (
                        f"🎯 {app.event}\n"
                        f"📅 Дата: {app.date}\n"
                        f"⏰ Время: {app.time}\n"
                        f"👤 Мастер: {app.master}\n"
                        f"{status_line}"
                        f"📍 Адрес: г.Томск, ул. Фрунзе, 11Б\n"
                        f"🔗 {app.clientlink}\n\n"
                    )
                await message.answer(text)
        except Exception as e:
            import traceback
            traceback.print_exc()
            await message.answer("❌ Произошла ошибка при регистрации. Попробуйте позже.")


@router.message(F.text == "📅 Записаться")
async def handle_btn_zapisatsya(message: Message):
    """Кнопка «Записаться» в меню"""
    if not message.from_user:
        return
    if not _check_button_rate_limit(message.from_user.id, "zapisatsya"):
        await message.answer("⏳ Не более 2 нажатий в минуту. Подождите немного.")
        return
    await message.answer(
        "🔗 Записаться на процедуру:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Открыть запись", url=RECORDING_URL)]
        ])
    )


@router.message(F.text == "📋 Мои записи")
async def handle_btn_my_appointments(message: Message):
    """Кнопка «Мои записи» в меню — тот же функционал что /my_appointments"""
    if not message.from_user:
        return
    if not _check_button_rate_limit(message.from_user.id, "my_appointments"):
        await message.answer("⏳ Не более 2 нажатий в минуту. Подождите немного.")
        return
    await cmd_my_appointments(message)


@router.message(Command("my_appointments"))
async def cmd_my_appointments(message: Message):
    """Показывает все записи пользователя"""
    async with get_session() as session:
        try:
            result = await session.execute(
                select(User).where(User.telegram_id == message.from_user.id)
            )
            user = result.scalars().first()
            
            if not user:
                await message.answer(
                    "❌ Вы не зарегистрированы. Используйте /start для регистрации."
                )
                return
            
            # Получаем все записи пользователя
            result = await session.execute(
                select(Appointment)
                .where(Appointment.user_id == user.id)
                .where(Appointment.status != "canceled")
                .order_by(Appointment.date, Appointment.time)
            )
            appointments = result.scalars().all()
            
            if not appointments:
                await message.answer("📅 У вас пока нет активных записей.")
            else:
                text = "📅 Ваши записи:\n\n"
                for appointment in appointments:
                    status_line = f"📌 {appointment.visit_status}\n" if getattr(appointment, "visit_status", None) else ""
                    text += (
                        f"🎯 {appointment.event}\n"
                        f"📅 Дата: {appointment.date}\n"
                        f"⏰ Время: {appointment.time}\n"
                        f"👤 Мастер: {appointment.master}\n"
                        f"{status_line}"
                        f"📍 Адрес: г.Томск, ул. Фрунзе, 11Б\n"
                        f"🔗 Ссылка: {appointment.clientlink}\n\n"
                    )
                await message.answer(text)
        except Exception as e:
            await message.answer("❌ Произошла ошибка. Попробуйте позже.")
            print(f"Ошибка при получении записей: {e}")


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Справка по командам"""
    help_text = (
        "📖 Доступные команды:\n\n"
        "/start - Начать работу с ботом\n"
        "/my_appointments - Показать мои записи\n"
        "/help - Показать эту справку\n\n"
        "Бот автоматически отправляет уведомления о:\n"
        "• Создании записи\n"
        "• Изменении записи\n"
        "• Отмене записи\n"
        "• После визита\n"
        "• Напоминаниях о записи"
    )
    await message.answer(help_text)
