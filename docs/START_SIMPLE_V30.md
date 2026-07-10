# Быстрый старт v30

## 1. Запуск

```bash
unzip digital-legal-concierge-telegram-bot_v30_maintenance_ready.zip
cd tg_ready_v30
./run.sh
```

## 2. Главная страница оператора

Открыть:

```text
http://localhost:8000/maintenance-center/ui
```

Оттуда доступны все основные центры: запуск, production, QA, handover, настройки, операции, сообщения, аудит, backup, поиск и карта сценариев.

## 3. Минимальные настройки

В `.env` обязательно проверить:

```text
BOT_TOKEN=
ADMIN_USERNAME=
ADMIN_PASSWORD=
PAYMENT_PROVIDER=fake или yookassa
RUN_BOT=true
RUN_SCHEDULER=true
```

## 4. Приемка

```bash
./acceptance.sh
```

Если приемка проходит — можно тестировать бот в Telegram.
