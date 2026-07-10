# Launch checklist

1. Скопировать `.env.example` в `.env`.
2. Указать `BOT_TOKEN`.
3. Выполнить `pip install -e .`.
4. Выполнить `python scripts/init_db.py`.
5. Запустить `python -m app.main`.
6. Проверить `/health`.
7. Проверить `/admin/settings` с заголовком `X-Admin-Token: dev-admin-token`.
8. Запустить Telegram: `RUN_BOT=true python -m app.main`.

## Что редактируется без кода

- первый платеж М1;
- второй платеж М1;
- success fee;
- стоимость консультации;
- срок ожидания после претензии;
- время удержания слота.
