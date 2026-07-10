# Развертывание Telegram-бота

## 1. Создать бота

1. Откройте Telegram.
2. Найдите `@BotFather`.
3. Выполните `/newbot`.
4. Скопируйте токен.

## 2. Локальный запуск

```bash
cp .env.example .env
```

В `.env` укажите:

```env
BOT_TOKEN=ваш_токен
RUN_BOT=true
RUN_SCHEDULER=true
ADMIN_API_TOKEN=dev-admin-token
```

Далее:

```bash
pip install -e .
python scripts/init_db.py
python -m app.main
```

Бот работает в polling-режиме. Webhook для Telegram не нужен.

## 3. Проверка API

```bash
curl http://localhost:8000/health
curl -H "x-admin-token: dev-admin-token" http://localhost:8000/admin/dashboard
curl -H "x-admin-token: dev-admin-token" http://localhost:8000/runtime/snapshot
```

## 4. Проверка полного бизнес-пути без Telegram

```bash
python scripts/e2e_smoke.py
```

Скрипт создает тестовые M1 и M2 обращения, расчет, документы, платежи и автопереходы статусов.

## 5. Что редактируется без кода

Через `/admin/settings` можно менять:

- первый платеж М1;
- второй платеж М1;
- success fee %;
- стоимость консультации М2;
- срок ожидания после претензии;
- время удержания слота консультации.

Пример:

```bash
curl -X POST \
  -H "x-admin-token: dev-admin-token" \
  -H "Content-Type: application/json" \
  -d '{"value": 45000}' \
  http://localhost:8000/admin/settings/payments.m1_initial_payment
```

## 6. Минимальный сценарий клиента

Главное меню:

- 🏠 Главная
- 🧮 Рассчитать неустойку
- 📁 Мое дело
- 📄 Документы
- 💬 Связаться с юристом

Маршрут M1:

Расчет → документы → проверка → договор → первый платеж → доверенность → претензия → суд → второй платеж → исполнение → success fee → закрытие.

Маршрут M2:

Описание ситуации → документы при наличии → выбор слота → оплата консультации → консультация → перевод в M1 или закрытие.
