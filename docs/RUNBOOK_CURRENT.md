# RUNBOOK — CURRENT

Status: **authoritative current operations/release runbook** for existing M1/M2.

This runbook does not expand product scope. It defines what must be operated and proven before/after exposing the existing M1/M2 product to real clients and real money.

## Environment contract

Production-like staging and production use the same application/container artifact and migration chain. Required runtime dependencies are:

- PostgreSQL;
- Redis for Telegram FSM and singleton/coordination mechanisms where configured;
- Telegram bot token/account;
- configured payment provider (YooKassa for the current production payment path, or the explicitly disabled/offline mode);
- encrypted document storage and configured keyrings;
- scheduler;
- staff authentication/MFA/session infrastructure;
- backup storage.

SQLite and in-memory FSM are development/test conveniences only and are not production acceptance environments.

Persisted datetimes remain UTC. Outward presentation uses `BUSINESS_TIMEZONE` (default `Europe/Moscow`) and `BUSINESS_TIMEZONE_LABEL` (default `МСК`).

## Current infrastructure blocker

GitHub issue **#116** tracks the Actions runner/billing/spending blocker. Jobs stopping before runner allocation are an infrastructure failure, not a successful CI run.

Until #116 is resolved and the required workflows actually execute, release status is **blocked**. Do not mark the branch CI-green based on source inspection or a workflow file existing in the repository.

## Pre-deployment gate

Before deploying a candidate image:

1. confirm `PRODUCT_SCOPE_CURRENT.md`, `SYSTEM_CONTRACT_CURRENT.md` and `ACCEPTANCE_CURRENT.md` match the branch;
2. confirm one linear Alembic head and apply migrations to a fresh PostgreSQL database and a production-like upgraded copy;
3. run `scripts/architecture_check.py` without exemptions for duplicate runtime routes;
4. run the complete automated test workflows after Actions runners allocate;
5. build the exact container image intended for staging/production;
6. start it against PostgreSQL + Redis with production deployment settings;
7. execute mandatory M1/M2 smoke/persona cases from `ACCEPTANCE_CURRENT.md`;
8. verify scheduler heartbeat and notification delivery;
9. verify payment provider contract/sandbox before enabling real payment creation;
10. verify a fresh encrypted backup and a recent successful restore drill.

A failed gate stops deployment; it is not converted to a warning by manual approval.

## Database migration procedure

- Take/verify a restorable encrypted backup before a production schema change.
- Apply Alembic migrations once from the deployment/migration job, not concurrently from all application replicas.
- Verify the expected migration head after upgrade.
- For migrations that backfill historical business timestamps/events, treat approximated legacy values as approximations; do not relabel them as exact provider facts.
- Never restore removed invalid invariants such as one active Case per client during downgrade planning.

Current production-hardening migrations include multi-Case/calculation history, consent evidence, payment lifecycle timestamps, Case activity/closure semantics and normalized payment event ledger. Validate the full chain on PostgreSQL before release.

## Telegram operations

Production FSM must use Redis. Polling must hold the configured singleton lease so two replicas cannot process the same update stream concurrently.

On bot restart:

- persistent Cases/Documents/Payments must remain intact;
- Redis drafts/navigation may resume when Redis is healthy;
- if transient FSM state is absent, the client must land on database-backed Home/My Case rather than a fabricated mutation state;
- stale Telegram mutation callbacks remain Case-bound and fail closed when the selected Case/state differs.

If Telegram delivery fails **after** a committed legal/financial mutation, do not retry the domain mutation manually from the same old button. Open the current Case/Payment state first; presentation failure is not transaction failure.

## Payment incident procedure

For any payment anomaly, inspect three complementary evidence layers:

- `payments` — current projection and lifecycle timestamps;
- `payment_events` — normalized creation/status transition ledger;
- `payment_webhook_events` + Case/Audit history — provider evidence and business/actor context.

Never manually move a Case merely because a client shows a payment screenshot. Reconcile provider/bank evidence first.

If a stale M1/M2 payment arrives:

- preserve the received-money fact;
- do not reopen/advance an obsolete Case or reserve a different slot;
- use the existing review/refund path;
- verify refund resolution in current Payment status, business timestamp, normalized ledger and Case/Audit history.

Provider timeout after create must be treated as an idempotency/reconciliation scenario, not an invitation to create unlimited new provider payments.

## Payment provider enable/disable

When online payments are disabled, Telegram must not manufacture external payment links. Existing obligations/history remain visible and staff confirmation follows the approved offline/manual reconciliation procedure.

Before switching online payments on in production, run the complete sandbox matrix from `ACCEPTANCE_CURRENT.md`: create, timeout, idempotent retry, duplicate success/webhook, late failure, stale reservation, stale M1 payment, refund/review and restart.

## Scheduler operations

The scheduler runs under a singleton cycle lease and reports per-job success/failure. Mandatory jobs include backup, payment reminders, client inactivity reminders, slot release, consultation reminders/completion overdue, SLA, retention discovery, security cleanup, claim deadlines and pending notification delivery.

Operational alerting must detect at least:

- scheduler heartbeat missing;
- repeated scheduler job failure;
- notification pending/failed backlog growth;
- payment review/refund backlog growth;
- webhook processing lag/failure;
- database/Redis unavailability;
- backup freshness failure;
- document storage capacity/health failure;
- Telegram bot unable to poll/send.

A dashboard alone is insufficient: each production alert needs an owned notification/escalation destination.

## Client inactivity reminders

`last_activity_at`/`last_client_action_at` are client facts and must not be replaced by generic `updated_at`.

The reminder service emits once per stable `(Case, status, last client action)` snapshot after the configured inactivity period. It must never change Case state. If reminders are noisy or misrouted, disable `CLIENT_INACTIVITY_REMINDERS_ENABLED` while preserving the underlying Case/draft data, then investigate timestamps/dedupe before re-enabling.

## Document/security incident procedure

For suspicious upload/access:

- preserve relevant audit records/hashes;
- quarantine/reject unsafe content according to the existing upload pipeline;
- revoke access/session credentials where compromise is suspected;
- do not distribute raw document-storage paths;
- verify one-time/short-lived download grant behavior;
- inspect Case assignment/role at access time.

For a compromised staff/admin account:

1. disable/revoke the account and active sessions/tokens;
2. rotate exposed secrets/keys according to their cryptographic domain;
3. inspect AuditLog/document download/payment/admin actions for the affected period;
4. preserve incident evidence before destructive cleanup;
5. restore access only after credential/MFA reset and role review.

## Key rotation

Cryptographic domains are intentionally separated (session signing, security HMAC, MFA encryption, audit integrity, document encryption, backup encryption). Rotate only the affected domain unless incident scope requires broader rotation.

Previous keyrings are verification/decryption-only. Test historical document decrypt and backup restore after rotation before retiring an old key.

Never delete an old document/backup key merely because the new key is active; first prove all retained encrypted material has been migrated or remains decryptable through the approved keyring.

## Session revoke / access drill

Before production and periodically thereafter, verify:

- revoked staff session cannot continue browsing privileged pages;
- role downgrade/reassignment removes document access immediately;
- expired document grants cannot be reused;
- MFA/session version revocation behaves across multiple browser tabs;
- client Telegram identity cannot open another client's Case/payment/document data.

## Backup and restore drill

Backup success is accepted only after restore proof.

Procedure:

1. generate/locate authenticated encrypted backup;
2. restore it into an isolated staging PostgreSQL/database environment;
3. verify migration/schema head;
4. start the same application image against restored data;
5. authenticate staff;
6. open representative M1 and M2 Cases;
7. decrypt/open representative historical Documents;
8. verify Case/Audit/Payment/PaymentEvent evidence;
9. run Telegram read-only smoke against the restored environment;
10. record drill date, backup identity, operator and result.

Never perform an untested destructive restore directly over production as the first proof that backups work.

## Retention / legal hold

Business closure, archive and content deletion are separate facts. Retention deletion must follow the existing protected retention workflow and approval policy.

Where a legal hold/investigation requires preservation, retention execution must be blocked for affected material according to the approved operating procedure. Destructive retention must be auditable and must not be triggered merely by changing a Case status.

## Staff product operations

Ordinary legal staff should work in Workdesk/Lawyer surfaces, not release/maintenance pages. Platform/release/backup/diagnostic actions belong to superadmin/DevOps-level operating roles.

Generic manual Case-status editing is not a normal operating tool. Staff must use the dedicated business actions so evidence, notifications and state transitions remain consistent.

## Incident severity guidance

Treat as urgent/high severity:

- wrong-client/wrong-Case document/payment/message exposure;
- money received but lost/misapplied;
- stale callback mutates another Case;
- duplicate paid reservation/slot conflict;
- audit/document encryption integrity failure;
- inability to restore backups;
- privileged account compromise;
- state-machine corruption preventing safe legal continuation.

For these incidents, prefer disabling the affected mutation/payment entry point while preserving read access/evidence rather than attempting broad manual status corrections.

## Rollback principle

Application rollback must be schema-compatible with already-applied migrations. Do not blindly Alembic-downgrade production financial/consent/audit ledgers to make an old image boot.

If a new release fails after migrations, prefer a forward-compatible hotfix or a previously prepared compatible image. Preserve new evidence tables/columns even if an older UI does not display them.

## Release declaration

The phrase **production ready** may be used only after the mandatory gates in `ACCEPTANCE_CURRENT.md` have real evidence, GitHub Actions are no longer blocked, and staging/restore/security/payment drills have been executed.

Until then, repository work may be described as production-hardening/source-complete for particular areas, but not as live-proven production operation.