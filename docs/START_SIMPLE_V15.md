# Быстрый запуск v19

Версия v19 собрана как Telegram-бот, а не как проект для Cursor. Цель: распаковать, вставить токен BotFather и запустить.

## 1. Запуск одной командой

```bash
unzip digital-legal-concierge-telegram-bot_v19_final_launch.zip
cd tg_ready_v19
./run.sh
```

При первом запуске мастер создаст `.env`, попросит `BOT_TOKEN`, сгенерирует админский токен и подготовит базу.

## 2. Что открыть после запуска

- Операторская страница: `http://localhost:8000/operator`
- Админка: `http://localhost:8000/admin-ui`
- Проверка готовности: `http://localhost:8000/ready`
- Health: `http://localhost:8000/health`

## 3. Минимальная настройка Telegram

1. Открыть BotFather.
2. Создать бота.
3. Скопировать токен.
4. Вставить токен в мастер запуска или `.env`:

```env
BOT_TOKEN=123456:ABC...
RUN_BOT=true
```

## 4. Боевые платежи

Для локальных тестов используется `PAYMENT_PROVIDER=fake`.
Для реальных платежей:

```env
PAYMENT_PROVIDER=yookassa
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
PUBLIC_BASE_URL=https://ваш-домен.ru
```

Webhook YooKassa:

```text
https://ваш-домен.ru/webhooks/payments/yookassa
```

## 5. Управление

```bash
./bot-control.sh
```

В меню можно запустить, остановить, посмотреть статус, логи, сделать backup и выполнить проверки.
