# Простой запуск v29

## 1. Распаковать

```bash
unzip digital-legal-concierge-telegram-bot_v29_final_handover.zip
cd tg_ready_v29
```

## 2. Запустить

```bash
./run.sh
```

## 3. Открыть страницы

- Final Handover: http://localhost:8000/final-handover/ui
- Operator: http://localhost:8000/operator
- Admin: http://localhost:8000/admin-ui
- Ready: http://localhost:8000/ready

## 4. Для реального Telegram

Заполнить `.env`:

```env
BOT_TOKEN=...
RUN_BOT=true
PUBLIC_BASE_URL=https://your-domain.ru
ADMIN_USERNAME=admin
ADMIN_PASSWORD=strong-password
PAYMENT_PROVIDER=fake
```

Для боевых оплат заменить `PAYMENT_PROVIDER=fake` на реального провайдера и заполнить ключи.
