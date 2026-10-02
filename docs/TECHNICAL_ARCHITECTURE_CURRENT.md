# DIGITAL LEGAL CONCIERGE — ТЕХНИЧЕСКАЯ АРХИТЕКТУРА

**Зафиксированная базовая версия:** 02.10.2026  
**Статус:** АВТОРИТЕТНАЯ ТЕХНИЧЕСКАЯ АРХИТЕКТУРА ДЛЯ ПЕРЕДАЧИ

## 1. Runtime-стек

Staging и production используют один immutable Docker image из этого репозитория.

Основной стек:

- Python 3.11;
- FastAPI — HTTP/API/staff web;
- aiogram 3 — Telegram-бот;
- SQLAlchemy 2 async ORM;
- Alembic — единственный источник схемных миграций;
- PostgreSQL 17 — основной production data authority;
- Redis 7.4 — долговременный Telegram FSM/coordination;
- ClamAV sidecar — malware admission документов;
- зашифрованное Case-scoped файловое хранилище;
- scheduler + durable notification dispatcher;
- YooKassa — опциональный provider, только при осознанном включении.

SQLite и memory FSM допустимы только для разработки/тестов.

## 2. Топология развертывания

### Стандартный контур

`client/staff/Telegram → application image → PostgreSQL + Redis + encrypted storage`.

### Текущий Timeweb split-контур

Из-за фактически выявленной асимметрии доступа Telegram на текущем хосте допустим split одного и того же immutable image:

- **web runtime** — FastAPI/staff/API, `RUN_BOT=false`, `RUN_SCHEDULER=false`;
- **bot runtime** — host-network Telegram polling + scheduler/notification delivery, `RUN_BOT=true`, `RUN_SCHEDULER=true`;
- PostgreSQL и Redis доступны только внутри локального runtime-контура;
- ClamAV доступен всем входам загрузки документов;
- обе роли работают с одной БД, storage, keyring и одним release SHA.

Второй polling consumer запрещен.

## 3. Слои приложения

### Presentation

- `app/bot/*` — Telegram-клиентский кабинет;
- `app/api/*` — CRM, рабочее место юриста, operations/security API и web surfaces.

Presentation инициирует доменные команды, но не является источником юридической или финансовой истины.

### Domain

- `app/domain/cases/*` — Case lifecycle, transition authority, SLA, assignment, М1/М2;
- `app/domain/documents/*` — document workflow/review/derivatives;
- `app/domain/payments/*` — payment lifecycle, provider/offline reconciliation;
- `app/domain/consultations/*` — slots/reservations/consultation lifecycle;
- `app/domain/notifications/*` — durable notification generation/delivery;
- `app/domain/retention/*` — legal hold и controlled deletion.

### Platform / security

- `app/security/*` — sessions/RBAC/MFA, encryption/access, malware admission, audit integrity, backup/key rotation;
- `app/storage.py` — защищенная Case-scoped storage boundary;
- `app/scheduler/*` — singleton jobs, retry и maintenance;
- `app/db/*` — async database/session/migration bootstrap.

## 4. Граф источников истины

### Case

`Case.status` — единственный process-state authority.  
Граф переходов: `app/domain/cases/case_transition_policy.py`.  
Запись: `CaseService` и профильные domain services.

Каждый нормальный переход задан явно. Compatibility-only состояния читаются, но не создаются заново. Forced recovery — привилегированный, узкий и аудируемый механизм.

### Calculation

Preview живет только в Redis/FSM. Durable Case/Calculation создается после явного сохранения с idempotency operation key.

Правовые расчетные правила должны иметь version/effective-date evidence и не сводятся к скрытым константам.

### Document

`Document` — неизменяемое принятое source/version evidence.  
`DocumentDerivative` — воспроизводимый sanitized/OCR derivative, но не новый source.

Admission:

`incoming → ClamAV → structural/type validation → SHA agreement → DLCENC2 envelope encryption → Document`.

Derivative:

`VERIFIED encrypted source → pikepdf → encrypted sanitized derivative → text check → OCRmyPDF только при отсутствии usable text → encrypted OCR derivative`.

### Payment

`Payment` — current projection.  
`PaymentEvent` — append-only normalized lifecycle.  
`PaymentWebhookEvent` — provider evidence / idempotency ledger.

Provider webhook, ручная сверка и обработка stale-money сходятся в единую domain lifecycle.

### Consultation

`ConsultationSlot` владеет временем/емкостью.  
`Consultation` владеет Case/client booking lifecycle.  
Reservation/payment/result всегда блокируют и повторно валидируют точный slot/consultation context.

### Notification

Notification хранится долговременно. Delivery/retry отделены от бизнес-мутации: ошибка отправки не откатывает и не повторяет уже подтвержденное юридическое/финансовое действие.

## 5. Persistence

PostgreSQL хранит все долговременные юридические, финансовые, ролевые, аудиторские и операционные факты.

Ключевые инварианты:

- Client → Cases = one-to-many;
- у одного Case один текущий маршрут М1/М2 и один status;
- Case → Calculations = one-to-many;
- версии документов — append-only business records;
- PaymentEvent — append-only;
- audit/history не переписывается;
- provider payment identities и активные попытки защищены DB/domain constraints;
- slot/consultation uniqueness защищена на DB/domain boundary;
- Telegram/provider retries используют стабильные operation/event keys.

Alembic — единственный источник изменения схемы. Перед развертыванием требуется одна head revision и ORM/migration parity.

## 6. Транзакции и concurrency

- stateful-действие выполняется в явной async DB transaction;
- критичные записи блокируются через `SELECT ... FOR UPDATE` либо защищаются DB uniqueness;
- stale UI/Telegram snapshot отклоняется, а не применяется молча;
- значения ORM, нужные после commit/rollback, snapshot-ятся заранее;
- retries Telegram/provider идемпотентны;
- race-safety доказывается PostgreSQL concurrency tests; SQLite для этого недостаточен.

## 7. Telegram state model

Redis FSM хранит transient wizard/draft/navigation state.

Durable бизнес-состояние не восстанавливается только из Redis.

Правила:

- mutation callback несет точную Case/domain provenance;
- multi-Case неоднозначность fail-closed;
- Back воспроизводит только read/idempotent screen;
- после рестарта transient UI может быть утерян, но Home/My Case восстанавливаются из PostgreSQL без выдумывания мутации;
- polling защищен singleton lease.

## 8. Staff authorization

Права проверяются на сервере:

- admin/operator;
- lawyer + Case responsibility;
- superadmin/leadership + усиленная MFA/session policy.

Скрытая кнопка не является security boundary. Каждый endpoint валидирует актуальный account, roles, session version/revocation и Case responsibility.

Runtime-инвариант: одна пара HTTP method/path — один route owner. Порядок include/import не определяет авторизацию.

## 9. Document security

- ClamAV fail-closed в production;
- type/content/size validation;
- независимая SHA agreement;
- per-document envelope encryption;
- переносимые Case-scoped storage keys;
- one-time protected download grants;
- key rotation без лишней перезаписи ciphertext;
- quarantine не хранит rejected plaintext;
- legal hold/retention охватывает source и derivatives;
- restore заново проверяет storage identity и decryption.

## 10. Backup / restore

Аутентифицированный encrypted backup включает PostgreSQL evidence и защищенный storage по runbook-контракту.

Релизная проверка:

`pre-backup witness → encrypted backup → independent verification → restore в пустую staging DB/storage → Alembic/audit/document checks → запуск того же image → обычное авторизованное чтение исторического документа`.

Наличие файла backup само по себе не является приемкой.

## 11. Observability

Минимальный обязательный набор:

- `/health` — liveness;
- `/ready` — readiness зависимостей;
- scheduler heartbeat / job failures;
- notification backlog / retry failures;
- Telegram polling/sending failures;
- PostgreSQL / Redis availability;
- payment webhook / review / refund backlog;
- document scanner / storage capacity;
- backup freshness / restore readiness;
- security/audit events.

Секреты и plaintext документов не пишутся в логи.

## 12. Release gates

Один frozen SHA должен на одном и том же коде пройти:

- compile / architecture / Alembic / ORM;
- focused domain proofs;
- PostgreSQL migrations + backup/restore drill;
- PostgreSQL concurrency;
- Redis/Telegram runtime;
- browser staff E2E;
- malware/OCR runtime gates;
- locked/reproducible image differential;
- реальные Telegram/customer UAT personas;
- post-live backup→restore;
- provider/offline payment evidence для реально включенного механизма.

Source inspection не повышается до runtime PASS без фактического выполнения.
