# Настройка парсера Dikidi

## Важно

Парсер Dikidi использует Playwright для парсинга веб-страницы. Структура HTML на сайте может отличаться, поэтому необходимо адаптировать код под реальную структуру страницы.

## Что нужно настроить

### 1. Селекторы элементов

В файле `bot/services/dikidi_parser.py` в методе `_extract_appointment_data()` нужно настроить селекторы для поиска элементов на странице:

```python
# Пример - нужно адаптировать под реальную структуру
appointment_elements = await page.query_selector_all(".appointment-item")
```

### 2. Извлечение данных

Метод `_extract_appointment_data()` использует регулярные выражения для извлечения данных из текста. Возможно, потребуется:

- Найти правильные CSS-селекторы для элементов
- Адаптировать регулярные выражения под формат данных на сайте
- Использовать атрибуты data-* для получения ID записей

### 3. Авторизация (если требуется)

Если страница журнала требует авторизации, нужно добавить логику входа:

```python
# Пример авторизации
await page.goto("https://dikidi.ru/login")
await page.fill("#login", "your_login")
await page.fill("#password", "your_password")
await page.click("button[type='submit']")
await page.wait_for_navigation()
```

### 4. Ожидание загрузки данных

Возможно, потребуется настроить ожидание загрузки динамического контента:

```python
# Ждем загрузки конкретного элемента
await page.wait_for_selector(".appointments-list", timeout=10000)
```

## Отладка

Для отладки парсера можно:

1. Запустить браузер в режиме headless=False:
```python
browser = await p.chromium.launch(headless=False)
```

2. Добавить скриншоты:
```python
await page.screenshot(path="debug.png")
```

3. Вывести HTML страницы:
```python
html = await page.content()
print(html)
```

## Альтернативный подход

Если парсинг через Playwright не работает, можно рассмотреть:

1. Использование API Dikidi (если доступно)
2. Selenium вместо Playwright
3. Requests + BeautifulSoup для статических страниц
