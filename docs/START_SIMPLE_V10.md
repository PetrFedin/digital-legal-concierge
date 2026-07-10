# Запуск Telegram-бота v10

Цель версии v10 — запуск без Cursor и без ручного ковыряния в коде.

## Самый простой запуск

```bash
unzip digital-legal-concierge-telegram-bot_v10_operator_ready.zip
cd tg_ready_v10
./run.sh
```

При первом запуске мастер спросит:

1. `BOT_TOKEN` от BotFather.
2. Запускать ли Telegram-бота.
3. Запускать ли scheduler.
4. Публичный URL сервиса.
5. Платежный режим: `fake` или `yookassa`.

Для локальной проверки можно оставить `PAYMENT_PROVIDER=fake`.

## Что откроется

- Telegram-бот — если указан `BOT_TOKEN` и `RUN_BOT=true`.
- Backend — `http://localhost:8000`.
- Админка — `http://localhost:8000/admin-ui`.
- Проверка — `http://localhost:8000/ready`.

## Проверка после запуска

В другом терминале:

```bash
./status.sh
```

## Тестовая оплата

В режиме `fake` бот создает ссылку вида:

```text
http://localhost:8000/webhooks/payments/fake-pay/<payment_id>
```

Откройте ее в браузере и нажмите «Подтвердить оплату». После этого статус дела изменится автоматически.

## Docker-запуск

```bash
./docker-start.sh
```

## Backup

```bash
./backup.sh
```

## Restore

```bash
./restore.sh backups/legal_bot_backup_YYYYMMDD_HHMMSS.tar.gz
```

## Где менять суммы

Суммы и проценты вынесены в системные настройки:

- первый платеж М1;
- второй платеж М1;
- success fee %;
- стоимость консультации М2;
- срок удержания слота;
- срок ожидания после претензии.

Идея простая: суммы не должны жить в коде, потому что код не касса и не приказ по компании.
