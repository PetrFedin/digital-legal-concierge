# Быстрый запуск v19

## Самый простой запуск

```bash
unzip digital-legal-concierge-telegram-bot_v19_ops_ready.zip
cd tg_ready_v19
./run.sh
```

После запуска открыть:

```text
http://localhost:8000/operator
http://localhost:8000/admin-ui
http://localhost:8000/scenario-map-ui
http://localhost:8000/ops-guide
```

## Проверка перед показом

```bash
python scripts/full_check_v19.py
```

Проверка включает компиляцию, создание БД, smoke E2E и полный E2E М1/М2.

## Что важно заполнить в .env

```env
BOT_TOKEN=токен_бота_из_BotFather
RUN_BOT=true
RUN_SCHEDULER=true
ADMIN_API_TOKEN=сложный_секретный_токен
PAYMENT_WEBHOOK_SECRET=сложный_секрет_webhook
PAYMENT_PROVIDER=fake
```

Для реальной оплаты:

```env
PAYMENT_PROVIDER=yookassa
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
PUBLIC_BASE_URL=https://ваш-домен.ru
```
