# Production-развертывание v19

## Рекомендуемый вариант

1. Сервер Ubuntu.
2. Python 3.11+.
3. Домен с HTTPS.
4. Reverse proxy Nginx.
5. Запуск через systemd.

## Файлы-шаблоны

В корне проекта есть:

- `systemd-legal-concierge-bot.service`
- `nginx-legal-concierge-bot.conf`

## Минимальные команды

```bash
sudo mkdir -p /opt/digital-legal-concierge-bot
sudo cp -r . /opt/digital-legal-concierge-bot
cd /opt/digital-legal-concierge-bot
./install.sh
cp .env.production.example .env
nano .env
python scripts/init_db.py
python scripts/preflight_v19.py
```

После настройки:

```bash
sudo cp systemd-legal-concierge-bot.service /etc/systemd/system/legal-concierge-bot.service
sudo systemctl daemon-reload
sudo systemctl enable legal-concierge-bot
sudo systemctl start legal-concierge-bot
sudo systemctl status legal-concierge-bot
```

## Что обязательно заменить

- `BOT_TOKEN`
- `ADMIN_API_TOKEN`
- `ADMIN_PASSWORD`
- `PAYMENT_WEBHOOK_SECRET`
- `PUBLIC_BASE_URL`
- платежные настройки YooKassa, если используются реальные платежи.

## Telegram webhook

Если используется webhook-режим и публичный HTTPS URL:

```bash
python scripts/register_telegram_webhook.py
```

Удалить webhook:

```bash
python scripts/delete_telegram_webhook.py
```

Для polling-режима отдельная регистрация webhook не нужна.
