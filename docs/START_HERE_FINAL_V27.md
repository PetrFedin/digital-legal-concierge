# START HERE FINAL V27

Финальный порядок запуска Telegram-бота.

## Быстрый запуск

```bash
unzip digital-legal-concierge-telegram-bot_v27_go_live.zip
cd tg_ready_v27
./run.sh
```

После запуска открыть:

```text
http://localhost:8000/go-live/ui
```

## Что проверить первым

1. BOT_TOKEN в `.env`.
2. Логин и пароль администратора.
3. `/ready` должен вернуть `ok: true`.
4. `/go-live/ui` должен показать готовность.
5. В Telegram отправить `/start`.
6. В админке открыть `/admin-ui`.

## Основные разделы

- `/go-live/ui` — финальный экран запуска.
- `/production-center/ui` — производственная готовность.
- `/operator` — рабочее место оператора.
- `/admin-ui` — админка.
- `/settings-ui` — суммы, проценты, сроки.
- `/health-center/ui` — состояние сервиса.
- `/diagnostic-center/ui` — диагностика.
- `/recovery-center/ui` — восстановление.
- `/acceptance-center/ui` — приемочная проверка.

## Команды

```bash
./run.sh              # простой запуск
./bot-control.sh      # меню управления
./acceptance.sh       # приемочная проверка
./backup.sh           # резервная копия
./restore.sh          # восстановление
```
