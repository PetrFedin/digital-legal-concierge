# Что готово в v24

## Добавлено в v24

- Search Center `/search-center/ui`.
- JSON API поиска `/search-center/status?q=...`.
- Поиск по делам, клиентам, документам, оплатам и сообщениям.
- Ссылка на Search Center добавлена в `/operator` и `/launch-check`.
- Обновлены инструкции запуска.

## Что уже было готово ранее

- Telegram-бот с маршрутами М1/М2.
- Калькулятор неустойки.
- Раздел «Мое дело».
- Документы, оплаты, консультации, сообщения.
- Админка, рабочее место юриста, настройки.
- Centers: health, diagnostic, recovery, install, task, message, audit, notification, backup.
- YooKassa как опциональная интеграция.
- Fake payments для demo/test.
- Backup, CSV export, launch assistant, handover pages.

## Что остается по приоритету

1. Полностью проверить реальный Telegram BOT_TOKEN на сервере.
2. Подключить production YooKassa/CloudPayments.
3. Настроить HTTPS/nginx/systemd.
4. Проверить загрузку файлов на реальном Telegram.
5. Настроить backup cron.
6. Провести тестовый путь М1 и М2 руками в Telegram.
