# Быстрый запуск v24

## 1. Распаковать

```bash
unzip digital-legal-concierge-telegram-bot_v24_search_center.zip
cd tg_ready_v24
```

## 2. Запустить

```bash
./run.sh
```

## 3. Проверить

- `/ready` — техническая готовность
- `/operator` — операторская панель
- `/admin-ui` — админка
- `/search-center/ui` — единый поиск
- `/settings-ui` — суммы, проценты, сроки
- `/task-center/ui` — задачи
- `/message-center/ui` — сообщения
- `/audit-center/ui` — аудит
- `/notification-center/ui` — уведомления
- `/backup-center/ui` — резервные копии

## 4. Telegram

В `.env` должен быть указан `BOT_TOKEN`. Если бот запускается локально, используйте polling. Для production можно настроить webhook через файлы из `deploy/` и инструкции v19.
