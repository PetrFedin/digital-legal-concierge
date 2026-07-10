# Telegram Bot v8 — production hardening

Версия v8 усиливает бот именно как Telegram-сервис, а не как dev-заготовку.

## Что добавлено

1. **Хранилище документов**
   - файлы из Telegram скачиваются в `STORAGE_DIR`;
   - документы раскладываются по `storage/cases/<case_id>/`;
   - в базе хранится путь к сохраненному файлу;
   - fallback на Telegram file_id сохранен для локальных тестов.

2. **Безопасный webhook платежей**
   - добавлена HMAC-подпись `x-payment-signature`;
   - секрет задается через `PAYMENT_WEBHOOK_SECRET`;
   - в `APP_ENV=local` webhook допускает запрос без подписи для разработки.

3. **Админские операции для эксплуатации**
   - карточка дела `/admin/cases/{case_id}`;
   - ручная смена статуса `/admin/cases/{case_id}/status`;
   - ручное подтверждение оплаты `/admin/payments/{payment_id}/confirm`;
   - список оплат `/admin/payments`;
   - список документов `/admin/documents`;
   - справочник статусов `/admin/statuses`.

4. **Полный E2E M1/M2**
   - `scripts/e2e_m1_m2_full.py` проверяет полный M1 до закрытия и M2 с переводом в M1.

## Минимальный запуск

```bash
cp .env.example .env
# вставить BOT_TOKEN и сменить ADMIN_API_TOKEN
pip install -e .
python scripts/init_db.py
python scripts/e2e_m1_m2_full.py
RUN_BOT=true RUN_SCHEDULER=true python -m app.main
```

## Админка

```text
http://localhost:8000/admin-ui
```

## Проверка webhook подписи

```bash
BODY='{"payment_id":1,"status":"paid"}'
SIGN=$(python - <<PY
import hmac,hashlib,os
body=b'{"payment_id":1,"status":"paid"}'
secret=os.getenv('PAYMENT_WEBHOOK_SECRET','dev-payment-secret').encode()
print(hmac.new(secret, body, hashlib.sha256).hexdigest())
PY
)
curl -X POST http://localhost:8000/webhooks/payments/fake \
  -H "content-type: application/json" \
  -H "x-payment-signature: $SIGN" \
  -d "$BODY"
```

## Что еще останется после v8

- подключить реальную YooKassa/CloudPayments вместо fake-provider;
- добавить полноценный login/password в web-admin;
- подключить S3/MinIO вместо локального storage;
- включить боевой бэкап базы и документов.
