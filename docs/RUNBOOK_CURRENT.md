# RUNBOOK — CURRENT

Status: **authoritative current operations/release runbook** for the existing M1/M2 product.

This file does not expand product scope. It defines what must be operated and proven before exposing the existing M1/M2 product to real clients and real money. Acceptance criteria live in `docs/ACCEPTANCE_CURRENT.md`; exact post-LIVE evidence order lives in `docs/POST_LIVE_RELEASE_EVIDENCE.md`; automated matrix details live in `docs/LIVE_REQUIRED_RUNBOOK.md`; the maintained implementation/process/debt inventory lives in `docs/PROCESS_MAP_CURRENT.md`.

## Environment contract

Production-like staging and production use the same application/container artifact and migration chain. Runtime dependencies are:

- PostgreSQL;
- Redis for Telegram FSM and singleton/coordination mechanisms;
- Telegram bot account/token;
- YooKassa for the current online payment path, or explicitly disabled/offline mode;
- encrypted document storage and external document/audit keyrings;
- scheduler;
- staff authentication/MFA/session infrastructure;
- authenticated encrypted backup storage.

SQLite and in-memory FSM are development/test conveniences only. Persisted datetimes are UTC; outward presentation uses `BUSINESS_TIMEZONE` and `BUSINESS_TIMEZONE_LABEL`.

## Current infrastructure blocker

GitHub issue **#116** tracks the Actions runner/billing/spending blocker. Jobs that stop before runner allocation/steps are infrastructure failures, not application PASS/FAIL evidence.

Until #116 is resolved and required workflows actually execute, release status remains **BLOCKED_INFRA**. Do not repeatedly rerun the complete release matrix while the external blocker is unchanged.

## Ordered release procedure

For one frozen candidate SHA execute strictly in this order:

1. restore real Actions runner allocation;
2. execute full CI and required PR checks, including the living process-map maintenance contract;
3. execute dedicated PostgreSQL concurrency → Redis/Telegram runtime → browser staff E2E workflows;
4. execute one complete LIVE_REQUIRED run and retain its SHA/run/attempt-bound manifest;
5. execute real Telegram M1 and M2 persona walkthroughs and reconcile UI ↔ PostgreSQL ↔ Audit/PaymentEvent evidence;
6. execute encrypted backup → separate empty staging/restore DB and storage → application restore evidence → normal restored-runtime usability;
7. only then deliberately expand YooKassa **test-shop** proof to provider-side paid/refund scenarios that are safely supported without any production operation;
8. make the release/merge decision.

If source, migration, workflow or evidence code changes after evidence collection starts, freeze the new SHA and restart from full CI. Never combine evidence from different candidate SHAs or LIVE_REQUIRED attempts.

## Living process-map operation

`docs/PROCESS_MAP_CURRENT.md` is maintained with every repository change. It is the operational index connecting implementation to end-to-end `P-*` processes, business source-of-truth ownership, evidence status, known `PM-*` inconsistencies/debt and the dated change log.

For every PR/change batch:

1. identify every affected `P-*` process;
2. update implementation/source-of-truth/evidence state where the change alters them;
3. create or update a stable `PM-*` item for each discovered inconsistency, duplication, legacy residue, blocker or design risk;
4. record the corrective action without describing unexecuted source as runtime PASS;
5. append a dated change-log entry with release/evidence impact;
6. do not merge while the CI `Process map maintenance contract` is failing.

The process map cannot expand M1/M2 scope and cannot override the authoritative product/system/acceptance/runbook contracts. If it conflicts with one of them, correct the map or raise the conflict explicitly; never silently choose the more convenient text.

## Pre-deployment gate

Before deploying a candidate image:

1. confirm `PRODUCT_SCOPE_CURRENT.md`, `SYSTEM_CONTRACT_CURRENT.md`, `ACCEPTANCE_CURRENT.md`, `PROCESS_MAP_CURRENT.md` and this runbook match the branch;
2. confirm the process-map `PM-*` register contains every known unresolved release-relevant inconsistency and that no source change is missing from its change log;
3. confirm one Alembic head and apply migrations to fresh PostgreSQL plus a production-like upgraded copy;
4. run `scripts/architecture_check.py` without duplicate-route exemptions;
5. execute the full automated gate after runners allocate;
6. build the exact image intended for staging/production;
7. start it against PostgreSQL + Redis using production-like deployment settings;
8. follow the ordered release procedure above rather than cherry-picking later gates;
9. verify scheduler heartbeat, notification delivery and owned alerting;
10. leave online production payment creation disabled until provider acceptance for the candidate is complete;
11. require a fresh encrypted backup and successful ordered restore drill before release declaration.

A failed or missing gate stops deployment; manual approval does not turn it into PASS.

## Database migration procedure

- Take and verify a restorable encrypted backup before a production schema change.
- Apply Alembic migrations once from the deployment/migration job, never concurrently from every replica.
- Verify expected head afterward.
- Historical timestamps/events that were approximated during backfill remain labelled as approximations.
- Never restore obsolete invariants such as client-wide one-active-Case uniqueness.
- Do not destructive-downgrade financial/consent/audit ledgers merely to boot an older image.

## Telegram operations

Production FSM uses Redis. Polling must hold the configured PostgreSQL singleton lease so two replicas cannot process the same update stream concurrently.

### Timeweb split runtime

The current Timeweb host has a verified asymmetric Telegram route: general IPv4 egress works, Telegram IPv4 times out, while host IPv6 reaches `api.telegram.org`. Docker bridge containers on the host do not currently have usable IPv6 egress. The production topology therefore deliberately separates:

- `legal-concierge` — HTTP staff/API web container on the normal application + proxy bridge networks, with `RUN_BOT=false` and `RUN_SCHEDULER=false`;
- `legal-concierge-bot` — host-network background worker with `RUN_BOT=true` and `RUN_SCHEDULER=true`; it owns Telegram polling, durable notification delivery and scheduler jobs so every Telegram outbound call uses the host's working route;
- PostgreSQL and Redis remain the same authoritative services and expose loopback-only host ports for the host-network worker. They must never be published on a public interface.

`docker-compose.timeweb.yml` plus `docker-compose.timeweb.split.yml` is the reproducible Timeweb topology. `TELEGRAM_API_IPV6` is an operator-managed routing value, not a product constant: deployment must run the Telegram identity/singleton probe and fail if the address is stale or unreachable. Do not silently fall back to a second polling consumer.

The application and background worker use the same immutable image/release SHA and the same persistent storage/backups root. A release is not accepted if only the web container changed or only the background worker changed.

On restart:

- persistent Cases/Documents/Payments remain intact;
- Redis drafts/navigation may resume when healthy;
- absent transient state degrades to database-backed Home/My Case, never to a fabricated mutation context;
- stale mutation callbacks remain Case-bound and fail closed;
- the background worker must reacquire the Telegram polling singleton and scheduler lease; notification delivery resumes from durable rows.

If Telegram delivery fails **after** a committed mutation, do not repeat the domain action from the stale button. First read current Case/Payment state; presentation failure is not transaction failure.

## Real Telegram persona operation

The post-LIVE persona gate uses dedicated non-production acceptance identities/chats. Run M1 and M2 end to end, including documented recovery paths. At each material write reconcile the client/staff UI result with PostgreSQL Case/Document/Payment state, Case/Audit history and `PaymentEvent` evidence.

Screenshots alone are not acceptance evidence. Do not record bot tokens, document plaintext or payment credentials in the evidence package.

## Payment incident procedure

For any anomaly inspect together:

- `payments` — current projection and business timestamps;
- `payment_events` — normalized append-only creation/status ledger;
- `payment_webhook_events` — raw/normalized provider evidence where applicable;
- Case/Audit history — business/actor context.

Never move a Case because a client shows only a payment screenshot. Reconcile provider/bank evidence first.

For stale M1/M2 money:

- preserve the received-money fact;
- never reopen/advance an obsolete Case or reserve another slot automatically;
- use Payment Review/refund paths;
- verify resolution across Payment projection, business timestamps, event ledger and Case/Audit history.

Provider timeout after create is an idempotency/reconciliation case, not permission to create unlimited replacement payments.

## Payment provider enable/disable and sandbox sequence

### Offline production mode

The current no-provider production path is `PAYMENT_PROVIDER=offline`, not `disabled`.

In offline mode:

- M1/M2 obligations are still persisted as Payment records;
- no external payment URL is fabricated;
- the client pays using requisites approved by the operating team;
- staff must reconcile actual bank/accounting evidence before confirmation;
- the admin confirmation requires a reference + comment and applies the canonical payment lifecycle;
- M2 is still paid: a selected slot is not finally booked until the exact payment obligation is confirmed;
- PaymentEvent and Case/Audit evidence must reconcile with the client/staff result.

`PAYMENT_PROVIDER=disabled` is fail-closed in staging/production and intentionally fails production readiness. It is not a shortcut for offline payment and cannot advance a legal/consultation stage. Historical no-payment behavior is local/test only.

### YooKassa LIVE_REQUIRED baseline

When a candidate deliberately enables YooKassa, the automated provider smoke may only:

- prove credentials belong to a YooKassa test shop;
- create a small test payment;
- repeat create with the exact idempotency key;
- retrieve the same payment;
- require `test=true`, unpaid/pending state;
- avoid opening/completing the confirmation URL.

This is connectivity/idempotency evidence, not provider-side paid/refund proof. It is not run as fake evidence for an offline-only release.

### Provider-side paid/refund expansion

Do **not** expand provider mutations before Telegram personas and encrypted backup→restore both pass for the same candidate SHA.

After those gates, execute only YooKassa test-shop scenarios that are deterministic and safe. Every object must report `test=true`. Production shop ids, secrets, payment ids, callbacks and production cards/operations are forbidden. If YooKassa requires explicit test-card/user confirmation, perform and record that test-shop step; do not bypass it. Unsupported behavior is recorded as not proven rather than simulated.

Only after this later gate may production online payment enablement be considered.

## Scheduler operations

The scheduler uses a singleton cycle lease and reports per-job success/failure. Mandatory jobs include backup, payment reminders, client inactivity reminders, slot release, consultation reminders/completion overdue, SLA, retention discovery, security cleanup, claim deadlines and pending notification delivery.

Operational alerting must detect at least scheduler heartbeat loss, repeated job failure, notification backlog, Payment Review/refund backlog, webhook lag/failure, PostgreSQL/Redis unavailability, backup freshness failure, document storage health/capacity and Telegram polling/sending failure. Every production alert needs an owned escalation destination.

## Client inactivity reminders

`last_activity_at` and `last_client_action_at` are client facts; generic `updated_at` is not a substitute. Reminder dedupe is once per stable `(Case, status, last client action)` snapshot and never changes Case state.

If reminders are noisy/misrouted, disable `CLIENT_INACTIVITY_REMINDERS_ENABLED` while preserving data, investigate timestamps/dedupe, then re-enable only after verification.

## Encrypted document storage contract

New `Document.file_path` writes are portable keys only:

`cases/<case_id>/<32hex>.dlcenc`

Operational rules:

- relative document paths must be exactly this three-component form;
- prefixed relative values are invalid and are never suffix-normalized;
- legacy absolute rows may be read for backward compatibility only by extracting their terminal canonical `cases/<id>/<ciphertext>` key and rebasing it onto the **current** `STORAGE_DIR`;
- the historical absolute source prefix is never dereferenced;
- traversal and symlink components fail closed;
- normal authorized document download binds storage resolution to the authorized `Case.id`;
- raw storage paths must never be shown to clients/operators as a normal download mechanism.

This contract is essential for restoring DB+storage into a different root without accidentally reading the source environment.

## Document/security incident procedure

For suspicious upload/access:

- preserve audit records/hashes;
- quarantine/reject unsafe content via the existing pipeline;
- revoke affected credentials/sessions where compromise is suspected;
- never distribute raw storage paths;
- verify one-time/short-lived grant behavior and Case assignment/role at access time.

For compromised staff/admin:

1. disable account and revoke sessions/tokens;
2. rotate exposed secrets/keys by cryptographic domain;
3. inspect AuditLog/document download/payment/admin actions for the period;
4. preserve incident evidence before destructive cleanup;
5. restore access only after credential/MFA reset and role review.

## Key rotation

Session signing, security HMAC, MFA encryption, audit integrity, document encryption and backup encryption are separate cryptographic domains. Rotate the affected domain unless incident scope requires more.

Previous document/audit keys remain verification/decryption-only until all retained material is proven migrated or decryptable. Test historical document decrypt and restore after rotation before retiring an old key.

## Session revoke / access drill

Before production and periodically verify:

- revoked staff session loses privileged browsing;
- role downgrade/reassignment removes document access immediately;
- expired/used/revoked download grants cannot be reused;
- MFA/session version revocation works across browser tabs;
- one client cannot access another client's Case/payment/document data.

## Encrypted backup and restore drill

This mandatory release drill runs **after** real Telegram M1/M2 personas so the backup contains the business state actually exercised.

### Phase A — pre-backup witness

Select a meaningful persona Case with an encrypted V2 historical Document, Payment + `PaymentEvent`, Case Audit history and active staff witness. Run:

```bash
python scripts/post_live_restore_evidence.py snapshot \
  --release-sha <40-char-candidate-SHA> \
  --case-id <CASE_ID> \
  --document-id <DOCUMENT_ID> \
  --payment-id <PAYMENT_ID> \
  --staff-username <STAFF_USERNAME> \
  --output /secure-evidence/POST_LIVE_RESTORE_SOURCE.json
```

Record the emitted snapshot file SHA-256 separately. Evidence schema v2 enforces the same canonical/case-bound portable-storage contract as runtime and verifies the full AuditLog hash chain plus historical document decryption without writing plaintext to evidence.

### Phase B — encrypted backup

Create and verify with the existing backup CLI. Backup/document/audit keys remain external secret-manager material; never package runtime secrets into the archive.

### Phase C — separate safe restore

Restore only into an empty database whose name has a staging/restore/drill/test suffix and into a separate restore directory. Never use production as the first restore proof.

### Phase D — exact application verification

Run `post_live_restore_evidence.py verify` with the same candidate SHA, source snapshot, independently recorded snapshot SHA, staff identity and restored storage. Require `RESTORE_PASS` only if source/target DB endpoints differ, current Alembic head matches, full AuditLog chain verifies, exact business facts match and restored ciphertext decrypts from the restored storage root.

### Phase E — normal runtime usability

Start the same application image against restored DB/storage plus isolated Redis and verify through supported application paths:

- staff login;
- selected M1/M2 Case open;
- Case/Audit history;
- Payment/PaymentEvent state;
- historical document grant/download/decrypt;
- bot/dispatcher startup/read-only smoke;
- zero production Telegram/provider side effects.

Only archive verification + safe restore + `RESTORE_PASS` + normal restored-runtime usability equals backup→restore PASS.

## Retention / legal hold

Business closure, archive and content deletion are separate facts. Retention deletion follows the protected workflow and approval policy. Legal hold/investigation must block destructive retention for affected material. Changing Case status alone never authorizes destruction.

## Staff product operations

Ordinary legal staff work in Workdesk/Lawyer surfaces. Platform/release/backup/diagnostic actions belong to superadmin/DevOps-level roles. Generic manual Case-status editing is not an ordinary operating path; use dedicated business actions so evidence and notifications remain coherent.

## Incident severity guidance

Treat as urgent/high severity:

- wrong-client/wrong-Case document/payment/message exposure;
- received money lost/misapplied;
- stale callback mutating another Case;
- duplicate paid reservation/slot conflict;
- audit/document encryption integrity failure;
- inability to restore backup;
- privileged account compromise;
- state-machine corruption preventing safe legal continuation.

Prefer disabling the affected mutation/payment entry point while preserving read access/evidence over broad manual status corrections.

## Rollback principle

Application rollback must remain schema-compatible with migrations already applied. Prefer a forward-compatible hotfix or prepared compatible image. Preserve new evidence tables/columns even if an older UI does not render them.

## Release declaration

Use **production ready** only after the ordered mandatory gates in `ACCEPTANCE_CURRENT.md` have real evidence for one frozen candidate SHA, the corresponding `PROCESS_MAP_CURRENT.md` inventory/change log is current, #116 is cleared, and no unresolved release blocker remains.

Until then, specific areas may be described as source-hardened/SOURCE_OK, but not live-proven production operation. PR #114 is not merged automatically and remains blocked until the complete evidence chain exists.
