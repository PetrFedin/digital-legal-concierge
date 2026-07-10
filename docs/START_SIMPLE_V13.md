# Быстрый запуск Telegram-бота v13

## 1. Распаковать архив

```bash
unzip digital-legal-concierge-telegram-bot_v13_operator_plus.zip
cd tg_ready_v13
```

## 2. Запустить одной командой

```bash
./run.sh
```

При первом запуске скрипт создаст `.env`, установит зависимости, проверит проект, создаст базу и запустит сервис.

## 3. Где смотреть

- Операторская страница: http://localhost:8000/operator
- Админка: http://localhost:8000/admin-ui
- Health: http://localhost:8000/health
- Ready: http://localhost:8000/ready

## 4. Управление без запоминания команд

```bash
./bot-control.sh
```

## 5. Полная проверка

```bash
./bot-control.sh full
```

## 6. Включение Telegram-бота

В `.env` должны быть заполнены:

```env
BOT_TOKEN=токен_от_BotFather
RUN_BOT=true
RUN_SCHEDULER=true
ADMIN_API_TOKEN=сложный_секретный_токен
PUBLIC_BASE_URL=https://ваш-домен
```

Для локального теста можно оставить `PAYMENT_PROVIDER=fake`.
Для приема реальных платежей подключается боевой платежный провайдер.
