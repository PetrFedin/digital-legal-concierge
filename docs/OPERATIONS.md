# Эксплуатация сервиса

## Основные URL

```text
GET  /health
GET  /runtime/snapshot
GET  /admin-ui
GET  /admin/dashboard
GET  /admin/cases
GET  /admin/queue
GET  /admin/settings
GET  /admin/lawyers
GET  /admin/notifications
POST /admin/scheduler/run-once
```

Все `/admin/*` требуют заголовок:

```text
x-admin-token: <ADMIN_API_TOKEN>
```

## Ежедневная проверка

1. Открыть `/admin-ui`.
2. Проверить новые дела.
3. Проверить очередь без юриста.
4. Проверить ожидающие оплаты.
5. Проверить уведомления.
6. Запустить scheduler вручную при необходимости.

## Что делает scheduler

- напоминает о неоплаченных платежах;
- освобождает неоплаченные слоты консультаций;
- проверяет 30 дней после претензии;
- создает служебные уведомления.

## Где менять тарифы

Админка → Настройки:

- `payments.m1_initial_payment`
- `payments.m1_court_payment`
- `payments.m1_success_fee_percent`
- `payments.m2_consultation_payment`

## Типовая проблема

### Бот не стартует

Проверить `.env`:

```env
RUN_BOT=true
BOT_TOKEN=<реальный токен>
```

Запустить диагностику:

```bash
python scripts/doctor.py
```
