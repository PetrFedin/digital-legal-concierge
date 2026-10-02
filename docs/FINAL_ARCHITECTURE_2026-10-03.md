# Digital Legal Concierge — финальная техническая архитектура MVP

**Статус:** FROZEN FOR HANDOVER  
**Дата:** 2026-10-03

## 1. Цель архитектуры

Довести существующий MVP до эксплуатации без смены технологического стека и без внедрения новых платформ. Архитектура подчинена двум маршрутам М1/М2 и канонической спецификации `CANONICAL_MVP_SPEC_2026-10-03.md`.

## 2. Production topology

```text
Telegram Client
      |
Telegram Bot / aiogram
      |
      +------------------------+
      |                        |
 FastAPI application       Redis FSM
      |
      +----------+------------+------------------+
      |          |            |                  |
 PostgreSQL  Encrypted    YooKassa          Scheduler
             storage      API/webhook       /outbox
      |          |            |                  |
      +----------+------------+------------------+
                       |
                 Staff web UI
              Admin / Lawyer / Ops
```

Reverse proxy/TLS находится перед FastAPI. Сам application container публикуется только на loopback/private ingress.

## 3. Authority boundaries

### Case authority

Единственный владелец бизнес-переходов дела:

- `CaseService`;
- `case_transition_policy.py`;
- `CaseStatus`.

UI, webhook, scheduler и админские actions не должны напрямую придумывать новые статусы.

### Database authority

- PostgreSQL — production source of truth.
- Alembic migrations — schema authority.
- ORM обязан соответствовать Alembic head.
- SQLite используется для bounded development/tests, но не как production DB.

### Telegram conversational state

- Redis FSM — production authority незавершённых пользовательских шагов.
- Memory FSM запрещён в production.
- Состояние дела в PostgreSQL важнее transient Telegram FSM.

### Document authority

- metadata/version/review state — PostgreSQL;
- encrypted bytes — persistent `STORAGE_DIR`;
- access — application ACL + one-time/short-lived grant;
- backup — согласованный encrypted bundle БД + storage.

### Payment authority

- business payment record — PostgreSQL;
- фактическое внешнее подтверждение — provider API/webhook;
- `payment_webhook_events` — idempotency/replay ledger;
- UI не может самостоятельно пометить реальный provider payment как оплаченный без разрешённого domain action.

### Consultation authority

- slots/holds/bookings/outcomes — PostgreSQL;
- Telegram UI только показывает и вызывает разрешённые domain actions.

## 4. Runtime components

### Application

Один FastAPI application содержит:

- health/readiness;
- admin/operator UI;
- lawyer UI;
- payment webhook API;
- document delivery endpoints;
- security/audit/backup operational endpoints.

### Telegram worker

Aiogram использует тот же доменный слой и БД. Для одного BOT_TOKEN допускается один polling authority; singleton lease предотвращает параллельный consumer.

### Scheduler

Периодические задания:

- payment reminders;
- release expired consultation holds;
- consultation reminders;
- overdue consultation completion;
- case SLA;
- claim 30-day deadline;
- retention discovery;
- security cleanup/key migration;
- encrypted backup;
- notification delivery/retry.

Scheduler выполняет jobs независимо: ошибка одного job не должна превращать успешно сохранённый бизнес-факт в rollback другого job.

## 5. Database

Production target: PostgreSQL 17 (контейнер `postgres:17-alpine` в текущем Timeweb compose).

Миграционный порядок:

```text
empty PostgreSQL
→ alembic upgrade head
→ alembic current
→ alembic upgrade head (idempotency)
→ alembic check
→ application startup
```

Запрещено создавать production-таблицы вручную в обход Alembic.

## 6. Storage

Текущий MVP использует persistent filesystem volume, зашифрованный на уровне application envelope encryption.

Для customer handover это допустимый production contour при одновременном выполнении:

- storage mounted persistently;
- encrypted-at-rest application format;
- backup включает storage;
- restore drill подтверждён;
- свободное место контролируется.

S3/MinIO допускается после MVP как сменяемый storage adapter, но не является причиной откладывать передачу рабочего MVP.

## 7. Redis

Redis используется только как durable runtime state для Telegram FSM/locks, а не как system of record по делу.

Production параметры:

```text
FSM_STORAGE_BACKEND=redis
REDIS_URL=redis://redis:6379/0
```

Redis не публикуется во внешний интернет.

## 8. Payments

Production:

```text
PAYMENT_PROVIDER=yookassa
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
PAYMENT_WEBHOOK_SECRET=...
PUBLIC_BASE_URL=https://...
```

Go-live gate: реальный тестовый/минимальный платёж должен создать provider event, пройти webhook verification и ровно один допустимый business transition.

## 9. Security

Обязательные независимые secret domains:

- session signing;
- security HMAC;
- MFA encryption;
- audit integrity;
- document encryption;
- backup encryption.

Privileged UI защищается role/session/MFA policy. Default/demo secrets недопустимы в production.

## 10. Audit

Критические события фиксируются с actor, case, old/new state и временем. Audit chain защищается HMAC integrity. Audit не заменяет business tables и не используется как скрытый второй state store.

## 11. Backup and recovery

Production backup:

```text
PostgreSQL consistent dump + encrypted document storage
→ authenticated encrypted .dlcbak
→ manifest/checksum verification
```

Restore выполняется только в отдельный staging target. Go-live без хотя бы одного успешного restore drill запрещён.

## 12. Observability

Для MVP достаточно существующих:

- `/health`;
- `/ready`;
- structured application logs;
- Security Center;
- Audit Center;
- Backup Center;
- scheduler job outcomes.

Новый observability stack не добавляется перед handover, если эти сигналы позволяют однозначно определить отказ приложения, БД, Redis, backup и внешнего provider.

## 13. Deployment

Канонический production запуск:

```bash
cp .env.production.example .env
bash ./generate-secrets.sh
# заполнить provider/domain/credentials
bash ./timeweb-deploy.sh
COMPOSE_FILE=docker-compose.timeweb.yml bash ./status.sh
bash ./acceptance.sh
```

Перед переключением реальных клиентов `/ready` должен вернуть production-ready состояние без fake payment provider и без обязательных missing secrets.

## 14. Release environments

### Local/CI

- SQLite/PostgreSQL CI;
- fake providers допустимы;
- Memory FSM допустим только non-production.

### Staging

- PostgreSQL;
- Redis;
- persistent encrypted storage;
- HTTPS;
- реальный Telegram test bot;
- YooKassa test/controlled provider contour;
- production-like secrets.

### Production

Тот же application artefact/digest, который прошёл staging/UAT; меняются только production credentials/endpoints.

## 15. Что не внедряем перед сдачей

Перед handover не добавляются:

- второй workflow engine;
- отдельный клиентский web cabinet;
- OpenSearch;
- Docling/OCR platform;
- OPA/OpenFGA;
- external EDMS;
- новая CRM;
- новая BI/AI подсистема.

Они могут быть post-MVP change requests. Сейчас приоритет — зелёный exact release, staging, UAT, pilot, production.
