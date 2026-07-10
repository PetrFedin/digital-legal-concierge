# Передача проекта v27

Проект собран как Telegram-first система по двум маршрутам: М1 взыскание и М2 консультация.

## Что использовать оператору

- `/operator` — основной рабочий стол.
- `/production-center/ui` — готовность запуска.
- `/health-center/ui` — здоровье сервиса.
- `/diagnostic-center/ui` — диагностика.
- `/recovery-center/ui` — восстановление.
- `/settings-ui` — суммы, проценты, сроки.
- `/admin-ui` — карточки дел, документы, платежи.

## Команды

```bash
./run.sh
./bot-control.sh
./acceptance.sh
./backup.sh
./restore.sh
```

## Перед боевым запуском

1. Заполнить `.env`.
2. Установить реальный `BOT_TOKEN`.
3. Настроить домен и HTTPS.
4. Подключить YooKassa или оставить fake только для demo.
5. Проверить `/production-center/ui`.
6. Прогнать `./acceptance.sh`.
