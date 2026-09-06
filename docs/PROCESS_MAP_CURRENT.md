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

CI enforces this through `Process map maintenance contract`. The gate checks both that the map changed somewhere in the PR and that no later governed repository commit exists after the latest map update. A code/test/workflow/docs-contract commit after the map makes the map stale and must be followed by another map update. `AGENTS.md` exposes the same rule before a PR is created.

## Status vocabulary

| State | Meaning |
| --- | --- |
| `IMPLEMENTED` | Production path exists in source/schema. |
| `SOURCE_AUDITED` | Path was inspected against the current contract. |
| `RUNTIME_PENDING` | Source exists but required runtime evidence has not executed for the current candidate. |
| `BLOCKED_INFRA` | Required runtime cannot execute because infrastructure is unavailable. |
| `LIVE_PASS` | Required runtime/evidence actually passed for the current candidate SHA. |
| `LIVE_FAIL` | Required LIVE runtime contradicted the contract. |
| `DEBT_OPEN` | Known inconsistency, duplication, legacy residue or unclosed design risk. |
| `FIXED_PENDING_RUNTIME` | Source inconsistency was corrected but required runtime proof has not executed for the current candidate. |

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
| Staff product | `app/api/*`, staff UI modules | admin/lawyer Workdesk, messages, reviews, assignment, consultations, Payment Review/refunds | IMPLEMENTED / FIXED_PENDING_RUNTIME |
| Persistence | `app/models/*`, Alembic | durable Case/Document/Payment/Consultation/Audit facts | IMPLEMENTED / RUNTIME_PENDING |
| Platform/security | `app/security/*`, `app/storage.py` | auth, grants, encryption, audit integrity, backup/restore, key rotation, storage safety | IMPLEMENTED / SOURCE_AUDITED / RUNTIME_PENDING |
| Scheduler/notifications | `app/scheduler/*`, `app/domain/notifications/*` | reminders, slot release, SLA, retention, backup, cleanup, delivery | IMPLEMENTED / RUNTIME_PENDING |
| Release evidence | `.github/workflows/*`, evidence scripts | CI, runtime matrix, manifests, restore proof | IMPLEMENTED / RUNTIME_PENDING |
| Governance | `AGENTS.md`, CURRENT docs, this map, CI/architecture contracts | force process/debt/change traceability and boundary enforcement | IMPLEMENTED / FIXED_PENDING_RUNTIME |

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

- process state — `Case.status` through `CaseService`/approved dedicated domain boundary;
- consent — immutable `ConsentAcceptance`;
- documents — versioned `Document`/review facts;
- M1 lawyer — `Case.assigned_lawyer_id`, assignment audit/SLA;
- money — `Payment` + `PaymentEvent` + provider evidence + Case/Audit context;
- terminal facts — `closed_at`, `close_reason`, `archived_at`, separate retention deletion fact.

M1 rejection client recovery is one continuous Telegram path, not a second state machine: `CLIENT_ACTIONS["M1_REJECTED"] → contact_lawyer → contact_lawyer_scope_guard.scoped_contact_lawyer → m1_rejection_recovery._show_options`. The decision center exposes the existing safe choices to move the same Case to M2, write to the team, or start the explicit close-confirmation flow. The client card must describe those choices truthfully; presentation changes do not themselves mutate `Case.status`.

Static architecture enforcement for `Case.status` is model-aware: obvious direct writes, `setattr(..., "status", ...)`, aliased model classes, typed aliases and SQLAlchemy bulk update forms are rejected outside `CaseService`. This is a source guard, not a substitute for runtime/domain tests.

Evidence: `tests/test_m1_rejection_client_decision_contract.py` locks the rejected-M1 card wording/callback, the scope-guard delegation and presence of M2/message/close options. Runtime execution for the current candidate remains pending.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

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
- `architecture_check.py::check_legacy_assignment_imports` prevents production code from reintroducing historical `app.domain.assignment` semantics through absolute imports, relative `ImportFrom`, or statically-resolvable literal dynamic imports (`importlib.import_module` / `__import__`).

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

Payment Review audit traceability is read-only and exact-payment scoped: `GET /admin/payment-reviews/{payment_id}/history` remains available after a payment leaves the active `PAID_REVIEW` queue, queries the Case audit actions, filters the exact `payment_id` before bounding the visible timeline, and returns only a normalized whitelist. Free-text reconciliation comments, raw `old_value`/`new_value`, provider/reservation payloads and audit-chain integrity metadata are deliberately not exported by this endpoint.

The stale-command 409 recovery projection follows the same minimization rule. `payment_review_conflict_snapshot()` returns only server-authoritative state needed to explain the conflict and prevent overwrite: payment/Case state, normalized resolution decision, exact consultation/slot identifiers where applicable, actor id and resolution time. The persisted free-text `AuditLog.comment`, raw provider/reservation values and unrelated audit payload are not part of the conflict response. The browser keeps its own local draft/comment separately and reloads current server truth before any retry.

The Payment Review composition boundary is now single-owner by responsibility. `app/api/payment_review_center.py::PAYMENT_REVIEW_CENTER_HTML` owns the guided decision hierarchy (`Сейчас`, `Главный следующий шаг`, `Вторичные действия`). `app/api/payment_review_renderer.py` no longer tries to rewrite those guided markers and adds only the read-only exact-payment history panel. This removes the stale second guided-copy layer that raised `RuntimeError` and returned HTTP 500 when the base template evolved. `staff_ui_guards.py` owns authentication/redirect gating. `app/api/payment_review_product.py::NoStoreAPIRoute` applies `Cache-Control: no-store` centrally to Payment Review handlers and propagates the same directive through handler/dependency HTTP exceptions. The history layer is presentation only and does not mutate `Payment`, `Case`, `Consultation` or slot state.

`PaymentLifecycleService` is the canonical application mutation boundary for an existing `Payment.status`. The model listener stamps/records invariant facts but is not the product decision boundary. Static architecture enforcement recognizes obvious aliases/annotations/constructor aliases plus `setattr` and bulk SQLAlchemy status mutations outside `PaymentLifecycleService`.

Evidence: `tests/test_payment_review_history_contract.py`, `tests/test_payment_review_renderer_contract.py`, `tests/test_payment_review_product_contract.py`, `tests/test_payment_review_conflict_privacy_contract.py`, and `tests/test_browser_staff_e2e.py`. Browser Staff E2E run `33910691169` on superseded candidate `dd8e038f554fa9364242c1c9585ffe0655f3c4d4` actually executed and failed; one proven failure was the stale Payment Review composition described in PM-012. Source fix `544f7f5daa6003aa51e0c24097f89bd0ce053781` plus renderer regression commit `8a63323f709d3e6d9e283e749078741faf8fd1df` therefore remain `FIXED_PENDING_RUNTIME` until the exact final candidate reruns the browser/runtime gates.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-06 — Messages and notifications

Flow: `client/staff message → exact Case provenance → Message → priority/read state → notification event → dedupe → delivery → Telegram outcome`.

Rules: history/pagination are Case-bound; draft protection prevents silent loss; delivery failure after a committed mutation must not replay the mutation; reminders/notifications do not establish legal facts.

Message Center document navigation must resolve through the role-aware document review surface `/document-access/review/ui?case_id=...`; it must not use M1 operator Workdesk document actions as a proxy for M2 lawyer responsibility. Current final composition normalizes every composed legacy document action after the guided patch. Duplicate source ownership in the base Message Center template and the guided patch remains explicit debt under PM-013 and must not be confused with a second authorization model.

Anchors: `app/domain/messages/*`, `app/bot/client_message_provenance.py`, `app/domain/notifications/*`, `app/scheduler/notification_dispatcher.py`, `app/api/message_center_role_ui_impl.py`.

Evidence: `tests/test_message_center_role_ui_contract.py` locks successful final composition, absence of the legacy admin-only document action, the canonical role-aware review route and preservation of business-time formatting.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME / DEBT_OPEN`.

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

M2 lawyer responsibility is consultation/slot-driven. Role-safe Message Center document navigation therefore points to the document review surface that evaluates current M2 responsibility instead of depending on M1 `Case.assigned_lawyer_id`.

Anchors: consultation domain services, `case_service.py`, `case_transition_policy.py`, `app/models/case.py`.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-08 — Staff authentication, authorization and Workdesk

Flow: `login → MFA/session where configured → role → Workdesk/Lawyer product → role-safe domain action → audit/session revocation`.

Composition ownership:

- `app/api/assignment_queue.py` — `/admin/workdesk/ui` route/auth + responsibility/queue data APIs, **no direct HTML patching**;
- `app/api/workdesk_ui.py` — base Workdesk document;
- `app/api/workdesk_renderer.py` — single final composition boundary for base UI + responsibility/deep-link behavior + integrity overlay;
- `app/api/workdesk_integrity.py` — integrity producer consumed through renderer;
- `app/api/payment_review_product.py` — single Payment Review product router plus central `NoStoreAPIRoute` response/HTTP-exception cache boundary;
- `app/api/payment_review_center.py` — base guided Payment Review hierarchy;
- `app/api/payment_review_renderer.py` — final read-only exact-payment history composition over the guided base, **no second guided-marker rewrite**;
- `app/api/staff_ui_guards.py` — protected Payment Review authentication/redirect gate and endpoint-level no-store response boundary, **no Payment Review template patching**;
- `app/api/message_center_role_ui_impl.py` — final role-safe Message Center composition; it currently normalizes all legacy admin-only document actions to `/document-access/review/ui?case_id=...` after the guided patch while PM-013 tracks the remaining duplicate source ownership.

Rules: one `(HTTP method, path)` runtime owner; include order must not define security; route/data/auth modules do not mutate foreign templates; M2 responsibility remains consultation/slot-driven rather than M1 assignment-driven.

Evidence: `test_v37_api_import_inventory.py`, `architecture_check.py`, `test_browser_staff_e2e.py`, `test_workdesk_renderer_contract.py`, `test_payment_review_renderer_contract.py`, `test_payment_review_product_contract.py`, `test_message_center_role_ui_contract.py`.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-09 — Audit integrity and immutable evidence

Flow: `business/security action → AuditLog → chained integrity metadata → verification → backup/restore proof`.

Evidence families: `AuditLog` + `AuditChainHead`, immutable consent snapshot, append-only `PaymentEvent`, provider `PaymentWebhookEvent`, Case history.

Payment Review history and stale-409 recovery are privacy-bounded projections of `AuditLog`, not new evidence stores: the underlying audit rows and chain integrity remain authoritative and internal, while staff API/UI expose only the minimum normalized decision/provenance fields needed for reconciliation. Neither projection exports free-text `AuditLog.comment`, raw old/new audit payloads, provider/reservation internals or audit-chain integrity metadata. The Payment Review product route boundary is non-cacheable, with endpoint-level history/UI headers retained as defense in depth, and these projections do not duplicate or rewrite audit evidence.

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

1. prove runner allocation and executable steps for the exact candidate;
2. `CI` + `Deployment Readiness` + `Reproducible Dependencies` pass;
3. dedicated `PostgreSQL Concurrency` → `Telegram Runtime Contracts` → `Browser Staff E2E` pass;
4. one complete manual `LIVE_REQUIRED` run passes and creates exact SHA/run/attempt manifest;
5. real Telegram M1/M2 personas pass with UI ↔ PostgreSQL ↔ Audit/PaymentEvent reconciliation;
6. encrypted backup→restore application/runtime proof passes;
7. safe YooKassa test-shop provider-side paid/refund proof is expanded only then;
8. release/merge decision.

Any source/migration/workflow/evidence-script change after evidence collection begins creates a new candidate SHA and restarts from full CI.

Current runtime truth supersedes the old universal #116 blocker statement. On `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`, Actions allocated runners and executed steps: PostgreSQL Concurrency, Deployment Readiness and Telegram Runtime Contracts completed successfully, while Browser Staff E2E, CI and Reproducible Dependencies executed and failed. Therefore issue #116 is historical/stale as a universal explanation; the final candidate still needs its own exact-SHA evidence chain. No `LIVE_REQUIRED` attempt has been promoted to PASS.

# Data/source-of-truth matrix

| Business fact | Source of truth | Derived/presentation |
| --- | --- | --- |
| Client identity | `User` | Telegram presentation |
| Active/selected matter | `Case` + `ClientCaseContext` | My Case/cards |
| Process stage | `Case.status` | client/staff labels |
| Calculation history | `Calculation` | latest derived by query |
| Consent | immutable `ConsentAcceptance` | consent history/UI |
| M1 lawyer responsibility | Case assignment + assignment audit/SLA | Workdesk |
| M2 lawyer responsibility | `Consultation.lawyer_id` / selected slot | consultation UI/Workdesk/document review |
| Document state/version | `Document` | readiness/blockers |
| Document byte location | canonical portable `Document.file_path` | `LocalStorageService` resolution |
| Message history | `Message` | unread/priority projection |
| Consultation booking | `Consultation` + `ConsultationSlot` | booking UI |
| Current payment | `Payment` | payment UI |
| Financial history | append-only `PaymentEvent` | timeline |
| Provider evidence | `PaymentWebhookEvent` | reconciliation |
| Case/action audit | `AuditLog`/Case history | timelines/audit center + privacy-bounded Payment Review history/409 projection |
| Closure/archive/delete | separate lifecycle timestamps/reasons | terminal/read-only UI |
| Client inactivity | client/Case activity + stage-entry evidence | reminder scheduling |
| Runtime draft/navigation | Redis FSM | never legal source of truth |

# Automated verification map

## General pre-live gates

### `CI`

- `Process map maintenance contract`: file must exist, must be changed in the PR, and its latest commit must be at least as new as the latest governed repository commit;
- `tests/test_process_map_governance_contract.py` locks the presence/freshness source contract and root `AGENTS.md` wording;
- source compile;
- architecture check, including absolute/relative/literal-dynamic legacy assignment containment and alias/type-aware direct/bulk `Case.status` and `Payment.status` mutation containment;
- SQLite suite, including CaseService/M2 compatibility, Workdesk renderer, legacy-assignment import guard, status-mutation architecture regressions, rejected-M1 client decision, Payment Review history/privacy/409/renderer/router contracts and role-safe Message Center composition;
- Alembic current/idempotency/check + clean migration smoke;
- PostgreSQL migration + technical encrypted backup/restore drill;
- production container build/start/health.

Executed evidence on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`: main CI run `33910691133` did allocate a runner; the SQLite application suite executed and failed, and Process Map governance also failed. Downstream jobs cancelled through `needs` are not PASS evidence. This is useful defect evidence but is not current-candidate release proof.

### `Deployment Readiness`

- Redis FSM persistence;
- Docker/Compose deployment contract.

A real run on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4` completed successfully. Current-candidate rerun is still required.

### `Reproducible Dependencies`

- locked test/production images;
- dependency verification + `pip check`;
- complete suite in locked test image.

Executed evidence on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`: run `33910691239`, job `101146135918`, executed the locked suite and failed with **310 failed, 1465 passed, 19 skipped, 5 errors in 71.96s**. The diagnostic artifact also contained ResourceWarnings for unclosed aiosqlite/files and several runtime/source-contract mismatches. Some tracebacks referenced `/usr/local/lib/python3.11/site-packages/app/...`; this is only a possible import-path clue and is **not** yet a proven dependency root cause.

## Dedicated gates

### `PostgreSQL Concurrency`

Must cover multi-Case/calculation races, payment races, refund retries, staff concurrency and **auto-assignment final-capacity race**. A real run on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4` completed successfully; exact final-candidate proof remains required.

### `Telegram Runtime Contracts`

Redis-backed FSM restart and exact Case binding. A real run on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4` completed successfully; exact final-candidate proof remains required.

### `Browser Staff E2E`

Staff role/session paths, Payment Review stale two-tab recovery, active→terminal exact-payment history rendering/privacy assertions, role-safe Message Center/document navigation and current canonical Workdesk route/rendering.

Executed evidence on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`: run `33910691169` actually reached the browser job and failed. Source/log inspection localized two deterministic HTTP 500 composition defects: stale Payment Review guided-marker rewriting (PM-012) and exact-one Message Center legacy document-link rewriting after the guided patch (PM-013). Both are source-fixed and regression-locked, but remain `FIXED_PENDING_RUNTIME` until the exact final candidate executes the browser gate.

## Manual `LIVE_REQUIRED`

One exact SHA/run/attempt must prove PostgreSQL, Redis, real Telegram delivery, browser, YooKassa test-shop provider baseline and aggregate manifest. `LIVE_REQUIRED` remains `PENDING`; no source audit, dedicated gate, or superseded-SHA run is equivalent to LIVE evidence.

# Known inconsistency and debt register

| ID | Area | Finding | Risk | State / action |
| --- | --- | --- | --- | --- |
| PM-001 | Release infra | Historical Actions jobs ended before runner allocation (`runner_id=0`, empty/null steps), but later `dd8e...` jobs allocated runners and executed | Treating #116 as a universal blocker hides real application failures; treating one recovered SHA as permanent infra health would also overclaim | `RUNTIME_PENDING`; #116 is stale as a universal blocker. Keep/close it only after exact-head matrix confirms runner health and the issue description is reconciled |
| PM-002 | PostgreSQL dedicated gate | Auto-assignment capacity race existed in tests/acceptance/LIVE_REQUIRED but was omitted from dedicated PostgreSQL workflow | Dedicated gate could pass without assignment race proof | `FIXED_PENDING_RUNTIME`; test is included in dedicated workflow; superseded dd8e dedicated gate passed, current-candidate rerun required |
| PM-003 | M2 state machine | Historical `M2_CONSULTATION_ROUTE` can exist in string-backed old rows, while canonical current M2 begins at `M2_DESCRIPTION_PENDING` | Re-entry would split semantics; immediate deletion could break historical rows | `FIXED_PENDING_RUNTIME / COMPATIBILITY_READ_ONLY`; CaseService initial validation + no-reentry transition rule + ORM backstop + historical-row hydration/forward-upgrade regression; physical removal deferred until real DB audit |
| PM-004 | Assignment architecture | Historical `AssignmentEngine`/`WorkloadService` coexist with hardened `CaseAssignmentService`; initial containment missed relative/literal-dynamic imports | Reintroduced legacy code could bypass locking/capacity/audit semantics while evading architecture check | `DEBT_OPEN / SOURCE_CONTAINED / HARDENED_PENDING_RUNTIME`; guard resolves relative `ImportFrom` and statically-resolvable literal dynamic imports; focused regression added; physical deletion waits for historical/external-consumer audit + executable regressions |
| PM-005 | Workdesk composition | Final UI was assembled by route-level responsibility JS patch plus separate integrity injection | Distributed UI ownership could omit/duplicate behavior | `FIXED_PENDING_RUNTIME / COMPOSITION_CENTRALIZED`; single `workdesk_renderer.py`, route module no longer patches HTML, regression locks boundary |
| PM-006 | Repository governance | Private-repo ruleset required contexts cannot be independently enumerated through current GitHub API/plan | Formal branch protection contract is not API-proven | `DEBT_OPEN`; CI governance + root `AGENTS.md` implemented; manually verify repository rules when available |
| PM-007 | Runtime evidence | Runner execution recovered and exposed real failures; however current source fixes/regressions are newer than dd8e and have not yet completed the full exact-head chain | Superseded successes/failures cannot be promoted to current release evidence | `RUNTIME_PENDING`; dd8e evidence is diagnostic only. Exact final candidate must rerun all ordered gates |
| PM-008 | Living-map governance | Initial CI rule only required `PROCESS_MAP_CURRENT.md` to appear somewhere in total PR diff | A long PR could pass while the map had become stale | `FIXED_PENDING_RUNTIME / FRESHNESS_ENFORCED`; latest non-map governed commit must be an ancestor of latest map commit; AGENTS + focused regression lock the rule |
| PM-009 | Case/Payment mutation architecture | Initial `Case.status` check depended on literal variable name `case`; Payment check relied mainly on names containing `payment` and direct `Payment` class reference. Typed aliases, `setattr`, model aliases and several bulk SQLAlchemy forms could evade the static boundary | Product code could bypass `CaseService` or `PaymentLifecycleService`, splitting process/audit/SLA or financial timestamp/ledger semantics | `FIXED_PENDING_RUNTIME / ALIAS_AWARE_GUARD`; architecture check infers obvious model variables from imports/aliases/annotations/constructors/simple aliases, preserves conventional names, catches `setattr(..., "status", ...)` and model-referenced bulk forms. This is static containment, not a claim arbitrary reflection is impossible |
| PM-010 | M1 rejection Telegram presentation | The `M1_REJECTED` backend/client recovery already exposed M2, message/team and close choices, but `ClientCaseView` described the callback as contact-only | Client could miss valid next steps and the primary Case card contradicted the actual callback/product flow | `FIXED_PENDING_RUNTIME / PRESENTATION_ALIGNED`; card now describes the real decision center; current-candidate runtime still required |
| PM-011 | Payment Review audit traceability/privacy | Terminal history originally lacked a safe exact-payment surface and stale-409 conflict projection exposed persisted free-text reconciliation comments | Terminal/stale reconciliation could leak more audit context than needed | `FIXED_PENDING_RUNTIME / SAFE_HISTORY_UI_ADDED / NON_CACHEABLE_ROUTER_BOUNDARY / CONFLICT_PRIVACY_MINIMIZED`; privacy contracts are source-fixed. Browser execution on dd8e exposed a separate renderer composition defect tracked as PM-012; no current-candidate PASS yet |
| PM-012 | Payment Review renderer composition | `payment_review_renderer.py` reapplied guided-copy marker rewrites even though `PAYMENT_REVIEW_CENTER_HTML` already owned the guided hierarchy; evolved base markup broke exact-one markers | Protected Payment Review rendered HTTP 500 in real Browser Staff E2E instead of the operator decision surface | `FIXED_PENDING_RUNTIME`; commit `544f7f5daa6003aa51e0c24097f89bd0ce053781` removes the stale guided rewrite, keeps only exact-payment history injection; `8a63323f709d3e6d9e283e749078741faf8fd1df` locks single-owner/deterministic/privacy behavior; exact final-SHA browser rerun required |
| PM-013 | Message Center document navigation/composition | Base Message Center and guided patch both contain the legacy admin Workdesk document action; final role UI previously required exactly one marker and also targeted a non-canonical `/document-access/ui` path | Multiple composed markers caused HTTP 500; M2 lawyer navigation could be coupled to the wrong staff surface | `FIXED_PENDING_RUNTIME / DEBT_OPEN`; commit `00910ea6d573875a7efe3672b0ff7c624e553a9f` normalizes every composed legacy action to canonical `/document-access/review/ui?case_id=...`, and `91bfc7248f903875d062a7d0533efc89b55d6183` adds focused regression. Duplicate source ownership remains debt until both source templates are safely collapsed; exact final-SHA browser rerun required |

# Development priority plan

## P0 — current release truth and executable failures

1. Keep this Process Map as the final governed commit for the current batch, then treat that map commit SHA as the new candidate.
2. Execute `CI` + `Deployment Readiness` + `Reproducible Dependencies` on that exact SHA. Runners now execute, so failures are application/runtime evidence rather than automatically `BLOCKED_INFRA`.
3. Fix the first deterministic failing contract from the executable CI/locked suite without weakening governance or source-of-truth boundaries; after any governed fix, update this map last and restart exact-SHA evidence.
4. Execute dedicated PostgreSQL Concurrency → Telegram Runtime Contracts → Browser Staff E2E. Specifically prove PM-012 and PM-013 no longer return HTTP 500 and preserve M1/M2 role semantics.
5. Only after all general/dedicated gates are green, run one complete `LIVE_REQUIRED` attempt for the same exact SHA.

## P1 — controlled debt closure after executable CI is green

1. PM-013: collapse duplicate Message Center document-route ownership in the base/guided sources so final composition does not need compatibility normalization; preserve canonical `/document-access/review/ui` and M2 consultation/slot responsibility.
2. PM-004: finish exact historical/external consumer audit and delete legacy assignment modules only if safe-removal proof and regressions exist.
3. PM-006: verify actual branch/rules settings and exact required contexts; align them with the gate contract without weakening it.
4. Browser-check centralized Workdesk renderer for M1/M2 responsibility/action semantics.
5. Audit real staging/restore DB for remaining `M2_CONSULTATION_ROUTE` rows before considering compatibility enum removal.
6. Review PM-009 runtime results before deciding whether a later model/session-level mutation authorization backstop is justified.
7. Exercise PM-011 with authenticated browser/API sessions against active and terminal Payment Review records, including stale 409 server-truth fields, terminal history and cache headers.

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

## 2026-08-28 — PM-004 legacy assignment path source-contained

- Audited the historical assignment package vs canonical `CaseAssignmentService`.
- Added `architecture_check.py::check_legacy_assignment_imports`; production code cannot import `app.domain.assignment`.
- Kept physical legacy files until exact historical/external-consumer safety can be proven with executable regressions.

## 2026-08-28 — PM-003 M2 legacy bootstrap made compatibility-read-only

- Confirmed current M2 intake starts at `M2_DESCRIPTION_PENDING` and legacy `M2_CONSULTATION_ROUTE` is only a one-way upgrade source.
- Added read-only compatibility policy, normal/forced no-reentry, ORM backstop and focused regressions.
- Kept legacy value readable because historical string-backed rows may exist; no destructive migration is claimed without real DB evidence.

## 2026-08-28 — PM-005 Workdesk composition centralized

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

## 2026-08-28 — PM-008 process-map freshness enforced

- Strengthened `.github/workflows/ci.yml`: latest non-map governed commit must be an ancestor of latest process-map commit. Same-commit changes pass; map-after-code passes; code-after-map fails closed.
- Updated root `AGENTS.md` and added `tests/test_process_map_governance_contract.py`.

## 2026-08-28 — PM-004 legacy assignment containment hardened against bypass imports

- Added relative-module resolution plus detection for `importlib.import_module`, aliased `import_module`, and `__import__` when statically resolvable.
- Added `tests/test_legacy_assignment_import_guard.py` with forbidden bypass forms and allowed canonical assignment-service imports.

## 2026-08-28 — PM-009 Case/Payment mutation guards made alias-aware

- Expanded model symbol resolution for `Case` and `Payment`, including imports/aliases/annotations/constructors and simple alias propagation.
- Expanded direct mutation detection to `setattr`, model-referenced SQLAlchemy bulk forms and equivalent statically-resolvable writes.
- Added `tests/test_status_mutation_architecture_guard.py` with negative controls.

## 2026-08-29 — PM-010 M1 rejection client decision presentation aligned

- Re-audited the functional/UX rejection requirement against the exact production path and confirmed the decision center already existed.
- Changed only the client presentation contract to `Выбрать, что делать дальше` with explicit consultation/close/team wording.
- Added `tests/test_m1_rejection_client_decision_contract.py`.

## 2026-08-31 — PM-011 Payment Review safe audit history added

- Added `app/api/payment_review_history.py` and admin-only `GET /admin/payment-reviews/{payment_id}/history`.
- Added a strict response whitelist and `tests/test_payment_review_history_contract.py`.

## 2026-08-31 — PM-011 Payment Review terminal history surfaced in protected UI

- Added `app/api/payment_review_renderer.py` and exact-payment history panel; `staff_ui_guards.py` remained authentication/redirect boundary.
- Added `tests/test_payment_review_renderer_contract.py` and extended `tests/test_browser_staff_e2e.py` for active→terminal/stale history/privacy behavior.

## 2026-08-31 — PM-011 Payment Review no-store boundary centralized

- Added `NoStoreAPIRoute` to the Payment Review product router.
- Added `tests/test_payment_review_product_contract.py` to lock non-cacheable response/HTTP-exception behavior.

## 2026-09-02 — PM-011 Payment Review stale-409 privacy minimized

- Removed free-text `AuditLog.comment` from `payment_review_conflict_snapshot()` while preserving the fields required to explain the winning server decision and prevent stale overwrite.
- Added `tests/test_payment_review_conflict_privacy_contract.py`.

## 2026-09-06 — Runtime truth corrected after runners resumed execution

- Superseded the old blanket `BLOCKED_INFRA` interpretation: on `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`, Actions allocated runners and actually executed steps.
- Recorded real successes for PostgreSQL Concurrency, Deployment Readiness and Telegram Runtime Contracts on that superseded SHA.
- Recorded real failures for Browser Staff E2E, main CI and Reproducible Dependencies instead of attributing them to empty runner jobs.
- Recorded locked-suite evidence exactly: `310 failed, 1465 passed, 19 skipped, 5 errors in 71.96s` for run `33910691239` / job `101146135918`.
- Kept all superseded-SHA evidence diagnostic only; it does not promote the new candidate or `LIVE_REQUIRED`.

## 2026-09-06 — PM-012 Payment Review renderer stale composition removed

- Real Browser Staff E2E run `33910691169` exposed HTTP 500 from a second guided-marker rewrite layered over a base template that already owned `Сейчас`, `Главный следующий шаг` and `Вторичные действия`.
- Commit `544f7f5daa6003aa51e0c24097f89bd0ce053781` removed the obsolete guided-copy composition from `payment_review_renderer.py`; renderer now adds only exact-payment history.
- Commit `8a63323f709d3e6d9e283e749078741faf8fd1df` updated `tests/test_payment_review_renderer_contract.py` to lock single-owner deterministic composition and privacy behavior.
- Status remains `FIXED_PENDING_RUNTIME`; no exact-final-SHA browser PASS is claimed yet.

## 2026-09-06 — PM-013 Message Center role-safe document composition hardened

- Real browser failure analysis showed the guided Message Center composition can contain more than one legacy admin Workdesk document action, while `message_center_role_ui_impl.py` required exactly one marker; the role-safe replacement also pointed to non-canonical `/document-access/ui`.
- Commit `00910ea6d573875a7efe3672b0ff7c624e553a9f` changed final composition to normalize every composed legacy action to canonical `/document-access/review/ui?case_id=...`, preserving M2 consultation/slot authorization semantics and avoiding exact-one HTTP 500 failure.
- Commit `91bfc7248f903875d062a7d0533efc89b55d6183` added `tests/test_message_center_role_ui_contract.py` for successful composition, legacy-route absence, canonical route presence and business-time contract preservation.
- Duplicate document-link source ownership in the base Message Center and guided patch remains `DEBT_OPEN`; collapse is P1 after executable P0 gates are green.
- This Process Map update is intentionally the final governed commit of the batch. The resulting map commit becomes the new candidate and must rerun the ordered exact-SHA evidence chain; `LIVE_REQUIRED` remains `PENDING`.
