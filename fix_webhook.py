"""
Скрипт для отключения webhook и проверки бота.
Запускайте перед main.py, если видите TelegramConflictError.
"""
import asyncio
import sys
from bot.config import Config


async def main():
    if not Config.BOT_TOKEN:
        print("BOT_TOKEN не найден в .env")
        sys.exit(1)

    import aiohttp
    base = f"https://api.telegram.org/bot{Config.BOT_TOKEN}"

    async with aiohttp.ClientSession() as session:
        # 1. Проверяем текущий webhook
        async with session.get(f"{base}/getWebhookInfo") as r:
            data = await r.json()
            if not data.get("ok"):
                print("Ошибка getWebhookInfo:", data)
                sys.exit(1)
            wh = data.get("result", {}).get("url") or "не установлен"
            print(f"Webhook сейчас: {wh}")

        # 2. Удаляем webhook
        async with session.post(f"{base}/deleteWebhook") as r:
            data = await r.json()
            if data.get("ok"):
                print("Webhook успешно удалён. Бот готов к polling.")
            else:
                print("Ошибка deleteWebhook:", data)
                sys.exit(1)

        # 3. Проверяем, что бот доступен
        async with session.get(f"{base}/getMe") as r:
            info = (await r.json()).get("result", {})
            print(f"Бот: @{info.get('username', '?')} (id={info.get('id', '?')})")


if __name__ == "__main__":
    asyncio.run(main())
