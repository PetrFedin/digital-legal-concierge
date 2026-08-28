# PROCESS MAP — CURRENT

Status: **living authoritative implementation inventory** for the existing Digital Legal Concierge M1/M2 product.

This file is the single maintained map of what is implemented, what owns each business fact, what is still source-only, what requires runtime evidence, and what inconsistencies/debt are known. It does not expand product scope and does not override `PRODUCT_SCOPE_CURRENT.md`, `SYSTEM_CONTRACT_CURRENT.md`, `ACCEPTANCE_CURRENT.md`, `RUNBOOK_CURRENT.md`, `LIVE_REQUIRED_RUNBOOK.md`, or `POST_LIVE_RELEASE_EVIDENCE.md`.

## Mandatory maintenance rule

Every repository change that alters product behavior, data, migrations, API/UI, Telegram, scheduler, security, storage, deployment, tests, workflows, evidence tooling, or release procedure **must update this file in the same PR**.

The CI process-map contract exists to make that rule executable rather than advisory. A PR that changes the repository without changing `docs/PROCESS_MAP_CURRENT.md` is incomplete.

For each change, update at least one of:

- process inventory/status;
- data/source-of-truth ownership;
- runtime/evidence gate;
- known inconsistency/debt register;
- change log at the end of this file.

Do not mark a process `LIVE_PASS` from source inspection. Runtime/test evidence states are defined by `ACCEPTANCE_CURRENT.md`.

## Status vocabulary

| State | Meaning |
| --- | --- |
| `IMPLEMENTED` | Production path exists in source/schema. |
| `SOURCE_AUDITED` | Path was inspected against the current contract. |
| `RUNTIME_PENDING` | Source exists but required runtime evidence has not executed. |
| `BLOCKED_INFRA` | Required runtime cannot execute because infrastructure is unavailable. |
| `LIVE_PASS` | Required runtime/evidence actually passed for the current candidate SHA. |
| `LIVE_FAIL` | Executed runtime contradicted the contract. |
| `DEBT_OPEN` | Known inconsistency, duplication, legacy residue, or unclosed design risk. |
| `FIXED_PENDING_RUNTIME` | Source inconsistency was corrected but the correction has not executed in the required runtime gate. |

## Product boundary

Current product routes are exactly:

- `M1` — standard recovery / case handling;
- `M2` — paid consultation.

Telegram remains the client cabinet. Staff work through authenticated browser/admin/lawyer surfaces. M3/M4, a separate client web cabinet, AI legal decision-making, a new CRM, a second payment product, and a separate calendar product are outside the current release scope.

## System layers

| Layer | Main implementation areas | Responsibility | Current state |
| --- | --- | --- | --- |
| Client / Telegram | `app/bot/*` | client identity, selected Case, calculator, M1/M2 actions, documents, messages, payment UX, stale callback protection | IMPLEMENTED / RUNTIME_PENDING |
| Domain | `app/domain/*` | legal/business state machines and transaction semantics | IMPLEMENTED / SOURCE_AUDITED / RUNTIME_PENDING |
| Staff product | `app/api/*`, `app/admin/*`, `app/lawyer/*` | admin/lawyer workdesk, reviews, assignment, consultations, payment review/refunds, operations | IMPLEMENTED / RUNTIME_PENDING |
| Persistence | `app/models/*`, Alembic migrations | durable Case/Document/Payment/Consultation/Audit facts | IMPLEMENTED / RUNTIME_PENDING |
| Platform/security | `app/security/*`, `app/storage.py` | auth, grants, encryption, audit integrity, backup/restore, key rotation, storage safety | IMPLEMENTED / SOURCE_AUDITED / RUNTIME_PENDING |
| Scheduler/notifications | `app/scheduler/*`, `app/domain/notifications/*` | reminders, slot release, SLA, retention discovery, backup, cleanup, delivery | IMPLEMENTED / RUNTIME_PENDING |
| Release evidence | `.github/workflows/*`, `scripts/live_required_evidence.py`, `scripts/post_live_restore_evidence.py` | CI, runtime matrix, manifest, restore proof | IMPLEMENTED / BLOCKED_INFRA |

# End-to-end process map

## P-00 — Client identity, activity and selected Case

**Goal:** one Telegram client can safely own multiple independent Cases without a client-wide singleton.

Flow:

`Telegram update → User → client activity → ClientCaseContext.selected_case_id → exact Case-bound screen/action`

Primary facts:

- `User.telegram_id` — Telegram identity;
- `User.last_activity_at` — client Telegram activity;
- `Case.last_client_action_at` — activity against a Case;
- `ClientCaseContext.selected_case_id` — selected client Case;
- Case ownership — `Case.client_id`.

Implementation anchors:

- `app/bot/client_activity.py`;
- `app/bot/client_case_navigation.py`;
- `app/bot/client_case_view.py`;
- `app/bot/case_callback_scope.py`;
- `app/models/client_case_context.py`.

Safety rules:

- multiple active Cases per client are allowed;
- stale/crafted callbacks cannot silently switch Case;
- read-only historical views may open an owned Case without changing selected context;
- mutations bind to exact Case/domain identifiers.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-01 — New calculation and idempotent Case creation

Flow:

`Calculate → source operation key → CaseCreationRequest → new Case → calculator draft → Calculation history → client decision`

Primary facts:

- `CaseCreationRequest(client_id, operation_key)` deduplicates the same source operation;
- distinct Calculate operations may create distinct Cases;
- `Calculation.case_id` is one-to-many;
- `calc_start` means new matter;
- `calc_recover:v2:<case_id>` means resume exact existing matter.

Implementation anchors:

- `app/domain/calculator/*`;
- `app/bot/calculator_draft.py`;
- `app/domain/cases/case_service.py`;
- `app/models/calculation.py`;
- `app/models/case_creation_request.py`.

State path:

`NEW → CALCULATOR_STARTED → CALCULATED → CLIENT_DECISION`

with approved direct compatibility transitions defined in `case_transition_policy.py`.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-02 — M1 standard recovery

Canonical business flow:

`calculation → M1 decision → consent → documents → lawyer review → accept/request/reject → contract → 30k payment → POA → claim → 30-day wait → court → 70k payment when applicable → enforcement → recovered amount → success fee → closure → archive`

Canonical Case statuses:

`M1_DOCUMENTS_PENDING`
→ `M1_DOCUMENTS_RECEIVED`
→ `M1_LAWYER_REVIEW`
→ (`M1_DOCS_REQUESTED` ↔ review/received | `M1_REJECTED` | `M1_ACCEPTED`)
→ `M1_CONTRACT_READY`
→ `M1_WAITING_PAYMENT_30000`
→ `M1_PAYMENT_30000_RECEIVED`
→ `M1_POWER_OF_ATTORNEY`
→ `M1_POA_RECEIVED`
→ `M1_CLAIM_PREPARATION`
→ `M1_CLAIM_SENT`
→ `M1_WAITING_30_DAYS`
→ `M1_COURT_STAGE` / approved early recovered-money branch
→ `M1_WAITING_PAYMENT_70000`
→ `M1_PAYMENT_70000_RECEIVED`
→ `M1_ENFORCEMENT`
→ `M1_MONEY_RECEIVED`
→ `M1_WAITING_SUCCESS_FEE`
→ `M1_SUCCESS_FEE_RECEIVED`
→ `M1_CLOSED`
→ `ARCHIVED`.

Primary owners:

- process state: `Case.status` through `CaseService`/dedicated domain services;
- consent: `ConsentAcceptance` immutable snapshot;
- document facts: `Document` version/review records;
- assignment: `Case.assigned_lawyer_id`, `assigned_at`, SLA fields;
- money: `Payment` + `PaymentEvent` + provider evidence + Case/Audit context;
- closure: `closed_at`, `close_reason`, `archived_at`.

Implementation anchors:

- `app/domain/cases/*`;
- `app/domain/documents/*`;
- `app/domain/payments/*`;
- `app/api/contract_center.py`;
- M1 staff/workdesk surfaces under `app/api/*`.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-03 — Documents: upload, review, access, replacement, storage and deletion

Flow:

`required/upload → validation/scanning → quarantine when unsafe → hash → encrypt → portable storage key → Document version → review → protected grant → authorized decrypt/read → retention deletion when approved`

Document statuses:

`REQUIRED / UPLOADED / ON_REVIEW / APPROVED / REJECTED / NEEDS_REUPLOAD / ARCHIVED`.

Storage contract:

`cases/<case_id>/<32hex>.dlcenc`

Rules:

- new DB paths are portable keys, not host absolute paths;
- legacy absolute DB paths are reduced only to terminal canonical Case key and rebound to current `STORAGE_DIR`;
- prefixed relative paths, traversal, wrong Case, invalid ciphertext name, and symlink components fail closed;
- normal download passes authorized `Case.id` into storage resolution;
- retention uses the same `LocalStorageService` resolver/delete contract.

Implementation anchors:

- `app/storage.py`;
- `app/domain/documents/document_service.py`;
- `app/domain/documents/document_review_service.py`;
- `app/domain/documents/staff_upload_storage.py`;
- `app/api/document_access.py`;
- `app/security/document_*` and upload/scanning/grant components;
- `app/domain/retention/case_retention_service.py`.

Evidence anchors:

- `tests/test_document_storage_portability.py`;
- `tests/test_document_download_storage_scope_contract.py`;
- `tests/test_case_retention_storage_portability.py`;
- `tests/test_post_live_restore_evidence.py`.

State: `SOURCE_AUDITED / RUNTIME_PENDING`.

## P-04 — M1 lawyer assignment and SLA

Flow:

`eligible M1 Case after documents → auto-assignment policy → lock Case/candidates → active lawyer + active staff identity → workload/capacity → exactly one assignment → assignment audit → SLA start → workdesk responsibility`

Canonical implementation:

- `app/domain/cases/assignment_policy.py`;
- `app/domain/cases/assignment_service.py` (`CaseAssignmentService`);
- `app/domain/cases/sla_service.py`;
- `app/api/assignment_queue.py` and related staff endpoints.

Important rule: M2 lawyer responsibility is slot/Consultation-driven, not generic M1 `Case.assigned_lawyer_id` auto-assignment.

Concurrency acceptance: two eligible M1 Cases competing for one last lawyer capacity slot must produce exactly one assignment.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-05 — Payment lifecycle, provider evidence, Payment Review and refunds

Flow:

`business obligation → Payment creation → provider/offline path → current projection → immutable PaymentEvent → webhook/provider evidence → Case/Audit application → stale-money review/refund when required`

Payment statuses:

`PENDING / WAITING_CONFIRMATION / PAID / PAID_REVIEW / REFUND_PENDING / REFUND_DECLINED / FAILED / CANCELLED / REFUNDED / EXPIRED`.

Source-of-truth split:

- `payments` — current financial projection and business timestamps;
- `payment_events` — append-only normalized lifecycle ledger;
- `payment_webhook_events` — provider-event evidence/idempotency;
- Case/Audit history — actor/business application context.

Implementation anchors:

- `app/domain/payments/payment_service.py`;
- `payment_lifecycle.py`;
- `payment_webhook_service.py`;
- `payment_review_service.py`;
- `refund_service.py`;
- provider adapter in `providers.py`;
- Payment Review/refund staff product endpoints.

Safety rules:

- late failure cannot overwrite received money;
- exact retry is distinct from stale command;
- stale Payment Review browser action returns authoritative 409 recovery and does not duplicate decision;
- M2 payment stays bound to exact reservation/consultation context;
- stale money enters review/refund, never resurrects obsolete legal state.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-06 — Messaging and notification delivery

Flow:

`client/staff message → exact Case provenance → Message → priority/read state → notification event → dedupe → delivery queue/immediate delivery → Telegram delivery outcome`

Implementation anchors:

- `app/domain/messages/message_service.py`;
- `app/domain/messages/message_priority.py`;
- `app/bot/client_message_provenance.py`;
- `app/domain/notifications/*`;
- `app/scheduler/notification_dispatcher.py`;
- staff Message Center surfaces.

Safety rules:

- message/history pagination is Case-bound;
- draft protection prevents navigation from silently losing an unsent message;
- delivery failure after committed business mutation must not replay the mutation;
- reminders/notifications are deduplicated and do not establish legal facts.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-07 — M2 paid consultation

Canonical flow:

`new consultation action → M2 Case → description → optional documents → slot selection → reservation/hold → payment or approved no-payment path → booked → result/no-show → reschedule/refund/follow-up/to-M1/closure`

Case statuses:

`M2_DESCRIPTION_PENDING`
→ `M2_DOCUMENTS_OPTIONAL`
→ `M2_SLOT_PENDING`
→ `M2_PAYMENT_PENDING` when payment is required
→ `M2_CONSULTATION_BOOKED`
→ `M2_CONSULTATION_DONE`
→ (`M2_CLOSED` | `M2_TO_M1` → `M1_DOCUMENTS_PENDING` | approved reschedule branch).

Consultation statuses:

`DESCRIPTION_PENDING / DOCUMENTS_OPTIONAL / SLOT_PENDING / SLOT_RESERVED / PAYMENT_PENDING / BOOKED / DONE / CLIENT_NO_SHOW / LAWYER_NO_SHOW / CANCELLED / RESCHEDULED / CLOSED`.

Implementation anchors:

- `app/domain/consultations/consultation_intake.py`;
- `consultation_service.py`;
- `slot_service.py`;
- `consultation_change_service.py`;
- `outcome_service.py`;
- no-show resolution services;
- consultation slot/outcome staff surfaces.

Concurrency-sensitive facts:

- slot ownership/reservation;
- hold expiry vs payment success;
- two clients competing for one slot;
- stale reschedule/booking after Case switch.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-08 — Staff authentication, authorization and Workdesk

Flow:

`login → MFA/session where configured → role resolution → Workdesk/Lawyer product → role-safe domain action → audit/session revocation`

Roles include admin/superadmin/lawyer boundaries enforced by access-control and product endpoints.

Implementation anchors:

- `app/api/auth.py`;
- `app/security/access_control.py`;
- session/MFA/revocation security modules;
- `app/api/assignment_queue.py` / Workdesk product;
- lawyer workspace/product surfaces;
- document review, message, consultation, Payment Review/refund products.

Route ownership rule: one `(HTTP method, path)` has one runtime owner; route include order must not define security semantics.

Evidence anchors:

- `tests/test_v37_api_import_inventory.py`;
- `scripts/architecture_check.py`;
- `tests/test_browser_staff_e2e.py`.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-09 — Audit integrity and immutable evidence

Flow:

`business/security action → AuditLog event → chained integrity metadata → verification → backup/restore proof`

Important evidence families:

- `AuditLog` + `AuditChainHead`;
- immutable consent snapshots;
- append-only `PaymentEvent`;
- provider `PaymentWebhookEvent`;
- Case history.

Implementation anchors:

- `app/security/audit_integrity.py`;
- audit models;
- audit staff/diagnostic surfaces;
- restore evidence script validates the full chain.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-10 — Retention, legal hold and content deletion

Flow:

`business closure/archive → retention discovery → legal/payment/consultation/approval preflight → four-eyes protected action → LocalStorageService Case-bound file deletion → content_deleted_at / retention evidence`

Business closure, archive, and content deletion are separate facts.

Implementation anchors:

- `app/domain/retention/case_retention_service.py`;
- `backup_aware_case_retention_service.py`;
- retention records/models;
- scheduler retention discovery.

State: `SOURCE_AUDITED / RUNTIME_PENDING`.

## P-11 — Scheduler and background operations

Scheduler uses a singleton lease and includes processes for:

- encrypted backup when due;
- unpaid payment reminders;
- expired M2 hold release;
- consultation reminders;
- overdue consultation completion;
- Case SLA escalation;
- retention discovery;
- claim 30-day eligibility notification;
- security cleanup: login throttle state, revoked tokens, download grants, MFA re-encryption, document rescan/encryption migration, backup retention, quarantine cleanup;
- pending notification delivery.

Implementation anchors:

- `app/scheduler/scheduler.py`;
- `app/scheduler/jobs.py`;
- `app/scheduler/lease.py`;
- `app/scheduler/notification_dispatcher.py`.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-12 — Security, keys and access revocation

Security domains are intentionally separated:

- session signing;
- security HMAC;
- MFA encryption;
- audit integrity;
- document encryption;
- backup encryption.

Processes include login throttling, MFA/session revocation, role access, one-time/short-lived document grants, document key migration, historical decrypt support, compromised-account response, quarantine/scanning, and cleanup.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-13 — Encrypted backup and restore

Flow:

`post-persona source witness → authenticated encrypted backup → archive verification → separate empty restore DB + restored storage → schema-v2 exact application verification → normal restored staff/document/history/payment/bot usability`

Important rule: archive verification alone is not restore acceptance.

Implementation anchors:

- `app/security/backup_cli.py`;
- `backup_service.py`, `backup_encryption.py`, freshness/retention/restore-fence modules;
- `scripts/post_live_restore_evidence.py`;
- `tests/test_post_live_restore_evidence.py`.

State: `IMPLEMENTED / RUNTIME_PENDING AFTER PERSONAS`.

## P-14 — Release verification and evidence chain

Current ordered release chain for one candidate SHA:

1. runner allocation restored;
2. general pre-live gates pass: `CI`, `Deployment Readiness`, `Reproducible Dependencies`;
3. dedicated gates pass: `PostgreSQL Concurrency`, `Telegram Runtime Contracts`, `Browser Staff E2E`;
4. one complete manual `LIVE_REQUIRED` run passes and creates exact SHA/run/attempt manifest;
5. real Telegram M1/M2 persona walkthroughs pass with UI ↔ PostgreSQL ↔ Audit/PaymentEvent reconciliation;
6. encrypted backup→restore acceptance passes;
7. safe YooKassa test-shop provider-side paid/refund evidence is expanded only then;
8. release/merge decision.

Any source/migration/workflow/evidence-script change after evidence collection begins creates a new candidate SHA and restarts from full CI.

Current infrastructure state: `BLOCKED_INFRA` under GitHub issue #116 until Actions allocates real runners and executes steps.

# Data/source-of-truth matrix

| Business fact | Source of truth | Derived/presentation |
| --- | --- | --- |
| Client identity | `User` | Telegram presentation |
| Active/selected legal matter | `Case` + `ClientCaseContext` | My Case/cards |
| Process stage | `Case.status` | client/staff status labels |
| Calculation history | `Calculation` | latest calculation selected by query/business logic |
| Consent | `ConsentAcceptance` immutable snapshot | consent screen/history |
| M1 lawyer responsibility | `Case.assigned_lawyer_id` + assignment audit/SLA | Workdesk |
| M2 lawyer responsibility | `Consultation.lawyer_id` / selected slot | Workdesk/consultation UI |
| Document state/version | `Document` | readiness/blocker projection |
| Document bytes location | canonical portable `Document.file_path` key | resolved by `LocalStorageService` |
| Message history | `Message` | unread/priority projection |
| Consultation booking | `Consultation` + `ConsultationSlot` | booking UI |
| Current payment state | `Payment` | payment UI |
| Payment lifecycle history | append-only `PaymentEvent` | audit/payment timeline |
| Provider event evidence | `PaymentWebhookEvent` | reconciliation UI |
| Case/action audit | `AuditLog`/Case history | timelines/audit center |
| Closure/archive/deletion | separate Case lifecycle timestamps/reasons | terminal/read-only UI |
| Client inactivity anchor | `User.last_activity_at`, `Case.last_client_action_at`, stage-entry evidence | reminder scheduling |
| Runtime draft/navigation | Redis FSM | never legal source of truth |

# Automated verification map

## General pre-live gates

### `CI`

- source compile;
- architecture check;
- SQLite application suite;
- Alembic current/idempotency/check;
- clean migration smoke;
- PostgreSQL migration + technical encrypted backup/restore drill;
- production container build/start/health.

### `Deployment Readiness`

- Redis FSM persistence integration;
- Docker/Compose deployment contract.

### `Reproducible Dependencies`

- locked test image;
- dependency constraint verification and `pip check`;
- complete test suite in locked image;
- locked production image and package consistency.

## Dedicated runtime/concurrency gates

### `PostgreSQL Concurrency`

Required tests now include:

- multi-Case/calculation races;
- payment races;
- refund retry races;
- staff concurrency;
- **auto-assignment final-capacity race**.

### `Telegram Runtime Contracts`

- Redis-backed FSM restart and Case binding.

### `Browser Staff E2E`

- staff browser role/session paths over PostgreSQL;
- Payment Review stale two-tab recovery.

## Manual `LIVE_REQUIRED`

Requires exact same run attempt/SHA evidence for:

- PostgreSQL;
- Redis;
- real Telegram delivery;
- browser;
- YooKassa test-shop provider baseline;
- aggregate manifest.

# Known inconsistency and debt register

| ID | Area | Finding | Risk | State / action |
| --- | --- | --- | --- | --- |
| PM-001 | Release infra | GitHub Actions jobs end before runner allocation (`runner_id=0`, empty/null steps); issue #116 | No runtime/test PASS can be claimed | `BLOCKED_INFRA`; external billing/spending/runner fix required |
| PM-002 | PostgreSQL dedicated gate | `tests/test_postgres_auto_assignment_concurrency.py` existed and was required by acceptance/LIVE_REQUIRED but was absent from `.github/workflows/postgres-concurrency.yml` | Dedicated gate could pass without proving assignment capacity race | `FIXED_PENDING_RUNTIME` in current batch: added to dedicated workflow |
| PM-003 | M2 state machine | `M2_CONSULTATION_ROUTE` remains an enum/compatibility intake status, but canonical new M2 creation starts at `M2_DESCRIPTION_PENDING` and normal transition policy exposes no canonical incoming edge | Legacy/residue status can confuse diagrams, analytics, manual status handling | `DEBT_OPEN`: retain only as compatibility state until exact historical-row/runtime audit proves safe removal/migration |
| PM-004 | Assignment architecture | `app/domain/assignment/AssignmentEngine` + `WorkloadService` coexist with canonical hardened `app/domain/cases/CaseAssignmentService` | Two assignment abstractions can drift on locking/capacity/audit semantics if legacy path is invoked | `DEBT_OPEN`: exact-ref reference audit required; no new product path may use legacy engine; consolidate/remove only with regression proof |
| PM-005 | Workdesk composition | `app/api/assignment_queue.py` still composes canonical Workdesk by injecting a responsibility/deep-link JS patch into `WORKDESK_HTML` at render time | UI behavior is distributed across modules and harder to reason about than a single canonical component | `DEBT_OPEN`: not a current route-ownership violation, but candidate for later source consolidation after release evidence is restored |
| PM-006 | Repository governance | Private-repo ruleset API could not be independently inspected on current GitHub plan | Formal branch-protection required-context list is not independently proven through API | `DEBT_OPEN`: CI-level process-map contract provides repository enforcement; verify GitHub branch/rules settings manually when runner/billing is restored |
| PM-007 | Runtime evidence | Storage portability, retention portability, restore schema-v2, Payment Review and new assignment regressions exist but current runner has not executed them | Source correctness may hide runtime regressions | `RUNTIME_PENDING`; must pass ordered release gates on new candidate SHA |

# Change impact rule

Whenever code changes:

1. identify the affected `P-*` process(es);
2. update ownership/status/evidence entries if behavior or source of truth changed;
3. add or update a `PM-*` debt item for any newly discovered inconsistency;
4. do not delete an unresolved debt item merely because code moved; close it with evidence/action;
5. append a change-log entry below;
6. if release evidence had begun, declare a new candidate SHA and restart the release chain from full CI.

# Change log

## 2026-08-28 — Living process map introduced

- Created `docs/PROCESS_MAP_CURRENT.md` as the mandatory maintained implementation/process inventory.
- Recorded M1, M2, documents, assignment/SLA, payments, messages/notifications, staff, audit, retention, scheduler, security, backup/restore and release-evidence processes.
- Recorded current data/source-of-truth matrix and automated gate map.
- Recorded known inconsistencies/debt PM-001..PM-007 rather than hiding them in historical chat/audit notes.
- Fixed PM-002 in source: dedicated PostgreSQL Concurrency workflow now includes `tests/test_postgres_auto_assignment_concurrency.py`.
- Because this batch changes workflow/repository contracts, the previous frozen release SHA is invalidated; the new candidate must restart ordered verification from full CI once #116 is actually restored.
