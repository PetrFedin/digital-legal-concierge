# Безопасность и доступ v19

## Что добавлено

1. Страница входа `/login`.
2. Cookie-сессия для браузера.
3. `ADMIN_USERNAME` и `ADMIN_PASSWORD` в `.env`.
4. `ADMIN_API_TOKEN` для API и админских запросов.
5. `/security-check` для быстрой проверки критичных настроек.
6. CSV-экспорт с token-query для локального режима.

## Что обязательно для production

Перед реальным запуском проверьте:

```text
ADMIN_API_TOKEN не равен dev-admin-token
PAYMENT_WEBHOOK_SECRET не равен dev-payment-secret
ADMIN_PASSWORD задан
BOT_TOKEN задан, если RUN_BOT=true
PUBLIC_BASE_URL ведет на реальный домен
PAYMENT_PROVIDER=yookassa для реальных оплат
```

## Рекомендация

Для локального режима можно оставить:

```text
ALLOW_TOKEN_QUERY=true
```

Для production лучше поставить:

```text
ALLOW_TOKEN_QUERY=false
```

Тогда CSV-экспорт будет доступен только через запросы с заголовком `x-admin-token`.
