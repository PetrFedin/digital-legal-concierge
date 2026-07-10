# Быстрый запуск v23

## 1. Запуск

```bash
./run.sh
```

После запуска откройте:

- http://localhost:8000/operator — операторская страница
- http://localhost:8000/health-center/ui — здоровье сервиса
- http://localhost:8000/task-center/ui — операционные задачи
- http://localhost:8000/settings-ui — суммы, проценты и сроки
- http://localhost:8000/message-center/ui — сообщения клиентов
- http://localhost:8000/audit-center/ui — журнал действий
- http://localhost:8000/admin-ui — админка

## 2. Что проверять первым

1. `/ready` — сервис готов.
2. `/launch-check` — все основные ссылки доступны.
3. Telegram-бот отвечает на `/start`.
4. В настройках заданы суммы М1/М2.
5. Оператор видит задачи, сообщения и журнал действий.

## 3. Рабочий день оператора

1. Открыть `/operator`.
2. Проверить `/task-center/ui`.
3. Проверить `/message-center/ui`.
4. Проверить `/admin-ui` — новые дела, документы, оплаты.
5. Проверить `/audit-center/ui`, если были ручные изменения.

