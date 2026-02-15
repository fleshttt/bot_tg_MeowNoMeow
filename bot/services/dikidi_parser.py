import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from playwright.async_api import async_playwright, Page
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from bot.models.models import Appointment, User, Company
from bot.config import Config
import re
import os
from pathlib import Path


def _as_time(s: str) -> str:
    """Извлекает время в формате HH:MM. 11:00 → '11:00'. Если не время — ''."""
    if not s:
        return ""
    s = s.strip()
    m = re.search(r"(\d{1,2}:\d{2})", s)
    return m.group(1) if m else ""


def _as_date(s: str) -> str:
    """Извлекает дату в формате DD.MM.YYYY. 11.02.2026 → '11.02.2026'. Если строка похожа на время (11:00) — ''."""
    if not s or not re.search(r"\d", s):
        return ""
    s = s.strip()
    # Не трогать время (11:00)
    if re.search(r"\d{1,2}:\d{2}", s) and not re.search(r"\d{1,2}[./]\d{1,2}[./]", s):
        return ""
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{2,4})", s)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{int(d):02d}.{int(mon):02d}.{y}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mon, d = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}.{int(mon):02d}.{y}"
    return ""


def _normalize_date(s: str) -> str:
    """Приводит дату к формату DD.MM.YYYY (например 10.02.2026). Время (11:00) не обрабатывает."""
    return _as_date(s) or s


def _parse_date_from_data_time(data_time: str) -> str:
    """Парсит data-time (timestamp в мс/с или YYYY-MM-DD) в DD.MM.YYYY."""
    if not data_time:
        return ""
    data_time = data_time.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", data_time):
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", data_time)
        if m:
            return f"{int(m.group(3)):02d}.{int(m.group(2)):02d}.{m.group(1)}"
    try:
        ts = int(data_time)
        if ts > 1e12:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%d.%m.%Y")
    except (ValueError, OSError):
        pass
    return ""


# Сокращения месяцев (янв, февр, мар, ...) -> номер
_RU_MONTHS = {
    "янв": 1, "января": 1,
    "февр": 2, "фев": 2, "февраля": 2,
    "мар": 3, "марта": 3,
    "апр": 4, "апреля": 4,
    "май": 5, "мая": 5,
    "июн": 6, "июня": 6,
    "июл": 7, "июля": 7,
    "авг": 8, "августа": 8,
    "сен": 9, "сент": 9, "сентября": 9,
    "окт": 10, "октября": 10,
    "нояб": 11, "ноя": 11, "ноября": 11,
    "дек": 12, "декабря": 12,
}


async def _close_any_modal(page: Page, quick: bool = False) -> None:
    """Закрывает любую открытую модалку. quick=True — только Escape (быстрее, когда модалки нет)."""
    if quick:
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(100)
        except Exception:
            pass
        return
    for _ in range(2):
        closed = False
        for sel in [".bootbox-close-button", ".modal .close", "[data-dismiss='modal']"]:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click(timeout=1500)
                    closed = True
                    await page.wait_for_timeout(150)
                    break
            except Exception:
                continue
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(100)
        except Exception:
            pass
        if not closed:
            break


def _parse_visit_datetime(txt: str, year: int = None) -> tuple:
    """
    Парсит .journal458-visit-datetime: "Пн, 09 февр., 12:00"
    Возвращает (date DD.MM.YYYY, time, day_short) или ("", "", "")
    """
    if not txt:
        return ("", "", "")
    txt = txt.strip()
    year = year or datetime.now().year
    date_str = ""
    time_str = ""
    day_short = ""
    # День недели: Пн, Вт, Ср, ...
    m_day = re.match(r"^(Пн|Вт|Ср|Чт|Пт|Сб|Вс)\s*[,.]?\s*", txt, re.I)
    if m_day:
        day_short = m_day.group(1)
    # Число и месяц: 09 февр. или 9 февр
    m_dm = re.search(r"(\d{1,2})\s+([а-яё]+)", txt)
    if m_dm:
        day_num = int(m_dm.group(1))
        mon_raw = m_dm.group(2).lower().rstrip(".")
        mon_num = None
        for k, v in _RU_MONTHS.items():
            if mon_raw.startswith(k) or k.startswith(mon_raw[:3]):
                mon_num = v
                break
        if mon_num:
            date_str = f"{day_num:02d}.{mon_num:02d}.{year}"
    # Время: только HH:MM (11:00)
    time_str = _as_time(txt)
    return (date_str, time_str, day_short)


def _weekday_full(short: str) -> str:
    """Пн -> Понедельник, Вт -> Вторник, ..."""
    map_ = {
        "Пн": "Понедельник", "Вт": "Вторник", "Ср": "Среда",
        "Чт": "Четверг", "Пт": "Пятница", "Сб": "Суббота", "Вс": "Воскресенье",
    }
    return map_.get(short, short) if short else ""


def _year_from_page_url(url: str) -> int:
    """Извлекает год из start=YYYY-MM-DD в URL журнала."""
    m = re.search(r"start=(\d{4})-\d{2}-\d{2}", url)
    if m:
        return int(m.group(1))
    return datetime.now().year


class DikidiParser:
    def __init__(self):
        self.company_id = Config.DIKIDI_COMPANY_ID
        self.journal_url = Config.DIKIDI_JOURNAL_URL
        self.login_phone = Config.DIKIDI_LOGIN_PHONE
        self.login_password = Config.DIKIDI_LOGIN_PASSWORD
        self._journal_list_base = getattr(
            Config, "DIKIDI_JOURNAL_LIST_BASE",
            "https://dikidi.ru/ru/owner/journal/?company=1993359&view=list&limit=50&period=week"
        )

    def _journal_list_url(self, start_date: datetime = None, end_date: datetime = None) -> str:
        """URL журнала view=list. Если заданы DIKIDI_JOURNAL_START/END — используем их.
        Иначе: 1 неделя назад от начала текущей недели и 3 недели вперёд (полный охват)."""
        start_s = getattr(Config, "DIKIDI_JOURNAL_START", "").strip()
        end_s = getattr(Config, "DIKIDI_JOURNAL_END", "").strip()
        if start_s and end_s:
            pass
        elif start_date is not None and end_date is not None:
            start_s = start_date.strftime("%Y-%m-%d")
            end_s = end_date.strftime("%Y-%m-%d")
        else:
            today = datetime.now().date()
            week_start = today - timedelta(days=today.weekday())
            # 1 неделя назад и 3 недели вперёд (охват ~4 недель)
            start_date = week_start - timedelta(days=7)
            end_date = week_start + timedelta(days=6 + 21)
            start_s = start_date.strftime("%Y-%m-%d")
            end_s = end_date.strftime("%Y-%m-%d")
        base = self._journal_list_base.rstrip("&")
        return f"{base}&start={start_s}&end={end_s}"

    def _normalize_phone_for_input(self) -> tuple:
        """Возвращает (номер для поля: 7XXXXXXXXXX, начинается_ли_с_7). По записи рекордера в поле вводят 79526834874."""
        raw = re.sub(r"[\s\-\(\)]", "", self.login_phone)
        if raw.startswith("+7") or raw.startswith("8"):
            digits = "7" + raw.lstrip("+78")  # 7 + 10 цифр
            return (digits, True)
        if raw.startswith("7") and len(raw) >= 11:
            return (raw[:11], True)
        return (raw, False)

    async def _login(self, page: Page) -> bool:
        """
        Вход по алгоритму из recorder:
        open / → click a>.hidden-xs (Вход/Регистрация) → click "По номеру телефона" →
        type .bootbox-body #number → type name=password → sendKeys Enter
        """
        try:
            print("Переход на https://dikidi.ru/ ...")
            await page.goto("https://dikidi.ru/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1200)

            # Recorder: click css=a > .hidden-xs (Вход / Регистрация)
            print("Клик по Вход / Регистрация...")
            login_selectors = [
                "css=a > .hidden-xs",
                "a:has(span.hidden-xs)",
                "text=Вход / Регистрация",
                "text=Вход/регистрация",
                "text=Вход",
                "a[href*='login']",
            ]
            login_clicked = False
            for selector in login_selectors:
                try:
                    btn = await page.wait_for_selector(selector, timeout=3000)
                    if btn and await btn.is_visible():
                        await btn.click()
                        login_clicked = True
                        print(f"Кнопка входа найдена: {selector}")
                        break
                except Exception:
                    continue
            if not login_clicked:
                print("Кнопка входа не найдена, продолжаем...")
            await page.wait_for_timeout(1000)

            try:
                await page.wait_for_selector("div.bootbox.modal, .bootbox-body", timeout=5000, state="visible")
                print("Модальное окно авторизации открыто")
            except Exception:
                pass
            await page.wait_for_timeout(800)

            # Recorder: click linkText=По номеру телефона (альт: css=.bootbox-body .number > .btn)
            print("Клик «По номеру телефона»...")
            phone_tab_selectors = [
                "css=.bootbox-body .number > .btn",
                "a:has-text('По номеру телефона')",
                "text=По номеру телефона",
                "link=По номеру телефона",
                "a.btn.btn-default.phone-btn",
                "text=По номеру",
            ]
            phone_tab_clicked = False
            for selector in phone_tab_selectors:
                try:
                    el = await page.wait_for_selector(selector, timeout=2000)
                    if el and await el.is_visible():
                        await el.click()
                        phone_tab_clicked = True
                        print(f"Выбран вход по номеру: {selector}")
                        await page.wait_for_timeout(800)
                        break
                except Exception:
                    continue
            if not phone_tab_clicked:
                for el in await page.query_selector_all(".bootbox-body a, .bootbox-body .btn"):
                    try:
                        t = await el.inner_text()
                        if t and "номер" in t.lower():
                            if await el.is_visible():
                                await el.click()
                                phone_tab_clicked = True
                                await page.wait_for_timeout(800)
                                break
                    except Exception:
                        continue
            if not phone_tab_clicked:
                print("Вкладка «По номеру» не найдена, продолжаем...")
            await page.wait_for_timeout(600)

            # Recorder: type css=.bootbox-body #number value 79526834874
            phone_digits, _ = self._normalize_phone_for_input()
            print("Ввод номера телефона...")
            phone_input_selectors = [
                "css=.bootbox-body #number",
                ".bootbox-body input#number",
                "input#number",
                "input[name='number']",
                "input[type='tel']",
            ]
            phone_entered = False
            for selector in phone_input_selectors:
                try:
                    phone_input = await page.wait_for_selector(selector, timeout=5000)
                    if phone_input:
                        await phone_input.scroll_into_view_if_needed()
                        await phone_input.click()
                        await page.wait_for_timeout(300)
                        await phone_input.fill("")
                        await phone_input.fill(phone_digits)
                        phone_entered = True
                        print(f"Номер введён: {phone_digits}")
                        break
                except Exception:
                    continue
            if not phone_entered:
                raise Exception("Поле номера телефона не найдено")

            # Recorder: type name=password, затем sendKeys KEY_ENTER
            print("Ожидание поля пароля...")
            await page.wait_for_selector("input[name='password']", timeout=15000, state="visible")
            await page.wait_for_timeout(500)
            print("Ввод пароля...")
            password_input = await page.query_selector("input[name='password']")
            if not password_input:
                password_input = await page.wait_for_selector("input[type='password']", timeout=3000)
            if password_input:
                await password_input.click()
                await password_input.fill(self.login_password)
                print("Пароль введён")
            else:
                raise Exception("Поле пароля не найдено")
            await page.wait_for_timeout(500)
            # Recorder: click css=.bootbox-body form > .form-group > .btn (кнопка отправки)
            submit_btn = await page.query_selector("css=.bootbox-body form > .form-group > .btn")
            if submit_btn and await submit_btn.is_visible():
                await submit_btn.click()
            else:
                await page.keyboard.press("Enter")

            print("Ожидание завершения авторизации...")
            await page.wait_for_timeout(2500)
            current_url = page.url
            if "login" not in current_url.lower():
                print(f"Авторизация успешна, URL: {current_url}")
                return True
            await page.screenshot(path="login_debug.png")
            print("Возможна проблема с авторизацией, скриншот: login_debug.png")
            return True

        except Exception as e:
            print(f"Ошибка при авторизации: {e}")
            await page.screenshot(path="login_error.png")
            return False
    
    async def parse_appointments(self, session: AsyncSession) -> List[Dict]:
        """
        Парсит записи с сайта Dikidi после авторизации
        Возвращает список словарей с информацией о записях
        """
        appointments = []
        
        async with async_playwright() as p:
            # Запускаем браузер (можно установить headless=False для отладки)
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            
            try:
                login_success = await self._login(page)
                if not login_success:
                    print("Предупреждение: возможна проблема с авторизацией, продолжаем...")

                # Убираем модалку/остатки после входа, чтобы страница была как до регистрации
                await page.wait_for_timeout(800)
                try:
                    close_btn = await page.query_selector(".bootbox-close-button, .modal .close, [data-dismiss='modal']")
                    if close_btn and await close_btn.is_visible():
                        await close_btn.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass

                # Парсим по неделям — 1 нед. назад + 2 нед. вперёд (оптимально по скорости)
                today = datetime.now().date()
                week_start = today - timedelta(days=today.weekday())
                start_date = week_start - timedelta(days=7)
                end_date = week_start + timedelta(days=6 + 14)
                all_seen = set()
                week = start_date
                while week <= end_date:
                    week_end = week + timedelta(days=6)
                    list_url = self._journal_list_url(week, week_end)
                    print(f"Переход на журнал: {list_url}")
                    await page.goto(list_url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(1200)

                    list_selectors = [
                        "[data-view='list']", ".journal-list", ".journal458-record",
                        "table", "[class*='journal']", "[class*='record']",
                    ]
                    list_loaded = False
                    for selector in list_selectors:
                        try:
                            await page.wait_for_selector(selector, timeout=2500)
                            list_loaded = True
                            break
                        except Exception:
                            continue
                    await page.wait_for_timeout(800)

                    week_apps = await self._parse_list_appointments(page, all_seen)
                    for a in week_apps:
                        appointments.append(a)
                    week = week_end + timedelta(days=1)
                print(f"Всего найдено записей: {len(appointments)}")
                
            except Exception as e:
                print(f"Ошибка при парсинге Dikidi: {e}")
                await page.screenshot(path="parse_error.png")
            finally:
                await browser.close()
        
        return appointments

    async def _parse_list_appointments(self, page: Page, seen: set = None) -> List[Dict]:
        """
        Парсит записи из view=list. seen — множество ключей для дедупликации между неделями.
        """
        appointments = []
        seen = seen if seen is not None else set()
        try:
            rows = await page.query_selector_all(".journal458-row")
            if not rows:
                rows = await page.query_selector_all(".journal458-record")
            if not rows:
                for sel in ["[class*='journal458']", ".record", "div[class*='record']"]:
                    els = await page.query_selector_all(sel)
                    if els:
                        rows = els
                        break
            print(f"Строк в этой неделе: {len(rows)}")
            for idx, row in enumerate(rows):
                try:
                    data = await self._extract_list_record_data(row, page, idx)
                    if not data:
                        continue
                    # Уникальность: date + time + phone + event (разные услуги в одном слоте)
                    key = (
                        data.get("date") or "",
                        data.get("time") or "",
                        data.get("phone") or "",
                        data.get("event") or "Услуга",
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    appointments.append(data)
                    print(f"Запись {len(appointments)}: {data.get('master', '')} — {data.get('time', '')} {data.get('client_name', '') or data.get('phone', '')}")
                except Exception as e:
                    print(f"Ошибка парсинга строки {idx + 1}: {e}")
        except Exception as e:
            print(f"Ошибка парсинга списка: {e}")
        return appointments

    async def _extract_list_record_data(self, row_element, page: Page, index: int = 0) -> Optional[Dict]:
        """
        Парсит запись только из строки — без открытия модалок.
        journal458-client-name, journal458-client-phone, journal458-visit-datetime,
        journal458-visit-duration, journal458-visit-status, journal458-ias-title, journal458-ias-services
        """
        try:
            # Имя клиента: .journal458-client-name a
            client_name = ""
            el = await row_element.query_selector(".journal458-client-name a")
            if el:
                client_name = (await el.inner_text()).strip()
            if not client_name:
                el = await row_element.query_selector(".journal458-client-name")
                if el:
                    client_name = (await el.inner_text()).strip()

            # Телефон: .journal458-client-phone
            phone = None
            el = await row_element.query_selector(".journal458-client-phone")
            if el:
                raw = (await el.inner_text()).strip().replace("\xa0", " ")
                phone = re.sub(r"[^\d+]", "", raw)
                if phone:
                    if phone.startswith("8"):
                        phone = "+7" + phone[1:]
                    elif not phone.startswith("+"):
                        phone = "+7" + phone

            # Дата и время: .journal458-visit-datetime (Пн, 09 февр., 12:00)
            date = ""
            time = ""
            day_of_week = ""
            el = await row_element.query_selector(".journal458-visit-datetime")
            if el:
                txt = (await el.inner_text()).strip().replace("\xa0", " ")
                year = _year_from_page_url(page.url)
                date, time, day_short = _parse_visit_datetime(txt, year)
                if day_short:
                    day_of_week = _weekday_full(day_short)
            if not time:
                el = await row_element.query_selector(".journal458-visit-time")
                if el:
                    raw = (await el.inner_text()).strip()
                    time = _as_time(raw) or raw

            # Длительность: .journal458-visit-duration (2 ч 30 мин)
            duration = ""
            el = await row_element.query_selector(".journal458-visit-duration")
            if el:
                duration = (await el.inner_text()).strip().replace("\xa0", " ")

            # Состояние: .journal458-visit-status (status-1/2/3 или текст)
            visit_status = ""
            el = await row_element.query_selector(".journal458-visit-status")
            if el:
                visit_status = (await el.inner_text()).strip()
                if not visit_status:
                    cls = await el.get_attribute("class") or ""
                    if "status-1" in cls:
                        visit_status = "Визит завершен"
                    elif "status-2" in cls:
                        visit_status = "Запись отменена"
                    elif "status-3" in cls:
                        visit_status = "Ожидает визита"

            # Имя мастера: .journal458-ias-title a
            master = ""
            el = await row_element.query_selector(".journal458-ias-title a")
            if el:
                master = (await el.inner_text()).strip()
            if not master:
                el = await row_element.query_selector(".journal458-ias-title")
                if el:
                    master = (await el.inner_text()).strip()

            # Услуга: .journal458-ias-services span
            event = ""
            el = await row_element.query_selector(".journal458-ias-services span")
            if el:
                event = (await el.inner_text()).strip()
            if not event:
                el = await row_element.query_selector(".journal458-ias-services")
                if el:
                    event = (await el.inner_text()).strip()

            if not phone and not time:
                return None

            return {
                "client_name": client_name or "",
                "phone": phone or "",
                "day_of_week": day_of_week,
                "date": date or "",
                "time": time or "",
                "duration": duration,
                "master": master or "Мастер",
                "event": event or "Услуга",
                "clientlink": "https://dikidi.ru/ru/recording/",
                "visit_status": visit_status,
            }
        except Exception as e:
            print(f"Ошибка извлечения записи списка: {e}")
            return None
    
    async def _parse_calendar_appointments(self, page: Page) -> List[Dict]:
        """
        Парсит все записи из календаря (fallback, если list недоступен)
        """
        appointments = []
        
        try:
            appointment_selectors = [
                ".journal458-record",
                ".journal458-record-phone",
                ".journal458-time-wrapper",
                ".journal458-record-name",
                ".appointment",
                ".record",
                "div[class*='journal458']",
                "div[class*='record']",
            ]
            
            all_appointments = []
            for selector in appointment_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        all_appointments.extend(elements)
                except Exception:
                    continue
            
            if not all_appointments:
                cells = await page.query_selector_all("td, .day-cell, .calendar-cell")
                for cell in cells:
                    try:
                        text = await cell.inner_text()
                        if text and re.search(r'\d{1,2}:\d{2}|[А-Яа-я]{2,}', text):
                            all_appointments.append(cell)
                    except Exception:
                        continue
            
            for idx, element in enumerate(all_appointments):
                try:
                    appointment_data = await self._extract_appointment_data(element, page, idx)
                    if appointment_data:
                        appointments.append(appointment_data)
                except Exception as e:
                    continue
            
        except Exception as e:
            print(f"Ошибка при парсинге календаря: {e}")
        
        return appointments
    
    async def _extract_appointment_data(self, element, page: Page, index: int = 0) -> Optional[Dict]:
        """
        Извлекает данные о записи: сверху — мастер, снизу — клиент и время прихода.
        Парсит: master (мастер), time (во сколько клиент должен прийти), client_name, phone, услуга, дата.
        """
        try:
            text_content = await element.inner_text()
            
            try:
                await element.click()
                await page.wait_for_timeout(1000)
                
                detail_selectors = [
                    ".bootbox",
                    ".bootbox-body",
                    ".modal",
                    ".popup",
                    ".details",
                    "[data-modal]",
                    ".appointment-details",
                ]
                
                detail_element = None
                for selector in detail_selectors:
                    detail_element = await page.query_selector(selector)
                    if detail_element:
                        detail_text = await detail_element.inner_text()
                        text_content = detail_text
                        print(f"Найдено модальное окно с деталями")
                        break
                
                if detail_element:
                    close_button = await page.query_selector(".bootbox-close-button, .close, .modal-close, [aria-label='Close']")
                    if close_button:
                        await close_button.click()
                        await page.wait_for_timeout(500)
            except Exception:
                pass
            
            lines = [ln.strip() for ln in text_content.split('\n') if ln.strip()]
            top_part = '\n'.join(lines[:8])   # сверху — мастер
            rest = '\n'.join(lines[8:]) if len(lines) > 8 else text_content  # снизу — клиенты и время прихода
            
            # 1. Мастер — сверху (первая осмысленная строка с именем или после "Мастер"/"Специалист")
            master_patterns = [
                r'Мастер[:\s]+([А-Яа-яЁё\s]{2,})',
                r'Специалист[:\s]+([А-Яа-яЁё\s]{2,})',
                r'^([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)\s*$',  # Имя Фамилия в начале
                r'^([А-ЯЁ][а-яё]+)\s*$',
            ]
            master = ""
            for pattern in master_patterns:
                for block in (top_part, text_content):
                    m = re.search(pattern, block, re.MULTILINE)
                    if m:
                        cand = m.group(1).strip()
                        if len(cand) >= 2 and not re.match(r'^\d', cand):
                            master = cand
                            break
                if master:
                    break
            if not master and lines:
                for ln in lines[:5]:
                    if re.match(r'^[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+$', ln):
                        master = ln
                        break
            
            # 2. Время — только HH:MM (11:00)
            time = _as_time(rest or text_content)
            if not time:
                time = _as_time(text_content)

            # 3. Дата — только DD.MM.YYYY (11.02.2026)
            date = _as_date(text_content)
            if not date:
                m = re.search(r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})', text_content)
                if m:
                    date = f"{int(m.group(3)):02d}.{int(m.group(2)):02d}.{m.group(1)}"
            
            # 4. Услуга
            service_patterns = [
                r'(?:Услуга|Услуги)[:\s]+([А-Яа-яЁё\w\s-]+)',
                r'(\d{1,2}:\d{2})\s*[–-]?\s*([А-Яа-яЁё\w\s-]+?)(?:\n|Мастер|Клиент|телефон|$)',
            ]
            event = ""
            for pattern in service_patterns:
                service_match = re.search(pattern, text_content)
                if service_match:
                    gr = service_match.groups()
                    event = (gr[0] if len(gr) == 1 else gr[1]).strip()
                    if len(event) > 2 and not re.match(r'^\d', event):
                        break
            if not event:
                for line in lines:
                    line = line.strip()
                    if line and not re.match(r'^\d{1,2}:\d{2}$', line) and len(line) > 3 and re.search(r'[а-яА-ЯёЁ]', line):
                        if 'Маникюр' in line or 'педикюр' in line.lower() or 'покрытие' in line.lower() or 'дизайн' in line.lower():
                            event = line
                            break
            
            # 5. Клиент — снизу (имя)
            client_name_patterns = [
                r'Клиент[:\s]+([А-Яа-яЁё\s]{2,})',
                r'Имя[:\s]+([А-Яа-яЁё\s]{2,})',
            ]
            client_name = ""
            for pattern in client_name_patterns:
                name_match = re.search(pattern, text_content)
                if name_match:
                    client_name = name_match.group(1).strip()
                    break
            
            # 6. Номер телефона клиента
            phone_patterns = [
                r'(\+?7\s?\(?\d{3}\)?\s?\d{3}[-.\s]?\d{2}[-.\s]?\d{2})',
                r'(\+?8\s?\(?\d{3}\)?\s?\d{3}[-.\s]?\d{2}[-.\s]?\d{2})',
                r'(\d{10,11})',
            ]
            phone = None
            for pattern in phone_patterns:
                phone_match = re.search(pattern, text_content)
                if phone_match:
                    phone = phone_match.group(1)
                    # Очищаем телефон от пробелов и символов
                    phone = re.sub(r'[\s\-\(\)]', '', phone)
                    if phone.startswith('8'):
                        phone = '+7' + phone[1:]
                    elif not phone.startswith('+'):
                        phone = '+7' + phone
                    break
            
            # Если не нашли телефон в тексте, пробуем найти в атрибутах
            if not phone:
                phone_attr = await element.get_attribute("data-phone")
                if phone_attr:
                    phone = phone_attr
            
            clientlink = "https://dikidi.ru/ru/recording/"
            
            # Проверяем, что есть минимально необходимые данные
            if not phone and not date:
                print(f"Пропущена запись: недостаточно данных")
                return None
            
            # dikidi_id не включаем — в БД присваивается автоматически
            return {
                "event": event or "Услуга",
                "date": date or "",
                "time": time or "",
                "master": master or "Мастер",
                "client_name": client_name or "",
                "phone": phone or "",
                "clientlink": clientlink,
                "visit_status": "",  # В календарном виде нет .journal458-visit-status
            }
            
        except Exception as e:
            print(f"Ошибка при извлечении данных записи: {e}")
            return None
    
    async def sync_appointments(self, session: AsyncSession) -> Dict[str, int]:
        """
        Синхронизирует записи с базой данных
        Возвращает статистику: создано, изменено, отменено
        """
        parsed_appointments = await self.parse_appointments(session)
        
        stats = {"created": 0, "changed": 0, "canceled": 0}
        
        # Получаем все существующие записи. Ключ: (user_id, date, time, event) — чтобы
        # не терять несколько услуг в одном слоте (маникюр+дизайн и т.п.)
        result = await session.execute(select(Appointment))
        existing_appointments = {}
        next_dikidi_id = 1
        for app in result.scalars().all():
            key = (app.user_id, app.date, app.time, app.event or "")
            existing_appointments[key] = app
            if app.dikidi_id is not None and app.dikidi_id >= next_dikidi_id:
                next_dikidi_id = app.dikidi_id + 1

        # Получаем всех пользователей по телефонам (только с указанным номером — для сопоставления с Dikidi)
        result = await session.execute(select(User))
        users_by_phone = {
            user.phone: user
            for user in result.scalars().all()
            if user.phone and user.phone.strip()
        }

        # Нормализуем телефоны в справочнике (без пробелов/скобок для сопоставления)
        def norm_phone(p: str) -> str:
            if not p:
                return ""
            p = p.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            if p.startswith("8"):
                p = "+7" + p[1:]
            elif p and not p.startswith("+"):
                p = "+7" + p
            return p

        users_by_phone_norm = {norm_phone(p): u for p, u in users_by_phone.items()}

        # Привязка к Telegram — только когда пользователь напишет /start и отправит номер.
        # Записи без зарегистрированного пользователя в боте — пропускаем. После регистрации
        # следующий цикл синхронизации подтянет их записи из Dikidi.

        # Получаем компанию (предполагаем одну компанию)
        result = await session.execute(select(Company).limit(1))
        company = result.scalar_one_or_none()

        if not company:
            company = Company(name=Config.COMPANY_NAME, address="Томск")
            session.add(company)
            await session.flush()

        parsed_keys = set()  # (user_id, date, time, event) записей из парсера

        def _norm(s: str) -> str:
            """Нормализация для сравнения: strip, None/пусто — эквивалентны."""
            return (s or "").strip()

        def _same(new_val, old_val, normalize_date: bool = False) -> bool:
            n = _norm(new_val)
            o = _norm(old_val or "")
            if normalize_date and (n or o):
                n = _normalize_date(n) or n
                o = _normalize_date(o) or o
            return n == o

        for app_data in parsed_appointments:
            if not app_data.get("phone"):
                continue
            phone = norm_phone(app_data["phone"])
            user = users_by_phone_norm.get(phone)
            if not user:
                # Пропускаем — пользователь ещё не зарегистрирован в боте. Привязка при /start.
                continue
            event = app_data.get("event") or "Услуга"
            key = (user.id, app_data["date"], app_data["time"], event)
            parsed_keys.add(key)
            existing_app = existing_appointments.get(key)
            visit_status = app_data.get("visit_status") or ""

            if existing_app:
                # Обновляем в БД только при реальном изменении любых полей
                changed = (
                    not _same(app_data["event"], existing_app.event) or
                    not _same(app_data["date"], existing_app.date, normalize_date=True) or
                    not _same(app_data["time"], existing_app.time) or
                    not _same(app_data["master"], existing_app.master) or
                    not _same(visit_status, existing_app.visit_status) or
                    not _same(app_data.get("clientlink"), existing_app.clientlink)
                )
                if changed:
                    existing_app.event = app_data["event"]
                    existing_app.date = app_data["date"]
                    existing_app.time = app_data["time"]
                    existing_app.master = app_data["master"]
                    existing_app.clientlink = app_data.get("clientlink") or existing_app.clientlink
                    existing_app.visit_status = visit_status
                    existing_app.status = "changed"
                    stats["changed"] += 1
            else:
                new_appointment = Appointment(
                    dikidi_id=next_dikidi_id,
                    user_id=user.id,
                    company_id=company.id,
                    event=app_data["event"],
                    date=app_data["date"],
                    time=app_data["time"],
                    master=app_data["master"],
                    clientlink=app_data["clientlink"],
                    visit_status=visit_status,
                    status="created",
                )
                session.add(new_appointment)
                next_dikidi_id += 1
                stats["created"] += 1

        # Помечаем как отменённые записи, которых нет в распарсенных
        for key, appointment in existing_appointments.items():
            if key not in parsed_keys and appointment.status != "canceled":
                appointment.status = "canceled"
                stats["canceled"] += 1
        
        await session.commit()
        return stats
