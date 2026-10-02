# START HERE — Digital Legal Concierge

**Current delivery baseline:** 2026-10-02.

Do not start from historical `START_SIMPLE_V*`, `WHAT_IS_READY_V*` or `FINAL_HANDOVER_V*` files. Their role is historical evidence only.

## Authoritative reading order

1. `docs/CANONICAL_PRODUCT_SPEC_2026-10-02.md` — frozen customer/product contract.
2. `docs/PRODUCT_SCOPE_CURRENT.md` — current product implementation boundary.
3. `docs/TECHNICAL_ARCHITECTURE_CURRENT.md` — backend/database/runtime architecture.
4. `docs/SYSTEM_CONTRACT_CURRENT.md` — system invariants and authority ownership.
5. `docs/ACCEPTANCE_CURRENT.md` — release acceptance contract.
6. `docs/RUNBOOK_CURRENT.md` — deployment/operations procedure.
7. `docs/PROCESS_MAP_CURRENT.md` — living implementation/debt/evidence inventory.
8. `docs/CUSTOMER_HANDOVER_CHECKLIST_2026-10-02.md` — фактический статус 20 пунктов передачи заказчику.\n9. `docs/AUTHORITY_MANIFEST.yml` — реестр авторитетных и исторических документов.

## Local engineering start

The supported runtime path is container-first.

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Health:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/ready
```

Local `.env` must contain a Telegram bot token only if the bot runtime is enabled. Local/test payment and scanner modes are not production acceptance.

## Production-like runtime

Production-like staging requires:

- PostgreSQL;
- Redis;
- ClamAV;
- the same immutable application image for web/bot runtime;
- persistent encrypted document storage;
- configured security/document/backup key domains;
- authenticated staff accounts;
- Telegram token when bot runtime is enabled;
- a production payment mechanism (`offline` or deliberately enabled YooKassa).

Use `docker-compose.timeweb.yml` and, for the split Telegram topology, `docker-compose.timeweb.split.yml`.

## Before handing to a customer

Never declare release readiness from source inspection alone. Freeze one candidate SHA and execute the order in `docs/ACCEPTANCE_CURRENT.md` / `docs/RUNBOOK_CURRENT.md`, including PostgreSQL/Redis/browser/Telegram/payment/document-security and backup→restore evidence.

The release decision is recorded against one exact SHA only.
