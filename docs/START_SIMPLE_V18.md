# Быстрый запуск v19

Версия v19 сделана как Telegram-бот с простым запуском и понятной эксплуатацией.

## 1. Запуск одной командой

```bash
./run.sh
```

Скрипт сам:

1. создаст виртуальное окружение;
2. установит зависимости;
3. создаст `.env`, если его нет;
4. спросит BOT_TOKEN;
5. сгенерирует ADMIN_API_TOKEN, ADMIN_PASSWORD и PAYMENT_WEBHOOK_SECRET;
6. инициализирует базу;
7. выполнит проверки;
8. запустит сервис.

## 2. Основные страницы

После запуска откройте:

```text
http://localhost:8000/operator
http://localhost:8000/admin-ui
http://localhost:8000/login
http://localhost:8000/ready
http://localhost:8000/security-check
http://localhost:8000/scenario-map-ui
http://localhost:8000/handover
```

## 3. Админка

Войти можно через:

```text
/login
```

Логин и пароль находятся в `.env`:

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=...
```

Для API-запросов используется:

```text
ADMIN_API_TOKEN=...
```

## 4. Telegram

Для настоящего запуска нужно:

1. создать бота через BotFather;
2. вставить токен в `.env`;
3. поставить `RUN_BOT=true`;
4. перезапустить `./run.sh`.

## 5. Управление

```bash
./bot-control.sh
```

В меню доступны запуск, проверка, backup, отчет готовности и security check.
