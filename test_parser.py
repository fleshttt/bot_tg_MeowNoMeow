"""
Тестовый скрипт для проверки парсера Dikidi
Запуск: python test_parser.py
"""
import asyncio
import sys
import os
import json
import argparse
from datetime import datetime, timedelta

# Исправление кодировки для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.async_api import async_playwright, Page
from bot.config import Config
import re


def _normalize_phone_for_input(login_phone: str) -> str:
    """Возвращает номер для поля: 7XXXXXXXXXX (как в recorder: 79526834874)."""
    raw = re.sub(r"[\s\-\(\)]", "", login_phone)
    if raw.startswith("+7") or raw.startswith("8"):
        return "7" + raw.lstrip("+78")
    if raw.startswith("7") and len(raw) >= 11:
        return raw[:11]
    return raw


def _normalize_date(s: str) -> str:
    """Приводит дату к формату DD.MM.YYYY (например 10.02.2026)."""
    if not s or not re.search(r"\d", s):
        return s
    s = s.strip()
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{2,4})$", s)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        if len(y) == 2:
            y = "20" + y
        return f"{int(d):02d}.{int(mon):02d}.{y}"
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mon, d = m.group(1), m.group(2), m.group(3)
        return f"{int(d):02d}.{int(mon):02d}.{y}"
    return s


def _parse_date_from_data_time(data_time: str) -> str:
    """Парсит data-time (timestamp или YYYY-MM-DD) в DD.MM.YYYY."""
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


_RU_MONTHS = {
    "янв": 1, "января": 1, "февр": 2, "фев": 2, "февраля": 2,
    "мар": 3, "марта": 3, "апр": 4, "апреля": 4, "май": 5, "мая": 5,
    "июн": 6, "июня": 6, "июл": 7, "июля": 7, "авг": 8, "августа": 8,
    "сен": 9, "сент": 9, "сентября": 9, "окт": 10, "октября": 10,
    "нояб": 11, "ноя": 11, "ноября": 11, "дек": 12, "декабря": 12,
}


def _parse_visit_datetime(txt: str, year: int = None) -> tuple:
    """Парсит .journal458-visit-datetime: Пн, 09 февр., 12:00 -> (date, time, day_short)."""
    if not txt:
        return ("", "", "")
    txt = txt.strip()
    year = year or datetime.now().year
    date_str, time_str, day_short = "", "", ""
    m_day = re.match(r"^(Пн|Вт|Ср|Чт|Пт|Сб|Вс)\s*[,.]?\s*", txt, re.I)
    if m_day:
        day_short = m_day.group(1)
    m_dm = re.search(r"(\d{1,2})\s+([а-яё]+)", txt)
    if m_dm:
        day_num = int(m_dm.group(1))
        mon_raw = m_dm.group(2).lower().rstrip(".")
        for k, v in _RU_MONTHS.items():
            if mon_raw.startswith(k) or (len(mon_raw) >= 3 and k.startswith(mon_raw[:3])):
                date_str = f"{day_num:02d}.{v:02d}.{year}"
                break
    m_time = re.search(r"(\d{1,2}:\d{2})", txt)
    if m_time:
        time_str = m_time.group(1)
    return (date_str, time_str, day_short)


def _weekday_full(short: str) -> str:
    """Пн -> Понедельник, Вт -> Вторник, ..."""
    m = {"Пн": "Понедельник", "Вт": "Вторник", "Ср": "Среда", "Чт": "Четверг",
         "Пт": "Пятница", "Сб": "Суббота", "Вс": "Воскресенье"}
    return m.get(short, short) if short else ""


def _year_from_page_url(url: str) -> int:
    """Год из start=YYYY-MM-DD в URL."""
    m = re.search(r"start=(\d{4})-\d{2}-\d{2}", url)
    return int(m.group(1)) if m else datetime.now().year


def _journal_list_url():
    """
    URL журнала view=list ДЛЯ ТЕСТА.
    По условию теста всегда открываем фиксированный месяц:
    https://dikidi.ru/ru/owner/journal/?company=1993359&view=list&start=2026-02-01&end=2026-02-28&limit=50&period=month
    """
    return "https://dikidi.ru/ru/owner/journal/?company=1993359&view=list&start=2026-02-01&end=2026-02-28&limit=50&period=month"


class DikidiParserTest:
    """Упрощенная версия парсера для тестирования без БД. Тот же сценарий входа, что в DikidiParser."""
    
    def __init__(self):
        self.company_id = Config.DIKIDI_COMPANY_ID
        self.journal_url = Config.DIKIDI_JOURNAL_URL
        self.login_phone = Config.DIKIDI_LOGIN_PHONE
        self.login_password = Config.DIKIDI_LOGIN_PASSWORD

    async def _login(self, page: Page) -> bool:
        """
        Авторизация по актуальному сценарию (аналогично DikidiParser._login):
        1) open https://dikidi.ru/
        2) клик по «Вход / Регистрация»
        3) выбор «По номеру телефона»
        4) ввод номера и пароля
        5) отправка формы
        """
        try:
            print("Переход на https://dikidi.ru/ ...")
            await page.goto("https://dikidi.ru/", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1000)

            # 1. Кнопка «Вход / Регистрация»
            print("Клик Вход / Регистрация...")
            login_locators = [
                "xpath=//div[@id='root-container']/div/div/ul/li[3]/a/span",
                "li.authorization > a",
                "css=span.hidden-xs",
                "a:has(span.hidden-xs)",
                "text=Вход / Регистрация",
                "text=Вход/регистрация",
                "text=Вход",
            ]
            login_clicked = False
            for selector in login_locators:
                try:
                    btn = await page.wait_for_selector(selector, timeout=4000)
                    if btn and await btn.is_visible():
                        await btn.click()
                        print(f"[OK] Вход: {selector}")
                        login_clicked = True
                        break
                except Exception:
                    continue
            if not login_clicked:
                print("[WARN] Кнопка входа не найдена, продолжаем...")
            await page.wait_for_timeout(800)

            # Ждём модальное окно авторизации
            try:
                await page.wait_for_selector("div.bootbox.modal, .bootbox-body", timeout=8000, state="visible")
                print("[OK] Модальное окно авторизации")
            except Exception:
                print("[WARN] Модальное окно авторизации не найдено")
            await page.wait_for_timeout(500)

            # 2. Вкладка «По номеру телефона»
            print("Клик «По номеру телефона»...")
            phone_tab_locators = [
                "css=div.bootbox-body > div.container.base > div.form-group.text-center.number > a.btn.btn-default.phone-btn",
                "css=.bootbox-body .number > .btn",
                "a:has-text('По номеру телефона')",
                "text=По номеру телефона",
                "text=По номеру",
            ]
            phone_tab_clicked = False
            for selector in phone_tab_locators:
                try:
                    el = await page.wait_for_selector(selector, timeout=4000)
                    if el and await el.is_visible():
                        await el.click()
                        print(f"[OK] По номеру: {selector}")
                        phone_tab_clicked = True
                        break
                except Exception:
                    continue
            await page.wait_for_timeout(500)

            # 2.1. Выбор страны Russia (если открыт дропдаун с флагами)
            try:
                country_btn = await page.query_selector(
                    "div.input-group-btn > button.btn.btn-default.dropdown-toggle"
                )
                if country_btn and await country_btn.is_visible():
                    await country_btn.click()
                    await page.wait_for_timeout(300)
                    for sel in [
                        "text=Россия",
                        "li a:has-text('Россия')",
                        "ul.dropdown-menu li:nth-child(63) a",
                    ]:
                        try:
                            opt = await page.query_selector(sel)
                            if opt and await opt.is_visible():
                                await opt.click()
                                print("[OK] Страна выбрана: Россия")
                                break
                        except Exception:
                            continue
            except Exception:
                pass

            # 3. Ввод номера телефона
            phone_digits = _normalize_phone_for_input(self.login_phone)
            print(f"Ввод номера телефона {phone_digits}...")
            phone_input_locators = [
                "css=div.bootbox-body > div.container.auth > form > div.form-group > div.input-group.input-phone.f16 > #number",
                "css=.bootbox-body #number",
                ".bootbox-body input#number",
                "input#number",
                "input[name='number']",
                "input[type='tel']",
            ]
            phone_entered = False
            for selector in phone_input_locators:
                try:
                    phone_input = await page.wait_for_selector(selector, timeout=5000)
                    if phone_input:
                        await phone_input.scroll_into_view_if_needed()
                        await phone_input.click()
                        await page.wait_for_timeout(300)
                        await phone_input.fill("")
                        await phone_input.fill(phone_digits)
                        print(f"[OK] Номер введён через селектор: {selector}")
                        phone_entered = True
                        break
                except Exception:
                    continue
            if not phone_entered:
                raise Exception("Поле номера не найдено")

            # 4. Ввод пароля
            print("Ввод пароля...")
            password_input = await page.query_selector("input[name='password']")
            if not password_input:
                password_input = await page.wait_for_selector("input[type='password']", timeout=8000)
            if password_input:
                await password_input.click()
                await password_input.fill(self.login_password)
                print("[OK] Пароль введён")
            else:
                raise Exception("Поле пароля не найдено")

            # 5. Отправка формы
            submit_locators = [
                "css=div.bootbox-body > div.container.auth > form > div.form-group.footer > button.btn.btn-auth.btn-dikidi",
                "css=.bootbox-body form > .form-group > .btn",
                "css=.bootbox-body button.btn-auth",
            ]
            submitted = False
            for selector in submit_locators:
                try:
                    submit_btn = await page.query_selector(selector)
                    if submit_btn and await submit_btn.is_visible():
                        await submit_btn.click()
                        print(f"[OK] Нажата кнопка входа: {selector}")
                        submitted = True
                        break
                except Exception:
                    continue
            if not submitted:
                await page.keyboard.press("Enter")
                print("[INFO] Отправка формы клавишей Enter")

            await page.wait_for_timeout(2500)
            print(f"[OK] Авторизация завершена, URL: {page.url}")
            return True
        except Exception as e:
            print(f"[ERROR] Авторизация: {e}")
            try:
                await page.screenshot(path="login_error.png", timeout=5000)
            except Exception:
                pass
            return False

    async def parse_appointments(self) -> list:
        """Парсит записи с сайта Dikidi"""
        appointments = []
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            
            try:
                login_success = await self._login(page)
                if not login_success:
                    print("⚠ Предупреждение: возможна проблема с авторизацией")

                # Убираем модалку после входа
                await page.wait_for_timeout(1500)
                try:
                    close_btn = await page.query_selector(".bootbox-close-button, .modal .close")
                    if close_btn and await close_btn.is_visible():
                        await close_btn.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass

                # Журнал списком за текущую неделю
                list_url = _journal_list_url()
                print(f"\nПереход на журнал (list): {list_url}")
                await page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(4000)

                print("Ожидание загрузки списка...")
                list_loaded = False
                for selector in ["[data-view='list']", ".journal458-record", ".journal-list", "table", "[class*='record']"]:
                    try:
                        await page.wait_for_selector(selector, timeout=5000)
                        list_loaded = True
                        print(f"[OK] Список загружен: {selector}")
                        break
                    except Exception:
                        continue
                if not list_loaded:
                    await page.screenshot(path="calendar_not_found.png")
                    print("⚠ Список не найден, скриншот: calendar_not_found.png")
                await page.wait_for_timeout(2000)

                print("\nНачало парсинга записей (list)...")
                appointments = await self._parse_list_appointments(page)
                print(f"[OK] Найдено записей: {len(appointments)}")
                
            except Exception as e:
                print(f"[ERROR] Ошибка при парсинге: {e}")
                try:
                    await page.screenshot(path="parse_error.png", timeout=5000)
                except Exception:
                    pass
                import traceback
                traceback.print_exc()
            finally:
                try:
                    print("\nОжидание 5 секунд перед закрытием браузера...")
                    await asyncio.sleep(5)
                    if not browser.is_connected():
                        print("Браузер уже закрыт")
                    else:
                        await browser.close()
                        print("Браузер закрыт")
                except Exception as e:
                    print(f"Ошибка при закрытии браузера: {e}")
        
        return appointments

    async def _parse_list_appointments(self, page: Page) -> list:
        """
        Парсит записи только из строк списка .journal458-row — без открытия модалок.
        Если есть кнопка «Показать ещё» (button.btn.btn-default.btn-more), нажимает её и читает дальше.
        """
        appointments = []
        seen = set()
        max_load_more = 50
        load_more_count = 0
        try:
            while True:
                rows = await page.query_selector_all(".journal458-row")
                if not rows:
                    rows = await page.query_selector_all(".journal458-record")
                if not rows:
                    for sel in ["[class*='journal458']", ".record", "div[class*='record']"]:
                        els = await page.query_selector_all(sel)
                        if els:
                            rows = els
                            break
                if load_more_count == 0:
                    print(f"Строк в списке: {len(rows)}")

                for idx, element in enumerate(rows):
                    try:
                        data = await self._extract_list_record_data(element, page, idx)
                        if not data:
                            continue
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
                    except Exception:
                        continue

                # Кнопка «Показать ещё»
                btn = await page.query_selector("button.btn.btn-default.btn-more, .journal458-buttons .btn-more")
                if not btn or load_more_count >= max_load_more:
                    break
                try:
                    visible = await btn.is_visible()
                except Exception:
                    visible = False
                if not visible:
                    break
                try:
                    await btn.click()
                    load_more_count += 1
                    print(f"Нажато «Показать ещё» ({load_more_count})")
                    await page.wait_for_timeout(1200)
                except Exception as e:
                    print(f"[WARN] Ошибка при нажатии «Показать ещё»: {e}")
                    break
        except Exception as e:
            print(f"[ERROR] Парсинг списка: {e}")
        return appointments

    async def _extract_list_record_data(self, row_element, page: Page, index: int = 0) -> dict:
        """Парсит только из строки — без открытия модалок."""
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
                    phone = "+7" + phone[1:] if phone.startswith("8") else ("+7" + phone if not phone.startswith("+") else phone)

            # Дата и время: .journal458-visit-datetime
            date, time, day_of_week = "", "", ""
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
                    time = (await el.inner_text()).strip()

            # Длительность: .journal458-visit-duration
            duration = ""
            el = await row_element.query_selector(".journal458-visit-duration")
            if el:
                duration = (await el.inner_text()).strip().replace("\xa0", " ")

            # Состояние: .journal458-visit-status
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

            # Мастер: .journal458-ias-title a
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
        except Exception:
            return None

    async def _parse_calendar_appointments(self, page: Page) -> list:
        """Парсит все записи из календаря"""
        appointments = []
        
        try:
            appointment_selectors = [
                ".appointment",
                ".record",
                ".booking",
                "[data-appointment]",
                ".calendar-event",
                ".day-event",
                "td:has(.appointment)",
                ".cell-event",
                "div[class*='appointment']",
                "div[class*='record']"
            ]
            
            all_appointments = []
            for selector in appointment_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        print(f"  Найдено элементов по селектору {selector}: {len(elements)}")
                        all_appointments.extend(elements)
                except:
                    continue
            
            if not all_appointments:
                print("  Поиск записей альтернативным способом...")
                cells = await page.query_selector_all("td, .day-cell, .calendar-cell")
                for cell in cells:
                    text = await cell.inner_text()
                    if text and len(text.strip()) > 0:
                        if re.search(r'\d{1,2}:\d{2}|[А-Яа-я]{2,}', text):
                            all_appointments.append(cell)
            
            print(f"  Всего найдено потенциальных записей: {len(all_appointments)}")
            
            for idx, element in enumerate(all_appointments):
                try:
                    appointment_data = await self._extract_appointment_data(element, page, idx)
                    if appointment_data:
                        appointments.append(appointment_data)
                except Exception as e:
                    print(f"  [ERROR] Ошибка при парсинге записи {idx + 1}: {e}")
                    continue
            
        except Exception as e:
            print(f"[ERROR] Ошибка при парсинге календаря: {e}")
        
        return appointments
    
    async def _extract_appointment_data(self, element, page: Page, index: int = 0) -> dict:
        """Извлекает данные о записи"""
        try:
            text_content = await element.inner_text()
            
            try:
                await element.click()
                await page.wait_for_timeout(1000)
                
                detail_selectors = [
                    ".modal",
                    ".popup",
                    ".details",
                    "[data-modal]",
                    ".appointment-details"
                ]
                
                detail_element = None
                for selector in detail_selectors:
                    detail_element = await page.query_selector(selector)
                    if detail_element:
                        detail_text = await detail_element.inner_text()
                        text_content = detail_text
                        break
                
                if detail_element:
                    close_button = await page.query_selector(".close, .modal-close, [aria-label='Close']")
                    if close_button:
                        await close_button.click()
                        await page.wait_for_timeout(500)
            except:
                pass
            
            time_match = re.search(r'(\d{1,2}):(\d{2})', text_content)
            time = time_match.group(0) if time_match else ""
            
            date_match = re.search(r'(\d{1,2})[./](\d{1,2})[./](\d{2,4})', text_content)
            if not date_match:
                date_match = re.search(r'(\d{4})[./-](\d{1,2})[./-](\d{1,2})', text_content)
            
            date = date_match.group(0) if date_match else ""
            
            master_patterns = [
                r'Мастер[:\s]+([А-Яа-яЁё\s]{2,})',
                r'Специалист[:\s]+([А-Яа-яЁё\s]{2,})',
                r'([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)',
            ]
            master = ""
            for pattern in master_patterns:
                master_match = re.search(pattern, text_content)
                if master_match:
                    master = master_match.group(1).strip()
                    break
            
            service_patterns = [
                r'(?:Услуга|Услуги)[:\s]+([А-Яа-яЁё\w\s-]+)',
                r'(\d{1,2}:\d{2})\s+([А-Яа-яЁё\w\s-]+?)(?:\n|Мастер|Клиент|$)',
            ]
            event = ""
            for pattern in service_patterns:
                service_match = re.search(pattern, text_content)
                if service_match:
                    event = service_match.group(1 if len(service_match.groups()) == 1 else 2).strip()
                    break
            
            if not event:
                lines = text_content.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not re.match(r'^\d{1,2}:\d{2}$', line) and len(line) > 3:
                        event = line
                        break
            
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
                    phone = re.sub(r'[\s\-\(\)]', '', phone)
                    if phone.startswith('8'):
                        phone = '+7' + phone[1:]
                    elif not phone.startswith('+'):
                        phone = '+7' + phone
                    break
            
            if not phone:
                phone_attr = await element.get_attribute("data-phone")
                if phone_attr:
                    phone = phone_attr
            
            clientlink = "https://dikidi.ru/ru/recording/"
            
            if not phone and not date:
                return None
            
            return {
                "event": event or "Услуга",
                "date": date or "",
                "time": time or "",
                "master": master or "Мастер",
                "client_name": client_name or "",
                "phone": phone or "",
                "clientlink": clientlink
            }
            
        except Exception as e:
            print(f"    Ошибка при извлечении данных: {e}")
            return None


def print_parser_info():
    """Выводит описание работы парсера и извлекаемых полей"""
    print()
    print("=" * 60)
    print("  КАК РАБОТАЕТ ПАРСЕР")
    print("=" * 60)
    print("  1. Открывает https://dikidi.ru/")
    print("  2. Нажимает «Вход» → «По номеру телефона»")
    print("  3. Вводит номер (при необходимости выбирает страну Russia)")
    print("  4. Ждёт появления поля пароля → вводит пароль → «Продолжить»")
    print("  5. После входа убирает модалку, переходит на журнал списком (view=list) за текущую неделю:")
    print("     .../journal/?company=...&view=list&start=YYYY-MM-DD&end=YYYY-MM-DD&limit=50&period=week")
    print("  6. Парсит строки списка без открытия модалок — все данные из DOM строки")
    print("=" * 60)
    print("  КАКУЮ ИНФОРМАЦИЮ ПАРСИТ (по каждой записи)")
    print("=" * 60)
    print("  В списке (view=list):")
    print("  • .journal458-client-name a — имя клиента")
    print("  • .journal458-client-phone — телефон")
    print("  • .journal458-visit-datetime — дата, время (Пн → Понедельник)")
    print("  • .journal458-visit-duration — длительность (2 ч 30 мин)")
    print("  • .journal458-visit-status — состояние (Визит завершен / Запись отменена / Ожидает визита)")
    print("  • .journal458-ias-title a — мастер")
    print("  • .journal458-ias-services span — услуга")
    print("  Поля: client_name, phone, day_of_week, date, time, duration, visit_status, master, event, clientlink")
    print("=" * 60)
    print()


async def test_parser():
    """Тестирует парсер"""
    print_parser_info()
    parser = DikidiParserTest()
    
    print("ТЕСТИРОВАНИЕ ПАРСЕРА DIKIDI")
    print(f"Номер: {parser.login_phone}  |  Журнал: {parser.journal_url}")
    print()
    
    try:
        appointments = await parser.parse_appointments()
        
        print("\n" + "=" * 60)
        print(f"РЕЗУЛЬТАТ: Найдено записей: {len(appointments)}")
        print("=" * 60)
        
        for idx, app in enumerate(appointments, 1):
            print(f"\n📅 Запись #{idx}:")
            print(f"   №: {idx}")
            print(f"   Клиент: {app.get('client_name', 'N/A')}  |  Телефон: {app.get('phone', 'N/A')}")
            print(f"   Визит: {app.get('day_of_week', '')} {app.get('date', 'N/A')} {app.get('time', 'N/A')}  Длительность: {app.get('duration', '')}")
            print(f"   Состояние: {app.get('visit_status', '(не распознано)')}")
            print(f"   Сотрудник: {app.get('master', 'N/A')}  |  Услуга: {app.get('event', 'N/A')}")
            print(f"   Ссылка: {app.get('clientlink', 'N/A')}")
        
        print("\n" + "=" * 60)
        if len(appointments) > 0:
            print("[SUCCESS] ПАРСИНГ ЗАВЕРШЕН УСПЕШНО!")
        else:
            print("[WARNING] НЕ НАЙДЕНО ЗАПИСЕЙ")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[ERROR] ОШИБКА: {e}")
        import traceback
        traceback.print_exc()

    return appointments


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--info", "-i", action="store_true", help="Показать описание и выйти")
    ap.add_argument("--out", default="", help="Путь к файлу результата (JSON). Если не задан — создастся автоматически.")
    args = ap.parse_args()

    if args.info:
        print_parser_info()
        print("Запуск полного теста (с браузером):  python test_parser.py")
        raise SystemExit(0)

    parsed = asyncio.run(test_parser())

    out_path = args.out.strip()
    if not out_path:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"dikidi_parsed_{ts}.json")

    # dikidi_id не сохраняем — он присваивается автоматически в БД при синхронизации
    appointments_out = [{k: v for k, v in (app or {}).items() if k != "dikidi_id"} for app in (parsed or [])]
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "journal_url": Config.DIKIDI_JOURNAL_URL,
        "company_id": Config.DIKIDI_COMPANY_ID,
        "count": len(appointments_out),
        "appointments": appointments_out,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print()
    print(f"[OK] Результат сохранён в файл: {out_path}")
