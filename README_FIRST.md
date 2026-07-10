# Digital Legal Concierge - запуск за 3 шага

## Что нужно один раз

1. Установить Docker Desktop для Mac или Windows.
2. Запустить Docker Desktop и дождаться статуса Engine running.
3. Дважды нажать `START_BOT.command` на Mac. На Windows открыть терминал в папке и выполнить `docker compose up -d --build`.

После сообщения об успешном запуске откройте Telegram, найдите бота и отправьте `/start`.

## Управление

- `START_BOT.command` - запустить или обновить.
- `STOP_BOT.command` - остановить.
- `BOT_STATUS.command` - посмотреть состояние и логи.
- Админка: http://localhost:8000/admin-ui
- Проверка сервиса: http://localhost:8000/health

## Важное

Текущая оплата работает в тестовом режиме (`PAYMENT_PROVIDER=fake`). Реальные платежи не принимаются.
Перед публичным запуском смените токен Telegram, пароль администратора и секреты в `.env`.
