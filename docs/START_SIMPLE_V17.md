# Простой запуск Telegram-бота v19

## 1. Распаковать архив

```bash
unzip digital-legal-concierge-telegram-bot_v19_handover_ready.zip
cd tg_ready_v19
```

## 2. Запустить одной командой

```bash
./run.sh
```

Скрипт сам создаст виртуальное окружение, установит зависимости, создаст `.env`, проверит проект, инициализирует базу и запустит сервис.

## 3. Открыть страницы управления

- Операторская страница: `http://localhost:8000/operator`
- Передача проекта: `http://localhost:8000/handover`
- Админка: `http://localhost:8000/admin-ui`
- Готовность: `http://localhost:8000/ready`
- Карта экранов: `http://localhost:8000/scenario-map-ui`

## 4. Настроить реального бота

В `.env` нужно указать:

```env
BOT_TOKEN=токен_из_BotFather
RUN_BOT=true
RUN_SCHEDULER=true
ADMIN_API_TOKEN=длинный_секретный_токен
```

## 5. Проверить готовность

```bash
python scripts/launch_readiness_report.py
python scripts/full_check_v19.py
```

Если бот отвечает на `/start`, админка открывается, а `/ready` показывает `ok: true` — можно начинать рабочую проверку.
