# DIGITAL LEGAL CONCIERGE — TECHNICAL ARCHITECTURE CURRENT

**Frozen baseline:** 2026-10-02  
**Status:** AUTHORITATIVE TECHNICAL DELIVERY ARCHITECTURE

## 1. Runtime topology

Production/staging artifact: one immutable Docker image built from this repository.

Core runtime:

- Python 3.11;
- FastAPI HTTP/staff API;
- aiogram 3 Telegram bot;
- SQLAlchemy 2 async ORM;
- Alembic migrations;
- PostgreSQL 17 production authority;
- Redis 7.4 durable Telegram FSM/coordination;
- ClamAV sidecar for document malware admission;
- encrypted Case-scoped document storage;
- scheduler + durable notification dispatcher;
- optional YooKassa provider integration when deliberately enabled.

SQLite and in-memory FSM are test/development conveniences only.

## 2. Deployment shape

### Standard container contour

`client/staff/Telegram → application image → PostgreSQL + Redis + encrypted storage`.

### Current Timeweb split contour

Because Telegram reachability is asymmetric on the current host, production topology may split the same immutable image into:

- **web runtime** — FastAPI/staff/API, `RUN_BOT=false`, `RUN_SCHEDULER=false`;
- **bot runtime** — host-network Telegram polling + scheduler/notification delivery, `RUN_BOT=true`, `RUN_SCHEDULER=true`;
- PostgreSQL/Redis exposed only to the local host/runtime contour;
- ClamAV available to every upload ingress authority;
- both runtimes use the same database, storage, keyring and release SHA.

No second polling consumer is allowed.

## 3. Application layers

### Presentation

- `app/bot/*` — Telegram client cabinet;
- `app/api/*` — staff CRM, lawyer workspace, operations/security APIs and web surfaces.

Presentation code may request domain actions; it does not own legal/financial truth.

### Domain

- `app/domain/cases/*` — Case lifecycle, transition authority, SLA, assignment, M1/M2 services;
- `app/domain/documents/*` — document workflow/review/derivatives;
- `app/domain/payments/*` — payment lifecycle, provider/offline reconciliation;
- `app/domain/consultations/*` — slots/reservations/consultation lifecycle;
- `app/domain/notifications/*` — durable notification generation/delivery;
- `app/domain/retention/*` — legal hold and controlled deletion.

### Platform/security

- `app/security/*` — sessions/RBAC/MFA, document encryption/access, malware admission, audit integrity, backup/key rotation;
- `app/storage.py` — protected Case-scoped storage boundary;
- `app/scheduler/*` — singleton scheduler jobs and retry/maintenance execution;
- `app/db/*` — async database/session/migration bootstrap.

## 4. Business authority graph

### Case

`Case.status` is the only process-state authority.  
Transitions: `app/domain/cases/case_transition_policy.py`.  
Mutations: `CaseService` or dedicated domain services.

Every normal transition is explicit. Compatibility-only states are readable but cannot be re-entered by current writes. Forced recovery is privileged, narrow and audited.

### Calculation

Preview is transient Redis/FSM state. A durable Case/Calculation is created only by explicit save with an idempotent operation key.

Legal calculation rules are versioned/effective-dated evidence, not hidden constants.

### Document

`Document` = immutable accepted source/version evidence.  
`DocumentDerivative` = reproducible sanitized/OCR derivative; never a replacement source.

Admission order:

`incoming → ClamAV verdict → structural/type validation → SHA agreement → DLCENC2 envelope encryption → Document`.

Derivative order:

`VERIFIED encrypted source → pikepdf → encrypted sanitized derivative → usable-text test → OCRmyPDF only when needed → encrypted OCR derivative`.

### Payment

`Payment` = current projection.  
`PaymentEvent` = append-only normalized lifecycle.  
`PaymentWebhookEvent` = provider evidence/idempotency ledger.

Provider/webhook, staff reconciliation and stale-money handling all converge on the same domain lifecycle.

### Consultation

`ConsultationSlot` owns capacity/time.  
`Consultation` owns client/Case booking lifecycle.  
Reservation/payment/result operations lock/revalidate the exact slot/consultation context.

### Notification

Notification records are durable. Delivery/retry is separate from domain mutation; a send failure never rolls back or repeats an already committed legal/financial action.

## 5. Persistence model

PostgreSQL stores all durable legal, financial, role, audit and operational facts.

Important invariants:

- Client → Cases = one-to-many;
- Case has one current M1/M2 route/status at a time;
- Case → Calculations = one-to-many;
- document versions are append-only business records;
- normalized payment events are append-only;
- audit/history is not rewritten;
- one active provider payment attempt per controlled identity where required;
- consultation/slot uniqueness enforced at DB/domain boundaries;
- idempotent Telegram/provider operations use stable operation/event keys.

Alembic is the only schema evolution authority. Deployment requires one head and ORM/migration parity.

## 6. Transaction and concurrency model

- every stateful action operates inside an explicit async DB transaction;
- critical rows use `SELECT ... FOR UPDATE` or DB uniqueness as the concurrency backstop;
- stale UI/Telegram snapshots are rejected, not silently replayed;
- ORM values needed after commit/rollback are snapshotted before the boundary;
- provider/Telegram retry is idempotent by stable identifiers;
- PostgreSQL concurrency tests are mandatory release evidence; SQLite cannot prove race safety.

## 7. Telegram state model

Redis FSM stores transient wizard/draft/navigation state.

Persistent business state is never reconstructed from Redis alone.

Rules:

- callback mutations carry exact Case/domain provenance;
- multi-Case ambiguity fails closed;
- Back only replays read/idempotent screens;
- process restart may lose transient UI state but must recover from PostgreSQL Home/My Case safely;
- polling is protected by singleton lease.

## 8. Staff authorization

Roles are enforced server-side:

- admin/operator;
- lawyer with Case responsibility checks;
- superadmin/leadership with stronger MFA/session requirements.

UI visibility is convenience only. Every protected endpoint independently validates current account, roles, session version/revocation and Case responsibility.

Runtime contract: one HTTP method/path has one route owner. Import/include order must not define authorization.

## 9. Document security

- ClamAV fail-closed in production;
- content/type/size validation;
- independent SHA agreement;
- per-document envelope encryption;
- Case-scoped portable storage keys;
- one-time protected download grants;
- key rotation without unnecessary ciphertext rewrite;
- quarantine never retains rejected plaintext;
- legal hold/retention includes source and derivatives;
- backup/restore revalidates storage identity and decryption.

## 10. Backup / restore

Authenticated encrypted backups include PostgreSQL evidence plus protected storage under the runbook contract.

Release requires:

`pre-backup witness → encrypted backup → independent verification → restore to empty staging DB/storage → Alembic/audit/document checks → same image runtime smoke → normal authorized historical document read`.

A backup file existing is not acceptance.

## 11. Observability

Mandatory operational signals:

- `/health` liveness;
- `/ready` dependency/readiness contract;
- scheduler heartbeat/job failures;
- notification backlog/retry failures;
- Telegram polling/sending failure;
- DB/Redis availability;
- payment webhook/review/refund backlog;
- document scanner/storage capacity;
- backup freshness/restore readiness;
- security/audit events.

Secrets and document plaintext are excluded from logs.

## 12. Release gates

One frozen SHA must pass, on that same SHA:

- compile/architecture/Alembic/ORM;
- focused domain proofs;
- PostgreSQL migrations + backup/restore drill;
- PostgreSQL concurrency;
- Redis/Telegram runtime;
- browser staff E2E;
- document malware/OCR runtime gates;
- locked/reproducible image differential;
- real Telegram/customer UAT personas;
- post-live backup→restore;
- provider/offline payment evidence for the mechanism actually enabled.

No gate is promoted from source inspection to runtime PASS without execution.
