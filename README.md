# Digital Legal Concierge Telegram Bot v29

Telegram-бот для сопровождения клиентов по взысканию неустойки по ДДУ 214-ФЗ.

## Быстрый запуск

```bash
./run.sh
```

## Управление

```bash
./bot-control.sh
```

## Главные страницы

- http://localhost:8000/final-handover/ui
- http://localhost:8000/final-qa/ui
- http://localhost:8000/go-live/ui
- http://localhost:8000/production-center/ui
- http://localhost:8000/operator
- http://localhost:8000/admin-ui
- http://localhost:8000/ready

## Проверка

```bash
python scripts/full_check_v29.py
./acceptance.sh
```

## Перед реальным запуском

Заполните `.env`: BOT_TOKEN, PUBLIC_BASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD, платежные ключи и рабочие настройки.


## v30 Maintenance Ready

Главная страница эксплуатации: `http://localhost:8000/maintenance-center/ui`

Быстрый запуск:

```bash
./run.sh
```

Полная приемка:

```bash
./acceptance.sh
python scripts/full_check_v30.py
```
