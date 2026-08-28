# PROCESS MAP — CURRENT

Status: **living authoritative implementation inventory** for the existing Digital Legal Concierge M1/M2 product.

This file is the maintained map of what is implemented, what owns each business fact, what is source-only, what still requires runtime evidence, what inconsistencies/debt are known, and what must happen next. It does **not** expand product scope and does not override `PRODUCT_SCOPE_CURRENT.md`, `SYSTEM_CONTRACT_CURRENT.md`, `ACCEPTANCE_CURRENT.md`, `RUNBOOK_CURRENT.md`, `LIVE_REQUIRED_RUNBOOK.md`, or `POST_LIVE_RELEASE_EVIDENCE.md`.

## Repository entrypoints and mandatory maintenance

Read before changing the repository:

1. root `AGENTS.md` — pre-change contract for agents/developers;
2. `docs/PRODUCT_SCOPE_CURRENT.md`;
3. `docs/SYSTEM_CONTRACT_CURRENT.md`;
4. `docs/ACCEPTANCE_CURRENT.md`;
5. `docs/RUNBOOK_CURRENT.md`;
6. `docs/LIVE_REQUIRED_RUNBOOK.md`;
7. `docs/POST_LIVE_RELEASE_EVIDENCE.md`;
8. this file.

Every repository change that alters product behavior, data, migrations, API/UI, Telegram, scheduler, security, storage, deployment, tests, workflows, evidence tooling, documentation contract, or release procedure **must update this file in the same change batch/PR**.

Before finishing a change:

- identify every affected `P-*` process;
- update source-of-truth ownership/status/evidence state where behavior changed;
- create or update a stable `PM-*` item for every discovered inconsistency, duplication, legacy residue, blocker or design risk;
- never delete a `PM-*` item merely because code moved — close it only with an explicit corrective action and the evidence required by the contract;
- append a dated change-log entry explaining the change and release/evidence impact;
- never promote source inspection to `LIVE_PASS`;
- make this map the **last governed commit in the batch**, or commit the governed change and map together.

CI enforces this through `Process map maintenance contract`. The gate now checks both that the map changed somewhere in the PR and that no later governed repository commit exists after the latest map update. A code/test/workflow/docs-contract commit after the map makes the map stale and must be followed by another map update. `AGENTS.md` exposes the same rule before a PR is created.

## Status vocabulary

| State | Meaning |
| --- | --- |
| `IMPLEMENTED` | Production path exists in source/schema. |
| `SOURCE_AUDITED` | Path was inspected against the current contract. |
| `RUNTIME_PENDING` | Source exists but required runtime evidence has not executed. |
| `BLOCKED_INFRA` | Required runtime cannot execute because infrastructure is unavailable. |
| `LIVE_PASS` | Required runtime/evidence actually passed for the current candidate SHA. |
| `LIVE_FAIL` | Executed runtime contradicted the contract. |
| `DEBT_OPEN` | Known inconsistency, duplication, legacy residue or unclosed design risk. |
| `FIXED_PENDING_RUNTIME` | Source inconsistency was corrected but required runtime proof has not executed. |

## Product boundary

Current product routes are exactly:

- `M1` — standard recovery / case handling;
- `M2` — paid consultation.

Telegram remains the client cabinet. Staff work through authenticated browser/admin/lawyer surfaces. M3/M4, a separate client web cabinet, AI legal decision-making, a second CRM/payment/calendar product, or unrelated scope is out of the current release unless the authoritative product contract is explicitly changed first.

## System layers

| Layer | Main implementation areas | Responsibility | Current state |
| --- | --- | --- | --- |
| Client / Telegram | `app/bot/*` | identity, selected Case, calculator, M1/M2 actions, documents, messages, payment UX, stale callback protection | IMPLEMENTED / RUNTIME_PENDING |
| Domain | `app/domain/*` | legal/business state machines and transaction semantics | IMPLEMENTED / SOURCE_AUDITED / RUNTIME_PENDING |
| Staff product | `app/api/*`, staff UI modules | admin/lawyer Workdesk, reviews, assignment, consultations, Payment Review/refunds | IMPLEMENTED / RUNTIME_PENDING |
| Persistence | `app/models/*`, Alembic | durable Case/Document/Payment/Consultation/Audit facts | IMPLEMENTED / RUNTIME_PENDING |
| Platform/security | `app/security/*`, `app/storage.py` | auth, grants, encryption, audit integrity, backup/restore, key rotation, storage safety | IMPLEMENTED / SOURCE_AUDITED / RUNTIME_PENDING |
| Scheduler/notifications | `app/scheduler/*`, `app/domain/notifications/*` | reminders, slot release, SLA, retention, backup, cleanup, delivery | IMPLEMENTED / RUNTIME_PENDING |
| Release evidence | `.github/workflows/*`, evidence scripts | CI, runtime matrix, manifests, restore proof | IMPLEMENTED / BLOCKED_INFRA |
| Governance | `AGENTS.md`, CURRENT docs, this map, CI contract | force process/debt/change traceability and map freshness | IMPLEMENTED / FIXED_PENDING_RUNTIME |

# End-to-end process map

## P-00 — Client identity, activity and selected Case

Flow: `Telegram update → User → activity facts → ClientCaseContext.selected_case_id → exact Case-bound screen/action`.

Source of truth:

- `User.telegram_id` — Telegram identity;
- `User.last_activity_at` — client Telegram activity;
- `Case.last_client_action_at` — activity against a Case;
- `ClientCaseContext.selected_case_id` — selected client Case;
- `Case.client_id` — ownership.

Anchors: `app/bot/client_activity.py`, `client_case_navigation.py`, `client_case_view.py`, `case_callback_scope.py`, `app/models/client_case_context.py`.

Rules: multiple active Cases are allowed; stale/crafted callbacks cannot silently switch Case; read-only history may open another owned Case without changing selected context; mutations carry exact Case/domain provenance.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-01 — Calculation and idempotent Case creation

Flow: `Calculate → source operation key → CaseCreationRequest → new Case → calculator draft → Calculation history → client decision`.

Facts:

- `(client_id, operation_key)` deduplicates the same source action;
- distinct Calculate operations may create distinct Cases;
- `Calculation.case_id` is one-to-many;
- `calc_start` means a new matter;
- `calc_recover:v2:<case_id>` resumes an exact existing matter.

Canonical path: `NEW → CALCULATOR_STARTED → CALCULATED → CLIENT_DECISION` plus explicit compatibility transitions from `case_transition_policy.py`.

Anchors: `app/domain/calculator/*`, `app/bot/calculator_draft.py`, `app/domain/cases/case_service.py`, `Calculation`, `CaseCreationRequest`.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-02 — M1 standard recovery

Canonical business flow:

`calculation → M1 decision → exact consent → documents → lawyer review → accept/request/reject → contract → 30k payment → POA → claim → 30-day wait → court → 70k payment when applicable → enforcement → recovered amount → success fee → structured closure → archive`.

Canonical Case path:

`M1_DOCUMENTS_PENDING → M1_DOCUMENTS_RECEIVED → M1_LAWYER_REVIEW → M1_DOCS_REQUESTED/M1_ACCEPTED/M1_REJECTED → M1_CONTRACT_READY → M1_WAITING_PAYMENT_30000 → M1_PAYMENT_30000_RECEIVED → M1_POWER_OF_ATTORNEY → M1_POA_RECEIVED → M1_CLAIM_PREPARATION → M1_CLAIM_SENT → M1_WAITING_30_DAYS → M1_COURT_STAGE/approved early recovered-money branch → M1_WAITING_PAYMENT_70000 → M1_PAYMENT_70000_RECEIVED → M1_ENFORCEMENT → M1_MONEY_RECEIVED → M1_WAITING_SUCCESS_FEE → M1_SUCCESS_FEE_RECEIVED → M1_CLOSED → ARCHIVED`.

Owners:

- process state — `Case.status` through `CaseService`/dedicated domain services;
- consent — immutable `ConsentAcceptance`;
- documents — versioned `Document`/review facts;
- M1 lawyer — `Case.assigned_lawyer_id`, assignment audit/SLA;
- money — `Payment` + `PaymentEvent` + provider evidence + Case/Audit context;
- terminal facts — `closed_at`, `close_reason`, `archived_at`, separate retention deletion fact.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-03 — Documents, encrypted storage, review, access and deletion

Flow: `required/upload → validation/scanning → quarantine when unsafe → hash → encrypt → portable storage key → Document version → review → protected grant → authorized decrypt/read → retention deletion when approved`.

Storage contract: `cases/<case_id>/<32hex>.dlcenc`.

Rules:

- new DB paths are portable keys, never host-specific absolute paths;
- a legacy absolute row may be reduced only to terminal canonical Case key and rebound to **current** `STORAGE_DIR`;
- historical source prefix is never dereferenced;
- prefixed relative paths, traversal, wrong Case, invalid ciphertext name and symlink components fail closed;
- normal authorized download passes authorized `Case.id` into storage resolution;
- retention uses the same `LocalStorageService` resolver/delete contract.

Anchors: `app/storage.py`, `app/domain/documents/*`, `app/api/document_access.py`, document security modules, `app/domain/retention/case_retention_service.py`.

Evidence: `test_document_storage_portability.py`, `test_document_download_storage_scope_contract.py`, `test_case_retention_storage_portability.py`, `test_post_live_restore_evidence.py`.

State: `SOURCE_AUDITED / RUNTIME_PENDING`.

## P-04 — M1 lawyer assignment and SLA

Flow: `eligible M1 Case after documents → auto-assignment policy → Case/candidate locking → active lawyer + active staff identity → workload/capacity → exactly one assignment → assignment audit → SLA → Workdesk responsibility`.

Canonical owner: `app/domain/cases/assignment_service.py::CaseAssignmentService` plus `assignment_policy.py` and `sla_service.py`.

Rules:

- M2 responsibility is consultation/slot-driven, not M1 `Case.assigned_lawyer_id` auto-assignment;
- two eligible M1 Cases competing for one final capacity slot may produce exactly one assignment;
- `architecture_check.py::check_legacy_assignment_imports` prevents production code from reintroducing historical `app.domain.assignment` semantics through absolute imports, relative `ImportFrom`, or literal dynamic imports (`importlib.import_module` / `__import__`).

Evidence: `tests/test_postgres_auto_assignment_concurrency.py`, `tests/test_legacy_assignment_import_guard.py`.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-05 — Payment lifecycle, Payment Review and refunds

Flow: `business obligation → Payment → provider/offline path → current projection → immutable PaymentEvent → provider/webhook evidence → Case/Audit application → stale-money review/refund when required`.

Payment statuses: `PENDING / WAITING_CONFIRMATION / PAID / PAID_REVIEW / REFUND_PENDING / REFUND_DECLINED / FAILED / CANCELLED / REFUNDED / EXPIRED`.

Source split:

- `Payment` — current projection + business timestamps;
- `PaymentEvent` — append-only normalized lifecycle ledger;
- `PaymentWebhookEvent` — provider evidence/idempotency;
- Case/Audit history — actor/business application context.

Rules: late failure cannot overwrite received money; exact retry differs from stale command; stale Payment Review returns authoritative 409 without a duplicate resolution; M2 payment stays bound to exact reservation/consultation context; stale money never resurrects obsolete legal state.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-06 — Messages and notifications

Flow: `client/staff message → exact Case provenance → Message → priority/read state → notification event → dedupe → delivery → Telegram outcome`.

Rules: history/pagination are Case-bound; draft protection prevents silent loss; delivery failure after a committed mutation must not replay the mutation; reminders/notifications do not establish legal facts.

Anchors: `app/domain/messages/*`, `app/bot/client_message_provenance.py`, `app/domain/notifications/*`, `app/scheduler/notification_dispatcher.py`.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-07 — M2 paid consultation

Canonical flow: `new consultation → M2 Case → description → optional documents → slot → reservation/hold → payment or approved no-payment path → booked → result/no-show → reschedule/refund/follow-up/to-M1/closure`.

Canonical new Case path: `M2_DESCRIPTION_PENDING → M2_DOCUMENTS_OPTIONAL → M2_SLOT_PENDING → M2_PAYMENT_PENDING when required → M2_CONSULTATION_BOOKED → M2_CONSULTATION_DONE → M2_CLOSED or M2_TO_M1 → M1_DOCUMENTS_PENDING` plus approved reschedule branches.

Historical compatibility contract:

- old string-backed rows may contain `M2_CONSULTATION_ROUTE`;
- the only supported use is read/upgrade `M2_CONSULTATION_ROUTE → M2_DESCRIPTION_PENDING`;
- `CaseService.create_case()` validates initial status before persistence and rejects the legacy value;
- normal and `force=True` transitions cannot re-enter it;
- ORM status-write backstop blocks direct recreation/re-entry;
- regression inserts a raw historical DB row, proves ORM hydration remains readable, upgrades it forward and verifies persisted state;
- focused service regression proves rejection occurs before DB use.

Anchors: consultation domain services, `case_service.py`, `case_transition_policy.py`, `app/models/case.py`.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-08 — Staff authentication, authorization and Workdesk

Flow: `login → MFA/session where configured → role → Workdesk/Lawyer product → role-safe domain action → audit/session revocation`.

Composition ownership:

- `app/api/assignment_queue.py` — `/admin/workdesk/ui` route/auth + responsibility/queue data APIs, **no direct HTML patching**;
- `app/api/workdesk_ui.py` — base Workdesk document;
- `app/api/workdesk_renderer.py` — single final composition boundary for base UI + responsibility/deep-link behavior + integrity overlay;
- `app/api/workdesk_integrity.py` — integrity producer consumed through renderer.

Rules: one `(HTTP method, path)` runtime owner; include order must not define security; route/data modules do not mutate foreign templates.

Evidence: `test_v37_api_import_inventory.py`, `architecture_check.py`, `test_browser_staff_e2e.py`, `test_workdesk_renderer_contract.py`.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-09 — Audit integrity and immutable evidence

Flow: `business/security action → AuditLog → chained integrity metadata → verification → backup/restore proof`.

Evidence families: `AuditLog` + `AuditChainHead`, immutable consent snapshot, append-only `PaymentEvent`, provider `PaymentWebhookEvent`, Case history.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-10 — Retention, legal hold and content deletion

Flow: `business closure/archive → retention discovery → legal/payment/consultation/approval preflight → four-eyes action → Case-bound LocalStorageService deletion → content_deleted_at/evidence`.

Business closure, archive and content deletion are separate facts. Missing-file retry remains idempotent; legal hold/unsettled obligations block destructive retention.

State: `SOURCE_AUDITED / RUNTIME_PENDING`.

## P-11 — Scheduler and background operations

Singleton scheduler covers:

- encrypted backup when due;
- unpaid payment reminders;
- expired M2 hold release;
- consultation reminders/overdue completion;
- Case SLA escalation;
- retention discovery;
- claim 30-day eligibility notification;
- login/token/grant/MFA/document/backup/quarantine cleanup/migration work;
- pending notification delivery.

Anchors: `app/scheduler/scheduler.py`, `jobs.py`, `lease.py`, `notification_dispatcher.py`.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-12 — Security, keys and access revocation

Cryptographic domains remain separate: session signing, security HMAC, MFA encryption, audit integrity, document encryption, backup encryption.

Processes include login throttling, MFA/session revocation, role access, short-lived/one-time document grants, key migration/historical decrypt, compromised-account response, quarantine/scanning and cleanup.

State: `IMPLEMENTED / RUNTIME_PENDING`.

## P-13 — Encrypted backup and restore

Flow: `post-persona source witness → authenticated encrypted backup → archive verification → separate empty restore DB + restored storage → schema-v2 application verification → normal restored staff/document/history/payment/bot usability`.

Archive verification alone is not restore acceptance. Normal application read/decrypt after restore is mandatory.

State: `IMPLEMENTED / RUNTIME_PENDING AFTER PERSONAS`.

## P-14 — Release verification and evidence chain

Ordered chain for one candidate SHA:

1. real runner allocation restored;
2. `CI` + `Deployment Readiness` + `Reproducible Dependencies` pass;
3. dedicated `PostgreSQL Concurrency` → `Telegram Runtime Contracts` → `Browser Staff E2E` pass;
4. one complete manual `LIVE_REQUIRED` run passes and creates exact SHA/run/attempt manifest;
5. real Telegram M1/M2 personas pass with UI ↔ PostgreSQL ↔ Audit/PaymentEvent reconciliation;
6. encrypted backup→restore application/runtime proof passes;
7. safe YooKassa test-shop provider-side paid/refund proof is expanded only then;
8. release/merge decision.

Any source/migration/workflow/evidence-script change after evidence collection begins creates a new candidate SHA and restarts from full CI.

Current infrastructure state: `BLOCKED_INFRA` under issue #116 until Actions allocates real runners and executes steps.

# Data/source-of-truth matrix

| Business fact | Source of truth | Derived/presentation |
| --- | --- | --- |
| Client identity | `User` | Telegram presentation |
| Active/selected matter | `Case` + `ClientCaseContext` | My Case/cards |
| Process stage | `Case.status` | client/staff labels |
| Calculation history | `Calculation` | latest derived by query |
| Consent | immutable `ConsentAcceptance` | consent history/UI |
| M1 lawyer responsibility | Case assignment + assignment audit/SLA | Workdesk |
| M2 lawyer responsibility | `Consultation.lawyer_id` / selected slot | consultation UI/Workdesk |
| Document state/version | `Document` | readiness/blockers |
| Document byte location | canonical portable `Document.file_path` | `LocalStorageService` resolution |
| Message history | `Message` | unread/priority projection |
| Consultation booking | `Consultation` + `ConsultationSlot` | booking UI |
| Current payment | `Payment` | payment UI |
| Financial history | append-only `PaymentEvent` | timeline |
| Provider evidence | `PaymentWebhookEvent` | reconciliation |
| Case/action audit | `AuditLog`/Case history | timelines/audit center |
| Closure/archive/delete | separate lifecycle timestamps/reasons | terminal/read-only UI |
| Client inactivity | client/Case activity + stage-entry evidence | reminder scheduling |
| Runtime draft/navigation | Redis FSM | never legal source of truth |

# Automated verification map

## General pre-live gates

### `CI`

- `Process map maintenance contract`: file must exist, must be changed in the PR, and its latest commit must be at least as new as the latest governed repository commit;
- `tests/test_process_map_governance_contract.py` locks the presence/freshness source contract and root `AGENTS.md` wording;
- source compile;
- architecture check, including absolute/relative/literal-dynamic legacy assignment containment;
- SQLite suite, including CaseService/M2 compatibility, Workdesk renderer and legacy-assignment import-guard regressions;
- Alembic current/idempotency/check + clean migration smoke;
- PostgreSQL migration + technical encrypted backup/restore drill;
- production container build/start/health.

### `Deployment Readiness`

- Redis FSM persistence;
- Docker/Compose deployment contract.

### `Reproducible Dependencies`

- locked test/production images;
- dependency verification + `pip check`;
- complete suite in locked test image.

## Dedicated gates

### `PostgreSQL Concurrency`

Must cover multi-Case/calculation races, payment races, refund retries, staff concurrency and **auto-assignment final-capacity race**.

### `Telegram Runtime Contracts`

Redis-backed FSM restart and exact Case binding.

### `Browser Staff E2E`

Staff role/session paths, Payment Review stale two-tab recovery and current canonical Workdesk route/rendering.

## Manual `LIVE_REQUIRED`

One exact SHA/run/attempt must prove PostgreSQL, Redis, real Telegram delivery, browser, YooKassa test-shop provider baseline and aggregate manifest.

# Known inconsistency and debt register

| ID | Area | Finding | Risk | State / action |
| --- | --- | --- | --- | --- |
| PM-001 | Release infra | Actions jobs end before runner allocation (`runner_id=0`, empty/null steps); issue #116 | No runtime/test PASS can be claimed | `BLOCKED_INFRA`; external billing/spending/runner fix required |
| PM-002 | PostgreSQL dedicated gate | Auto-assignment capacity race existed in tests/acceptance/LIVE_REQUIRED but was omitted from dedicated PostgreSQL workflow | Dedicated gate could pass without assignment race proof | `FIXED_PENDING_RUNTIME`; test is now included in dedicated workflow |
| PM-003 | M2 state machine | Historical `M2_CONSULTATION_ROUTE` can exist in string-backed old rows, while canonical current M2 begins at `M2_DESCRIPTION_PENDING` | Re-entry would split semantics; immediate deletion could break historical rows | `FIXED_PENDING_RUNTIME / COMPATIBILITY_READ_ONLY`; CaseService initial validation + no-reentry transition rule + ORM backstop + historical-row hydration/forward-upgrade regression; physical removal deferred until real DB audit |
| PM-004 | Assignment architecture | Historical `AssignmentEngine`/`WorkloadService` coexist with hardened `CaseAssignmentService`; initial containment covered direct absolute imports but relative or literal dynamic imports could bypass it | Reintroduced legacy code could bypass Case/candidate locking, active staff identity, workload/capacity and SLA/audit semantics while still evading the architecture gate | `DEBT_OPEN / SOURCE_CONTAINED / HARDENED_PENDING_RUNTIME`; guard now resolves relative `ImportFrom` and detects absolute/relative literal dynamic imports through `importlib.import_module` aliases and `__import__`; `tests/test_legacy_assignment_import_guard.py` covers forbidden bypass forms and allowed canonical imports; physical legacy-module deletion still waits for historical/external-consumer audit + executable regressions |
| PM-005 | Workdesk composition | Final UI was assembled by route-level responsibility JS patch plus separate integrity injection | Distributed UI ownership could omit/duplicate behavior | `FIXED_PENDING_RUNTIME / COMPOSITION_CENTRALIZED`; single `workdesk_renderer.py`, route module no longer patches HTML, regression locks boundary |
| PM-006 | Repository governance | Private-repo ruleset required contexts cannot be independently enumerated through current GitHub API/plan | Formal branch protection contract is not API-proven | `DEBT_OPEN`; CI governance + root `AGENTS.md` implemented; manually verify repository rules after billing/runner recovery |
| PM-007 | Runtime evidence | Current storage/restore/retention/Payment Review/assignment/M2/Workdesk/governance regressions have not run on an Actions runner | Source correctness may hide runtime regressions | `RUNTIME_PENDING`; ordered gates required on current candidate |
| PM-008 | Living-map governance | Initial CI rule only required `PROCESS_MAP_CURRENT.md` to appear somewhere in the total PR diff; a long PR could update the map once and then add later governed commits without another map update | The repository could satisfy the check while the living process/debt/change inventory was already stale | `FIXED_PENDING_RUNTIME / FRESHNESS_ENFORCED`: CI resolves latest map commit and latest non-map governed commit and requires the latter to be an ancestor of the former; root `AGENTS.md` documents the same invariant; focused source regression locks the gate structure |

# Development priority plan

## P0 — release truth and runtime recovery

1. Resolve #116 externally and prove real runner allocation (`runner_id != 0` + executed steps).
2. Freeze the then-current head and run `CI` + `Deployment Readiness` + `Reproducible Dependencies`.
3. Treat a real failure as application evidence; fix it, update affected P-/PM-items/change log, create a new candidate and restart full CI.
4. Validate PM-002, PM-003, PM-004, PM-005, PM-008, storage/retention portability and Payment Review recovery in executable gates.
5. Run dedicated PostgreSQL/Redis/browser gates, then one complete LIVE_REQUIRED attempt only after general gates are green.

## P1 — controlled debt closure after executable CI exists

1. PM-004: finish exact historical/external consumer audit and delete legacy assignment modules only if safe-removal proof and regressions exist.
2. PM-006: verify actual branch/rules settings and exact required contexts; align them with the gate contract without weakening it.
3. Browser-check centralized Workdesk renderer for M1/M2 responsibility/action semantics.
4. Audit real staging/restore DB for remaining `M2_CONSULTATION_ROUTE` rows before considering a migration/removal of the compatibility enum.

## P2 — post-LIVE evidence and release decision

1. Real Telegram M1/M2 personas and evidence reconciliation.
2. Encrypted backup→restore application/runtime proof.
3. Safe YooKassa test-shop paid/refund expansion.
4. Resolve or explicitly accept every remaining release-relevant PM-item.
5. Release/merge decision; no automatic merge.

# Change impact rule

Whenever anything changes:

1. identify affected `P-*` process(es);
2. update ownership/status/evidence entries;
3. add/update `PM-*` debt for every discovered inconsistency;
4. close debt only with explicit corrective action/evidence;
5. append the change log;
6. make this map the last governed commit in the batch or include it in the same commit;
7. if release evidence had begun, declare a new candidate and restart from full CI.

# Change log

## 2026-08-28 — Living process map introduced and enforced

- Created this mandatory maintained implementation/process inventory.
- Recorded P-00..P-14, source-of-truth matrix, automated gate map and PM-001..PM-007.
- Fixed PM-002 in source by adding `tests/test_postgres_auto_assignment_concurrency.py` to dedicated PostgreSQL Concurrency.
- Added CI `Process map maintenance contract`, requiring this file in every PR diff and requiring it to exist/non-empty on other CI events.
- Bound living-map governance into SYSTEM/ACCEPTANCE/RUNBOOK CURRENT documents.
- Any resulting source/workflow change supersedes the prior candidate and requires restart from full CI after #116 recovery.

## 2026-08-28 — PM-004 legacy assignment path source-contained

- Audited the historical assignment package vs canonical `CaseAssignmentService`.
- Added `architecture_check.py::check_legacy_assignment_imports`; production code cannot import `app.domain.assignment`.
- Kept physical legacy files until exact historical/external-consumer safety can be proven with executable regressions.

## 2026-08-28 — PM-003 M2 legacy bootstrap made compatibility-read-only

- Confirmed current M2 intake starts at `M2_DESCRIPTION_PENDING` and legacy `M2_CONSULTATION_ROUTE` is only a one-way upgrade source.
- Added read-only compatibility policy, normal/forced no-reentry, ORM backstop and focused regressions.
- Kept legacy value readable because historical string-backed rows may exist; no destructive migration is claimed without real DB evidence.

## 2026-08-28 — PM-005 Workdesk composition centralized

- Confirmed route/data module mixed API ownership with responsibility/deep-link JS patching plus separate integrity injection.
- Added `app/api/workdesk_renderer.py` as the single final composition boundary.
- Removed direct HTML/JS patching from `assignment_queue.py` and added `tests/test_workdesk_renderer_contract.py`.

## 2026-08-28 — PM-003 historical-row hydration regression strengthened

- Added in-memory DB regression inserting legacy M2 status through the table layer, loading through ORM, upgrading forward and verifying persistence.
- Added the P0/P1/P2 roadmap so this file is both inventory and execution plan.

## 2026-08-28 — PM-003 CaseService creation boundary aligned

- Found `validate_initial_status()` existed but canonical `CaseService.create_case()` still used generic normalization.
- Moved legacy-status rejection to the domain creation boundary before ORM/persistence; ORM listener remains defensive second layer.
- Added focused service regression proving rejection occurs before database use.

## 2026-08-28 — Root agent/developer governance added

- Added root `AGENTS.md` as a pre-change repository contract for Cursor/Claude/Codex/other agents and developers.
- `AGENTS.md` points to the authoritative CURRENT documents and this map, repeats the mandatory same-batch map-update rule, records source-of-truth boundaries, current release truth and no-auto-merge rule.
- This closes the discoverability gap where the process-map rule previously became obvious only after reading CI/docs; CI remains the enforcement backstop.
- This documentation/governance change creates a new candidate SHA and therefore supersedes the immediately prior release candidate; runtime evidence must start from full CI once #116 is restored.

## 2026-08-28 — PM-008 process-map freshness enforced

- Audited the initial `Process map maintenance contract` and found it only proved that `docs/PROCESS_MAP_CURRENT.md` appeared somewhere in the total PR diff.
- This allowed a long PR to update the map once, then add later code/test/workflow/docs-contract commits while the check still saw the historical map change.
- Strengthened `.github/workflows/ci.yml`: the gate now resolves the latest map commit and latest non-map governed commit in `BASE_SHA..HEAD_SHA` and requires the governed commit to be an ancestor of the map commit. Same-commit changes pass; map-after-code passes; code-after-map fails closed.
- Updated root `AGENTS.md` with the same last-governed-commit invariant.
- Added `tests/test_process_map_governance_contract.py` to lock the presence/freshness source contract.
- This map update is intentionally the final governed commit of the batch, satisfying the new invariant by construction.
- PM-008 is `FIXED_PENDING_RUNTIME`: the gate and regression remain unexecuted until issue #116 is resolved.

## 2026-08-28 — PM-004 legacy assignment containment hardened against bypass imports

- Re-audited `check_legacy_assignment_imports()` and found the first implementation covered direct absolute imports but could be bypassed by relative imports such as `from ..assignment import ...` or literal dynamic imports.
- Added canonical package resolution for relative `ImportFrom` nodes.
- Added literal dynamic-import detection for `importlib.import_module`, imported/aliased `import_module`, and `__import__`, including relative package/level forms when statically resolvable.
- Added `tests/test_legacy_assignment_import_guard.py` with forbidden absolute/relative/dynamic forms and allowed canonical `app.domain.cases.assignment_service` forms.
- Business assignment semantics were not changed; this is containment hardening around the already-canonical `CaseAssignmentService`.
- PM-004 remains open for physical legacy-module removal, but the source boundary is materially stronger and remains runtime pending until #116 is restored.
