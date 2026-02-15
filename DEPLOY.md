# Развёртывание на сервере (Docker)

## Быстрый старт

1. Скопируйте проект на сервер и создайте `.env`:

```env
BOT_TOKEN=ваш_токен_от_BotFather
DIKIDI_LOGIN_PHONE=89526834874
DIKIDI_LOGIN_PASSWORD=ваш_пароль
USE_SQLITE=1
```

2. Запуск:

```bash
docker compose up -d --build
```

Бот запустится, БД (SQLite) сохраняется в Docker volume `bot_data`.

## Команды

```bash
# Запуск
docker compose up -d

# Логи (в реальном времени)
docker compose logs -f bot

# Остановка
docker compose down

# Пересборка после изменений кода
docker compose up -d --build
```

## Требования Docker

- **Playwright/Chromium**: образ уже включает браузер. `shm_size: 512mb` нужен для Chromium.
- **init: true**: корректное завершение процессов при остановке контейнера.
- На **Linux** при ошибках Chromium можно добавить `ipc: host` в docker-compose.

## Устранение неполадок

### TelegramConflictError: "make sure that only one bot instance is running"

Один токен бота может обрабатывать updates только в одном процессе. **Одновременно может работать один экземпляр.**

Что сделать:

- Остановите все запущенные боты: `docker compose down`, закройте другие терминалы с `python main.py`.
- Не держите локальный запуск, если бот уже работает в Docker на сервере (и наоборот).
- Для тестов используйте отдельный тестовый бот с другим токеном.

### Парсер: "ElementHandle.click: Timeout" / "intercepts pointer events"

Модальное окно Dikidi (клиент/запись) перекрывает страницу. Парсер теперь автоматически закрывает модалки между записями. При повторении ошибки проверьте `test_parser.py` в отдельном запуске.

## PostgreSQL (опционально)

Для PostgreSQL укажите в `docker-compose.yml`:

```yaml
environment:
  - DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dikidi_bot
```

Или добавьте сервис PostgreSQL и задайте переменную `DATABASE_URL`.
