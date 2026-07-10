# Быстрый запуск v21

Версия v21 добавляет операционный Task Center и страницу редактируемых настроек.

## Запуск

```bash
unzip digital-legal-concierge-telegram-bot_v21_task_settings.zip
cd tg_ready_v21
./run.sh
```

## Главные страницы

- `/health-center/ui` — здоровье сервиса.
- `/task-center/ui` — что требует внимания сегодня.
- `/settings-ui` — суммы, проценты и сроки без редактирования кода.
- `/admin-ui` — админка.
- `/operator` — операторская страница.
- `/scenario-map-ui` — карта экранов B-001—B-028.

## Что проверить после запуска

1. Открыть `/ready`.
2. Открыть `/task-center/ui`.
3. Открыть `/settings-ui` и проверить суммы.
4. Запустить Telegram-бота командой `/start`.
5. Пройти расчет, М1 или М2.
6. Проверить дело в `/admin-ui`.
