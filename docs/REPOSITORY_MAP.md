# DIGITAL LEGAL CONCIERGE — КАРТА РЕПОЗИТОРИЯ

**Статус:** текущая карта поставки, 02.10.2026.

## С чего начинать

- `docs/START_HERE.md` — единая точка входа.
- `docs/CANONICAL_PRODUCT_SPEC_2026-10-02.md` — продуктовый контракт.
- `docs/TECHNICAL_ARCHITECTURE_CURRENT.md` — техническая архитектура.
- `docs/AUTHORITY_MANIFEST.yml` — какие документы авторитетны, а какие исторические.
- `docs/PROCESS_MAP_CURRENT.md` — living implementation/debt/evidence map.

## app/bot

Telegram-клиентский кабинет: Home, calculator preview/save/recovery, My Case, документы, платежи, сообщения, М1/М2, consultation flows, stale-callback/draft/navigation guards.

Durable бизнес-истина не хранится в bot handler/FSM.

## app/api

HTTP и внутренние staff surfaces:

- CRM / Workdesk администратора;
- lawyer workspace и консультации;
- document review/access;
- payment review/refund/webhooks;
- assignment/SLA;
- access/MFA/security/audit/backup/retention;
- monitoring/operations/recovery.

Файлы с `*_impl.py`, `*_guard.py` и compatibility-фасады могут сохраняться для контролируемой совместимости. Они не должны создавать параллельного владельца одного `(method, path)`; это проверяется architecture/runtime tests.

## app/domain

Основная бизнес-логика:

- `cases` — State Machine, CaseService, M1/M2, SLA, claim/enforcement/self-filing;
- `calculator` — расчет и versioned legal-rule evidence;
- `documents` — workflow/review/storage derivatives;
- `payments` — lifecycle, offline/provider reconciliation, refunds/review;
- `consultations` — slots/reservations/booking/result;
- `messages` — коммуникации;
- `notifications` — durable generation/delivery semantics;
- `assignment` — назначение и capacity;
- `retention` — legal hold / controlled deletion;
- `analytics` — продуктовые события;
- `statuses` — доменный словарь состояний.

## app/models

SQLAlchemy persistence model. Главные сущности:

`User, AdminUser, Lawyer, Case, Calculation, CalculationRuleRevision, Document, DocumentDerivative, Payment, PaymentEvent, PaymentWebhookEvent, Consultation, ConsultationSlot, Message, Notification, ConsentAcceptance, CaseRetentionRecord, SelfFilingPackage`.

Схема изменяется только через Alembic.

## app/security

- session/RBAC/MFA/revocation;
- audit integrity;
- malware scanning;
- document encryption/access/key rotation;
- secure file admission/quarantine;
- authenticated encrypted backup;
- security events and production readiness controls.

## app/storage.py

Единая защищенная storage boundary для Case-scoped encrypted files. Новые document storage keys переносимы между средами и не содержат host-specific абсолютный путь.

## app/scheduler

Singleton scheduler, background maintenance/reminders/security cleanup/document derivatives и durable notification delivery orchestration.

## app/db + migrations

Async SQLAlchemy engine/session bootstrap и Alembic chain. PostgreSQL — production data authority; SQLite — только development/test.

## app/admin, app/lawyer, app/system

Компактные legacy/current helper packages:

- `app/admin` — dashboard/case-detail/notification helpers;
- `app/lawyer` — lawyer decision helpers;
- `app/system` — persisted editable system settings.

Они не являются отдельными приложениями или отдельными источниками State Machine.

## scripts

Deployment, migration, backup/restore, production preflight, acceptance/evidence, architecture and runtime probes.

## tests

Тесты разделены на:

- быстрые source/SQLite/domain contracts;
- PostgreSQL migration/concurrency;
- Redis/Telegram runtime;
- Browser Staff E2E;
- live-required/evidence contracts;
- document security/ClamAV/OCR;
- backup/restore and production invariants.

SOURCE test не заменяет LIVE/UAT proof.

## .github/workflows

Release evidence: CI, reproducible dependencies/images, PostgreSQL concurrency, Telegram Runtime Contracts, Browser Staff E2E, Deployment Readiness и LIVE_REQUIRED.

## docs/source_specs

Замороженные исходные материалы заказчика:

- функциональная спецификация;
- UX/UI-спецификация;
- карта действий.

Они остаются бизнес-источниками и не заменяются историческими техническими handover-файлами.
