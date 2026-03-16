"""
Проверка: доходят ли сообщения в Telegram (токен и chat_id из .env).
Запуск из корня проекта: python scripts/test_telegram_alerts.py
"""
import os
import sys

# корень проекта = родитель папки scripts
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

# подгрузить .env
from pathlib import Path
_env = Path(_root) / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Токен для API = вся строка TELEGRAM (как у BotFather: число:строка), не только часть после двоеточия
token = (os.getenv("TELEGRAM") or "").strip().split("\n")[0].strip()
chat_id = (os.getenv("TELEGRAM_ALERTS_CHAT_ID") or "").strip()

if not token:
    print("В .env не задан TELEGRAM (формат: id:token или token).")
    sys.exit(1)
if not chat_id:
    print("В .env не задан TELEGRAM_ALERTS_CHAT_ID. Добавь его и перезапусти.")
    sys.exit(1)

async def main():
    try:
        from telegram import Bot
        from telegram.error import InvalidToken
    except ImportError:
        print("Установи: pip install python-telegram-bot")
        sys.exit(1)
    bot = Bot(token=token)
    try:
        await bot.send_message(chat_id=int(chat_id), text="Тест уведомления для агента ✅")
        print("Сообщение отправлено. Проверь Telegram — если пришло, токен и chat_id верные.")
    except InvalidToken:
        print(
            "Токен не принят (InvalidToken / Not Found). Сделай в Telegram:\n"
            "  1. @BotFather → /mybots → выбери бота → API Token\n"
            "  2. Если написано «Revoke» — получи новый токен и вставь в .env в переменную TELEGRAM (после двоеточия: только токен, число перед двоеточием оставь как есть).\n"
            "  3. Если бот удалён — создай нового /newbot и подставь новый токен в .env."
        )
        sys.exit(1)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
