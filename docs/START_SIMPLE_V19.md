# Быстрый запуск v19

Версия v19 сделана под простую эксплуатацию Telegram-бота: один запуск, понятные проверки, ссылки для оператора и администратора.

## 1. Распаковать

```bash
unzip digital-legal-concierge-telegram-bot_v19_launch_assistant.zip
cd tg_ready_v19
```

## 2. Запустить

```bash
./run.sh
```

Скрипт сам:

- создаст виртуальное окружение;
- установит зависимости;
- создаст `.env`, если его нет;
- проверит настройки;
- создаст базу данных;
- запустит FastAPI, админку и Telegram-бота, если `RUN_BOT=true`.

## 3. Открыть страницы

- Launch Assistant: `http://localhost:8000/launch-assistant`
- Операторская панель: `http://localhost:8000/operator`
- Админка: `http://localhost:8000/admin-ui`
- Карта сценариев: `http://localhost:8000/scenario-map-ui`
- Готовность: `http://localhost:8000/ready`

## 4. Для реального Telegram

В `.env` заполнить:

```env
BOT_TOKEN=токен_от_BotFather
RUN_BOT=true
ADMIN_API_TOKEN=случайный_длинный_токен
ADMIN_PASSWORD=сложный_пароль
PAYMENT_WEBHOOK_SECRET=отдельный_секрет
PUBLIC_BASE_URL=https://ваш-домен.ru
```

После этого снова:

```bash
./run.sh
```

## 5. Проверка

```bash
python scripts/preflight_v19.py
python scripts/full_check_v19.py
```

Отчеты появятся в папке `reports/`.
