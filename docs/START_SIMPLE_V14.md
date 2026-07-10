# Быстрый запуск Telegram-бота v14

## Самый простой запуск

```bash
unzip digital-legal-concierge-telegram-bot_v14_real_ops.zip
cd tg_ready_v14
./run.sh
```

Скрипт сам:

1. создаст виртуальное окружение;
2. установит зависимости;
3. создаст `.env`;
4. проверит настройки;
5. создаст базу;
6. запустит backend, админку, scheduler и Telegram-бота, если `RUN_BOT=true`.

## Где что открывать

```text
Операторская страница: http://localhost:8000/operator
Админка:             http://localhost:8000/admin-ui
Проверка health:     http://localhost:8000/health
Проверка ready:      http://localhost:8000/ready
```

## Управление без команд

```bash
./bot-control.sh
```

В меню можно:

- настроить `.env`;
- проверить готовность;
- инициализировать базу;
- запустить сервис;
- сделать полную проверку;
- создать backup.

## Реальный Telegram-бот

1. Откройте BotFather.
2. Создайте бота.
3. Скопируйте `BOT_TOKEN`.
4. Запустите:

```bash
./bot-control.sh
```

Выберите пункт `1` и вставьте токен. Для запуска бота установите:

```text
RUN_BOT=true
```

## Реальные платежи

По умолчанию стоит тестовый режим:

```text
PAYMENT_PROVIDER=fake
```

Для реальных платежей:

```text
PAYMENT_PROVIDER=yookassa
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
PUBLIC_BASE_URL=https://ваш-домен.ru
```

Webhook YooKassa:

```text
https://ваш-домен.ru/webhooks/payments/yookassa
```

## Что важно перед боевым запуском

```bash
python scripts/full_check_v14.py
```

Если проверка зеленая — можно запускать. Если красная — исправить указанное сообщение. Магии нет, только дисциплина. Так оно надежнее.
