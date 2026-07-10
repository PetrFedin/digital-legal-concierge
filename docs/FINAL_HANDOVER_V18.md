# Передача проекта v19

Проект готов как Telegram MVP с операторской панелью, админкой, маршрутами М1/М2 и простым запуском.

## Передать заказчику

1. Архив проекта `digital-legal-concierge-telegram-bot_v19_access_ready.zip`.
2. `.env` не передавать публично, хранить отдельно.
3. Инструкцию `docs/START_SIMPLE_V19.md`.
4. Инструкцию `docs/SECURITY_AND_ACCESS_V19.md`.
5. Инструкцию `docs/WHAT_IS_READY_V19.md`.

## Проверка перед запуском

```bash
./run.sh
python scripts/full_check_v19.py
python scripts/security_check.py
```

## Основные URL

```text
/operator
/login
/admin-ui
/ready
/security-check
/scenario-map-ui
/handover
```
