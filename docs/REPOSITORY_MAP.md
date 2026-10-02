# Карта репозитория — final MVP

## Канонические документы

- `docs/CANONICAL_MVP_SPEC_2026-10-03.md` — замороженный продуктовый контракт М1/М2.
- `docs/FINAL_ARCHITECTURE_2026-10-03.md` — production architecture.
- `docs/FINAL_CUSTOMER_HANDOVER_CHECKLIST_2026-10-03.md` — фактический план передачи.
- `docs/START_HERE.md` — запуск.
- `source_documents/1 Функциональная_спецификация_MVP_финальная_версия.docx` и `source_documents/1 UX_UI_спецификация_Telegram_бот_финальная_версия.docx` — финальные утверждённые исходники требований.\n- `docs/source_specs/` — рабочие/исторические копии и дополнительные материалы.

Versioned `WHAT_IS_READY_V*`, `HANDOVER_V*`, `FINAL_HANDOVER_V*` и исследовательские документы сохраняются как история и не расширяют frozen MVP.

## app/bot

Telegram-интерфейс: меню, калькулятор, документы, оплаты, консультация, сообщения, «Моё дело», recovery/stale callback handling.

## app/domain

Единая бизнес-логика: CaseService/State Machine, M1/M2, калькулятор, документы, оплаты, консультации, уведомления, SLA/retention.

## app/admin

Административный API и рабочие поверхности: очередь дел, назначения, документы, оплаты, консультации, audit/security/backup controls.

## app/lawyer

Рабочее место юриста: назначенные дела, document review, консультации, решения и переходы по разрешённому графу.

## app/api

HTTP API: health/readiness, auth/staff endpoints, payment webhook, document delivery, operational endpoints.

## app/security

Сессии/MFA, audit integrity, encrypted documents, access grants, backup/restore, retention/key rotation.

## app/scheduler

Reminders, slot expiration, consultation/deadline checks, SLA, retention/security cleanup, encrypted backup, notification delivery.

## app/models + migrations

SQLAlchemy ORM + Alembic. Production source of truth — PostgreSQL, schema authority — Alembic head.

## scripts

Инициализация/миграции, architecture checks, acceptance/smoke, backup/restore/deployment utilities.

## Docker/ops

- `Dockerfile`;
- `Dockerfile.test`;
- `docker-compose.yml`;
- `docker-compose.timeweb.yml`;
- `.env.production.example`;
- `timeweb-deploy.sh`, `status.sh`, `acceptance.sh`.

## Post-MVP

`docs/DIGITAL_LEGAL_CONCIERGE_INTEGRATION_MASTER_PLAN_2026-10-01.md` — отдельный post-MVP backlog. Он не является обязательным scope текущей сдачи.
