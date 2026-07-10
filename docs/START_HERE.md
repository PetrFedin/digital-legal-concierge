# START HERE — запуск Telegram-бота

1. Создайте бота в BotFather.
2. Скопируйте токен.
3. В корне проекта выполните:

```bash
cp .env.example .env
```

4. Откройте `.env` и замените:

```env
BOT_TOKEN=CHANGE_ME
```

на токен Telegram-бота.

5. Запустите:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python scripts/init_db.py
python -m app.main
```

6. Откройте Telegram-бота и нажмите `/start`.

7. Админка:

```text
http://localhost:8000/admin-ui
```

