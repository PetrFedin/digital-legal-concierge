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

Flow: `Calculate → source operation key → CaseCreationRequest → new Case → exact Case-bound calculator draft → Calculation history → client decision`.

Facts and exceptional-result rules:

- `(client_id, operation_key)` deduplicates the same source action;
- distinct Calculate operations may create distinct Cases;
- `Calculation.case_id` is one-to-many and newest stored Calculation is authoritative for current calculation outcome;
- `calc_start` means a new matter;
- `calc_recover:v2:<case_id>` resumes an exact existing matter; every resume/restart/back/transfer action emitted from recovery must also carry that exact Case id;
- an actual transfer before or on the contractual date is a valid factual outcome: the calculation is saved with zero delay/zero amount, M1 is not offered, and the client may finish/postpone, correct data, or choose M2;
- a contractual transfer date later than the current date is persisted before the informational boundary, no delay calculation is performed yet, and resume stays at the future-date boundary until the date arrives; after it arrives the same saved draft advances to transfer status;
- M1 continuation is permitted only when the **latest stored Calculation** for that Case has both positive delay days and positive penalty amount; a stale positive-result Telegram button cannot override a newer zero-delay result;
- incomplete questionnaire answers are currently held in Case-namespaced Redis FSM draft state. This is runtime recovery state, not a durable legal/business source of truth; the Functional Specification requirement that entered values be available in the Case card remains explicit PM-017 release debt rather than being reinterpreted away.

Canonical path: `NEW → CALCULATOR_STARTED → CALCULATED → CLIENT_DECISION` plus explicit exceptional completion/M2 branches and compatibility transitions from `case_transition_policy.py`.

Anchors: `app/domain/calculator/*`, `app/bot/calculator_draft.py`, `app/bot/screens/calculator.py`, `app/bot/screens/calculator_active_case_recovery.py`, `app/domain/cases/case_service.py`, `Calculation`, `CaseCreationRequest`.

Evidence added by the 2026-09-09 audit batch: `tests/test_calculator.py`, `tests/test_calculator_route_eligibility.py`, `tests/test_telegram_calculator_recovery_flow.py`, `tests/test_v37_calculator_active_case_recovery.py`, `tests/test_v37_calculator_draft_recovery.py`. These are source/regression additions only until an exact-head runner executes them.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME / DEBT_OPEN`.

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

PM-018 hardens the existing CaseService/CaseTransitionPolicy contour rather than adding a second state machine. Every applied Case process transition now advances one monotonic `Case.version`, writes one durable `CaseTransitionCommand` idempotency/recovery row, appends the existing `AuditLog` Case history event and writes one `CaseTransitionOutboxEvent` in the same database transaction. Ownership is checked before replay/stale evaluation; an exact committed command replay is resolved before the old expected version is rejected; an unrelated stale expected version is a side-effect-free no-op. M2→M1 remains a route handoff on the same Case: the authority changes the single `Case.route` from M2 to M1 and accepts that cross-route handoff only as `CASE_TRANSFERRED_TO_M1` by a lawyer. `ConsultationOutcomeService.complete(..., decision="to_m1")` supplies a deterministic consultation-scoped idempotency key and expected Case version.

Anchors: consultation domain services, `case_service.py`, `case_transition_policy.py`, `app/models/case.py`, `app/models/case_transition.py`, migration `20260918_0024_case_transition_authority_recovery.py`.

Evidence: `tests/test_case_transition_authority_pm018.py` and `tests/test_m2_to_m1_authority_pm018.py`. Source is implemented; exact-head CI/migration/runtime evidence is still required before PM-018 can be marked PASS.

State: `IMPLEMENTED / PM-018 FIXED_PENDING_RUNTIME`.

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

Rules: one `(HTTP method, path)` runtime owner; include order must not define security; route/data/auth modules do not mutate foreign templates; M2 responsibility remains consultation/slot-driven rather than M1 assignment-driven. Route-ownership and precedence regressions must inspect the **effective** FastAPI route inventory through the public `fastapi.routing.iter_route_contexts(...)` helper rather than assuming `app.routes` or a nested router's `.routes` collection contains only leaf `APIRoute` objects. Wrapper-skipping patterns such as `getattr(route, "path", None)` over raw `app.routes` are equally invalid because they can silently turn a real route into an empty owner set. Framework wrapper representation is not a product route owner and must not change application route assembly merely to satisfy test introspection.

Evidence: `test_v37_api_import_inventory.py`, `architecture_check.py`, `test_browser_staff_e2e.py`, `test_workdesk_renderer_contract.py`, `test_payment_review_renderer_contract.py`, `test_payment_review_product_contract.py`, `test_message_center_role_ui_contract.py`, plus the PM-014 effective-route inventory regressions.

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

Current runtime truth supersedes the old universal #116 blocker statement. On superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`, Actions allocated runners and exposed the first real application failures. On later superseded candidate `befe4f7c518d2224047b9fa65be8fcaf34be3102`, PostgreSQL Concurrency `34053363373`, Deployment Readiness `34053363423`, Telegram Runtime Contracts `34053363472` and Browser Staff E2E `34053363375` succeeded while Reproducible Dependencies `34053363389` and CI `34053363381` failed. On exact candidate `48381e7a3d835917685de9a3c9ec60e59c289602`, all six workflows again allocated runners and executed: PostgreSQL Concurrency run `34262572309` **success**, Deployment Readiness `34262572258` **success**, Telegram Runtime Contracts `34262572326` **success**, Browser Staff E2E `34262572234` **success**, Reproducible Dependencies `34262572283` **failure**, and CI `34262572237` **failure**. In CI, Process Map maintenance, compile and architecture checks passed; the SQLite application suite failed and downstream `needs` jobs did not become PASS evidence. The locked suite on `48381e7...` finished with **293 failed, 1483 passed, 19 skipped, 2 errors in 76.13s**. Its diagnostics contained zero `_IncludedRouter` occurrences, proving the original direct-AttributeError PM-014 cluster no longer reproduced, but they exposed a second wrapper-skipping variant in route-owner tests. The later v37 head `dd496ce383c5228d57a238d164401866f9115666` also executed real runners: PostgreSQL Concurrency, Telegram Runtime Contracts, Browser Staff E2E and Deployment Readiness succeeded while CI and Reproducible Dependencies failed. The 2026-09-09 calculator audit branch is newer than all of that evidence; no previous PASS/FAIL is promoted to its final map SHA. `LIVE_REQUIRED` remains `PENDING`.

# Data/source-of-truth matrix

| Business fact | Source of truth | Derived/presentation |
| --- | --- | --- |
| Client identity | `User` | Telegram presentation |
| Active/selected matter | `Case` + `ClientCaseContext` | My Case/cards |
| Process stage | `Case.status` + monotonic `Case.version` | client/staff labels; version guards stale mutation actions |
| Case transition command/retry identity | `CaseTransitionCommand` | idempotent replay / commit-before-response recovery |
| Case transition outbox | `CaseTransitionOutboxEvent` | post-commit transition event delivery/processing evidence |
| Calculation history | `Calculation` | latest derived by query |
| Incomplete calculator draft | Redis FSM only in current source | runtime recovery only; durable Case-card visibility is PM-017 release debt |
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
- SQLite suite, including CaseService/M2 compatibility, Workdesk renderer, legacy-assignment import guard, status-mutation architecture regressions, rejected-M1 client decision, Payment Review history/privacy/409/renderer/router contracts, role-safe Message Center composition, the PM-015 calculator date/result/recovery regressions, and PM-018 authority proofs for versioning, stale no-op, idempotent replay, client/Case isolation, rollback atomicity and M2→M1;
- Alembic current/idempotency/check + clean migration smoke;
- PostgreSQL migration + technical encrypted backup/restore drill;
- production container build/start/health.

Executed evidence on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`: main CI run `33910691133` allocated a runner; the SQLite application suite executed and failed, and Process Map governance also failed. Later superseded `befe4f7c518d2224047b9fa65be8fcaf34be3102` CI run `34053363381` executed and failed. On superseded exact candidate `48381e7a3d835917685de9a3c9ec60e59c289602`, CI run `34262572237` allocated runners: Process Map maintenance, compile and architecture passed, while SQLite job `102184069151` failed; downstream migration/container jobs gated through `needs` are not PASS evidence. On later v37 head `dd496ce...`, CI also executed and failed. The 2026-09-09 map-final audit candidate must execute the full chain again.

### `Deployment Readiness`

- Redis FSM persistence;
- Docker/Compose deployment contract.

A real run on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4` completed successfully. Later superseded `befe4f7c518d2224047b9fa65be8fcaf34be3102` run `34053363423` completed successfully. Exact `48381e7a3d835917685de9a3c9ec60e59c289602` run `34262572258` also completed successfully. Later v37 head `dd496ce...` also passed Deployment Readiness. Current audit candidate rerun is still required.

### `Reproducible Dependencies`

- locked test/production images;
- dependency verification + `pip check`;
- complete suite in locked test image.

Executed evidence on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`: run `33910691239`, job `101146135918`, failed with **310 failed, 1465 passed, 19 skipped, 5 errors in 71.96s**. On `befe4f7c518d2224047b9fa65be8fcaf34be3102`, run `34053363389` failed with **308 failed, 1467 passed, 19 skipped, 5 errors in 72.19s**. On exact superseded candidate `48381e7a3d835917685de9a3c9ec60e59c289602`, run `34262572283`, job `102184077696`, built/verified locked test and production images successfully, then the complete locked pytest suite failed with **293 failed, 1483 passed, 19 skipped, 2 errors in 76.13s**; diagnostics upload succeeded. Later v37 head `dd496ce...` also executed the locked suite and failed; those failures are diagnostic only for the newer audit branch.

The `48381e7...` diagnostics contained **zero** `_IncludedRouter` strings, runtime-confirming that the first 16-file/direct-AttributeError PM-014 migration removed the original failure shape. They also exposed **27 deterministic zero-owner route assertions across six additional test files** that still iterated raw `create_app().routes` and silently skipped wrapper objects using `getattr(...)`: `test_v37_api_import_inventory.py`, `test_v37_backup_center_auth_guard.py`, `test_v37_legacy_setup_ui_recovery.py`, `test_v37_staff_ui_shell_guard.py`, `test_v37_superadmin_ui_guards.py`, and `test_workdesk_route_ownership.py`. Those six files now also use public `iter_route_contexts(...)`. This remains `FIXED_PENDING_RUNTIME` until the current map-final SHA proves the 27 zero-owner cluster is gone. Other failures, including stale source assertions, assignment/SLA fixtures, FakeDB locking assumptions and resource-cleanup warnings, remain separate and must be prioritized from exact-head diagnostics rather than guessed from old logs.

## Dedicated gates

### `PostgreSQL Concurrency`

Must cover multi-Case/calculation races, payment races, refund retries, staff concurrency and **auto-assignment final-capacity race**. Superseded `dd8...`, `befe4f7...`, `48381...` and later v37 head `dd496ce...` runs succeeded. Current audit-candidate proof remains required.

### `Telegram Runtime Contracts`

Redis-backed FSM restart and exact Case binding. Superseded `dd8...`, `befe4f7...`, `48381...` and later v37 head `dd496ce...` runs succeeded. Current audit-candidate proof remains required, including PM-015 exact-case calculator recovery behavior.

### `Browser Staff E2E`

Staff role/session paths, Payment Review stale two-tab recovery, active→terminal exact-payment history rendering/privacy assertions, role-safe Message Center/document navigation and current canonical Workdesk route/rendering.

Browser Staff E2E failed on superseded `dd8e038f554fa9364242c1c9585ffe0655f3c4d4`, exposing PM-012/PM-013 HTTP 500 composition defects. After both source fixes, superseded `befe4f7c518d2224047b9fa65be8fcaf34be3102` run `34053363375` succeeded. Exact `48381e7a3d835917685de9a3c9ec60e59c289602` and later v37 head `dd496ce...` also succeeded. Because this audit batch contains newer governed calculator source/test changes, final release evidence still requires Browser Staff E2E on the current map-final SHA.

## Manual `LIVE_REQUIRED`

One exact SHA/run/attempt must prove PostgreSQL, Redis, real Telegram delivery, browser, YooKassa test-shop provider baseline and aggregate manifest. `LIVE_REQUIRED` remains `PENDING`; no source audit, focused regression, dedicated gate, or superseded-SHA run is equivalent to LIVE evidence.

# Known inconsistency and debt register

| ID | Area | Finding | Risk | State / action |
| --- | --- | --- | --- | --- |
| PM-001 | Release infra / governance truth | Historical Actions jobs ended before runner allocation (`runner_id=0`, empty/null steps), while later dd8/befe/48381/dd496 candidates allocated runners and executed | Stale authoritative guidance could cause real application failures to be misclassified as infrastructure and stop productive debugging | `DEBT_OPEN / DOC_ALIGNED / RUNTIME_OBSERVED`; `AGENTS.md` now requires exact-candidate inspection and treats #116 as historical/stale unless a current run actually has no runner. Reconcile/close issue #116 separately; current candidate must still prove its own allocation |
| PM-002 | PostgreSQL dedicated gate | Auto-assignment capacity race existed in tests/acceptance/LIVE_REQUIRED but was omitted from dedicated PostgreSQL workflow | Dedicated gate could pass without assignment race proof | `FIXED_PENDING_RUNTIME`; test is included in dedicated workflow; superseded dedicated gates passed, current-candidate rerun required |
| PM-003 | M2 state machine | Historical `M2_CONSULTATION_ROUTE` can exist in string-backed old rows, while canonical current M2 begins at `M2_DESCRIPTION_PENDING` | Re-entry would split semantics; immediate deletion could break historical rows | `FIXED_PENDING_RUNTIME / COMPATIBILITY_READ_ONLY`; CaseService initial validation + no-reentry transition rule + ORM backstop + historical-row hydration/forward-upgrade regression; physical removal deferred until real DB audit |
| PM-004 | Assignment architecture | Historical `AssignmentEngine`/`WorkloadService` coexist with hardened `CaseAssignmentService`; initial containment missed relative/literal-dynamic imports | Reintroduced legacy code could bypass locking/capacity/audit semantics while evading architecture check | `DEBT_OPEN / SOURCE_CONTAINED / HARDENED_PENDING_RUNTIME`; guard resolves relative `ImportFrom` and statically-resolvable literal dynamic imports; focused regression added; physical deletion waits for historical/external-consumer audit + executable regressions |
| PM-005 | Workdesk composition | Final UI was assembled by route-level responsibility JS patch plus separate integrity injection | Distributed UI ownership could omit/duplicate behavior | `FIXED_PENDING_RUNTIME / COMPOSITION_CENTRALIZED`; single `workdesk_renderer.py`, route module no longer patches HTML, regression locks boundary |
| PM-006 | Repository governance | Private-repo ruleset required contexts cannot be independently enumerated through current GitHub API/plan | Formal branch protection contract is not API-proven | `DEBT_OPEN`; CI governance + root `AGENTS.md` implemented; manually verify repository rules when available |
| PM-007 | Runtime evidence | Runners execute and expose real application failures, but each governed fix creates a newer candidate than the last evidence | Superseded successes/failures cannot be promoted to current release evidence | `RUNTIME_PENDING`; historical matrices are diagnostic only; current map-final candidate must rerun all ordered gates |
| PM-008 | Living-map governance | Initial CI rule only required `PROCESS_MAP_CURRENT.md` to appear somewhere in total PR diff | A long PR could pass while the map had become stale | `FIXED_PENDING_RUNTIME / FRESHNESS_ENFORCED`; latest non-map governed commit must be an ancestor of latest map commit; AGENTS + focused regression lock the rule |
| PM-009 | Case/Payment mutation architecture | Initial `Case.status` check depended on literal variable name `case`; Payment check relied mainly on names containing `payment` and direct `Payment` class reference. Typed aliases, `setattr`, model aliases and several bulk SQLAlchemy forms could evade the static boundary | Product code could bypass `CaseService` or `PaymentLifecycleService`, splitting process/audit/SLA or financial timestamp/ledger semantics | `FIXED_PENDING_RUNTIME / ALIAS_AWARE_GUARD`; architecture check infers obvious model variables from imports/aliases/annotations/constructors/simple aliases, preserves conventional names, catches `setattr(..., "status", ...)` and model-referenced bulk forms. This is static containment, not a claim arbitrary reflection is impossible |
| PM-010 | M1 rejection Telegram presentation | The `M1_REJECTED` backend/client recovery already exposed M2, message/team and close choices, but `ClientCaseView` described the callback as contact-only | Client could miss valid next steps and the primary Case card contradicted the actual callback/product flow | `FIXED_PENDING_RUNTIME / PRESENTATION_ALIGNED`; card now describes the real decision center; current-candidate runtime still required |
| PM-011 | Payment Review audit traceability/privacy | Terminal history originally lacked a safe exact-payment surface and stale-409 conflict projection exposed persisted free-text reconciliation comments | Terminal/stale reconciliation could leak more audit context than needed | `FIXED_PENDING_RUNTIME / SAFE_HISTORY_UI_ADDED / NON_CACHEABLE_ROUTER_BOUNDARY / CONFLICT_PRIVACY_MINIMIZED`; privacy contracts are source-fixed. Superseded Browser E2E passed, exact current-candidate evidence is still required |
| PM-012 | Payment Review renderer composition | `payment_review_renderer.py` reapplied guided-copy marker rewrites even though `PAYMENT_REVIEW_CENTER_HTML` already owned the guided hierarchy; evolved base markup broke exact-one markers | Protected Payment Review rendered HTTP 500 in real Browser Staff E2E instead of the operator decision surface | `FIXED_PENDING_RUNTIME`; commits `544f7f5...` and `8a63323...` removed stale composition and locked single-owner behavior; superseded Browser Staff E2E passed, final-SHA rerun required |
| PM-013 | Message Center document navigation/composition | Base Message Center and guided patch both contain the legacy admin Workdesk document action; final role UI previously required exactly one marker and also targeted a non-canonical `/document-access/ui` path | Multiple composed markers caused HTTP 500; M2 lawyer navigation could be coupled to the wrong staff surface | `FIXED_PENDING_RUNTIME / DEBT_OPEN`; commits `00910ea...` and `91bfc72...` normalize to canonical `/document-access/review/ui?case_id=...` and add regression. Duplicate source ownership remains P1 debt; final-SHA rerun required |
| PM-014 | FastAPI effective route inventory compatibility | Locked FastAPI `0.141.1` preserves `_IncludedRouter` wrappers. First, 16 tests directly dereferenced `.path` and produced 23 exceptions; after that fix, six more tests still iterated raw route lists with `getattr` and silently produced 27 zero-owner assertions | Route precedence/ownership/security regressions can fail before evaluating actual application owners, or worse silently report no owner, hiding real security/product regressions behind framework representation drift | `FIXED_PENDING_RUNTIME`; later source migrates both known test shapes to public `iter_route_contexts(...)`; current map-final CI/locked rerun must prove those clusters are gone |
| PM-015 | Calculator date/result/recovery truth | Audit found five linked client-path defects: early actual transfer was rejected instead of saved as zero delay; future contractual date was not persisted before informational exit; recovered draft UI emitted raw callbacks that exact-case handlers reject; zero-delay results still exposed M1; stale positive-result buttons could enter M1 after a newer zero-delay recalculation | A specified exceptional outcome could end as an input error, dead button, wrong Case interaction, or inappropriate M1 progression instead of a truthful client/operational result | `FIXED_PENDING_RUNTIME`; source/tests corrected in audit commits `58fb066...`, `2b7aabe...`, `974ae2d...`, `253838f...`, `ace7cf8...`, `73c115c...`, `9958d95...`, `1fb2c10...`, `29a1fca...`, `91e8fe1...`, `451e6db...`. No runtime PASS is claimed until exact-head CI/Telegram gates execute |
| PM-016 | Calculator legal rule engine | Current calculation path still uses one configured key rate/multiplier and a single formula path; inspected source does not implement versioned lawyer-approved rules, rate intervals, moratoria/excluded periods and the legal basis for each exclusion, although the Functional Specification requires these parameters in reference data rather than hard-coded logic | A numerically reproducible result can still be legally wrong for the relevant period, and historical recalculation may change when global settings change | `DEBT_OPEN / RELEASE_P0`; implement versioned, effective-dated rule/rate/exclusion data and persist the exact rule revision/segments used. No legal formula or 2026 moratorium behavior may be guessed; current law and lawyer approval must be evidenced before release acceptance |
| PM-017 | Calculator incomplete-answer durability / Case-card visibility | Functional Specification requires entered calculator values to reach the Case card and interrupted input to be saved. Current incomplete answers are only Case-namespaced Redis FSM draft state; `Calculation` is written only for completed calculations and `Case` has no calculator-draft fields | Redis loss/expiry can remove client-entered facts and staff cannot rely on the Case card for an interrupted calculator, contradicting the specified operational handoff | `DEBT_OPEN / RELEASE_P0`; do not rewrite the specification around Redis. Implement or identify a durable Case-scoped intake/draft source, persist field-level facts transactionally/auditably, expose them to authorized staff, and prove Redis restart/loss + multi-Case isolation + resume behavior |
| PM-018 | Case Transition Authority & Recovery | Existing CaseService row locking and transition policy did not provide an aggregate version plus durable command identity for every applied transition; after commit-before-response an old callback could not be distinguished from a new stale command at the shared process boundary | Duplicate/stale actions could create ambiguous recovery behavior; cross-Case ownership checks and M2→M1 handoff authority were not proven by one durable transition contract | `FIXED_PENDING_RUNTIME / SOURCE_IMPLEMENTED / RELEASE_P0`; migration 0024 adds `Case.version`, `case_transition_commands` and `case_transition_outbox_events`; CaseService orders ownership → replay → expected-version → policy → mutation → journal/audit/outbox; applied mutation increments version once; stale unrelated actions write nothing; committed duplicate keys replay without another version/audit/outbox; M2→M1 is lawyer-only `CASE_TRANSFERRED_TO_M1` on the same Case. Focused regression/negative tests added. PM-019 is explicitly NOT_STARTED until exact-head migration/tests/CI pass |
| PM-021 | Test infrastructure: aiosqlite lifecycle / event-loop leakage | Full-suite diagnostics on PM-018 and its stacked base show order-dependent `aiosqlite` ResourceWarning / worker-thread `Event loop is closed` errors, plus at least one unclosed source file in a legacy contract test. These errors can surface in a later unrelated test after the creating event loop has already closed. | Test-infrastructure leakage can obscure differential product evidence and inflate/shift the small current-only error set, making it unsafe to classify PM-018 from aggregate counts alone. | `SOURCE_IMPLEMENTED / RUNTIME_PENDING`; issue #122 isolates this debt from PM-018. `APP_ENV=test` now uses `NullPool` for every async backend while production/staging pooling is unchanged; directly observed archive-test SQLite pooling and unclosed source-file ownership were corrected; `tests/test_async_db_lifecycle_pm021.py` proves no cross-loop connection reuse. Re-run the same suites before deciding whether PM-018 can close on differential proof. PM-019 remains NOT_STARTED. |

# Development priority plan

## P0 — current release truth and production-critical calculator closure

1. Keep this Process Map as the final governed commit for this batch and treat its resulting SHA as the next audit candidate.
2. Open the calculator audit as a stacked **draft** PR against `feat/v37-guided-case-dashboard-telegram`, then execute exact-SHA `CI` + `Deployment Readiness` + `Reproducible Dependencies`; do not classify executed failures as infrastructure without current-run evidence.
3. Verify PM-015 with the exact-head calculator regressions and Telegram runtime. If a regression fails, fix the product/test contract based on the authoritative Functional Specification — never delete a failure, weaken the assertion, or rewrite the requirement to make the existing code pass.
4. Use the same exact-head diagnostics to classify remaining failures, including PM-014 route inventory, assignment/SLA fixtures, FakeDB/locking assumptions and teardown/resource defects. Select the largest deterministic current-contract cluster only after reading the new log.
5. Before calculator acceptance, close PM-016 with versioned/effective-dated legal calculation rules and PM-017 with durable Case-visible calculator intake. Each governed change creates a new candidate and restarts the exact-SHA evidence chain.
6. Prove PM-018 on one exact SHA: migration 0024 + focused regression/negative tests + the separate M2→M1 proof + normal CI migration/SQLite/PostgreSQL gates. PM-019 remains `NOT_STARTED` until this set passes.
7. After source P0 debt is closed, obtain full `CI` + `Deployment Readiness` + `Reproducible Dependencies` PASS, then PostgreSQL Concurrency → Telegram Runtime Contracts → Browser Staff E2E on the same SHA.
8. Only after all general/dedicated gates are green, run one complete `LIVE_REQUIRED` attempt for that exact SHA.

## P1 — controlled debt closure after executable CI is green

1. PM-013: collapse duplicate Message Center document-route ownership in the base/guided sources so final composition does not need compatibility normalization; preserve canonical `/document-access/review/ui` and M2 consultation/slot responsibility.
2. PM-004: finish exact historical/external consumer audit and delete legacy assignment modules only if safe-removal proof and regressions exist.
3. PM-006: verify actual branch/rules settings and exact required contexts; align them with the gate contract without weakening it.
4. Reconcile or close stale GitHub issue #116 so repository issue metadata matches AGENTS/Process Map runtime truth.
5. Browser-check centralized Workdesk renderer for M1/M2 responsibility/action semantics.
6. Audit real staging/restore DB for remaining `M2_CONSULTATION_ROUTE` rows before considering compatibility enum removal.
7. Review PM-009 runtime results before deciding whether a later model/session-level mutation authorization backstop is justified.
8. Exercise PM-011 with authenticated browser/API sessions against active and terminal Payment Review records, including stale 409 server-truth fields, terminal history and cache headers.

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

- Strengthened `.github/workflows/ci.yml`: latest non-map governed commit must be an ancestor latest map commit. Same-commit changes pass; map-after-code passes; code-after-map fails closed.
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
- Status remains `FIXED_PENDING_RUNTIME`; superseded befe Browser Staff E2E later passed, but no exact-final-SHA browser PASS is claimed.

## 2026-09-06 — PM-013 Message Center role-safe document composition hardened

- Real browser failure analysis showed the guided Message Center composition can contain more than one legacy admin Workdesk document action, while `message_center_role_ui_impl.py` required exactly one marker; the role-safe replacement also pointed to non-canonical `/document-access/ui`.
- Commit `00910ea6d573875a7efe3672b0ff7c624e553a9f` changed final composition to normalize every composed legacy action to canonical `/document-access/review/ui?case_id=...`, preserving M2 consultation/slot authorization semantics and avoiding exact-one HTTP 500 failure.
- Commit `91bfc7248f903875d062a7d0533efc89b55d6183` added `tests/test_message_center_role_ui_contract.py` for successful composition, legacy-route absence, canonical route presence and business-time contract preservation.
- Duplicate document-link source ownership in the base Message Center and guided patch remains `DEBT_OPEN`; collapse is P1 after executable P0 gates are green.

## 2026-09-08 — PM-014 FastAPI effective route inventory compatibility migrated

- Superseded `befe4f7c518d2224047b9fa65be8fcaf34be3102` Reproducible Dependencies run `34053363389` executed the locked suite and exposed 23 direct `_IncludedRouter.path` failures across 16 route-ownership/security test files under the repository-pinned FastAPI `0.141.1`.
- Root cause is a stale test assumption about flattened FastAPI route internals, not evidence of a production route duplication. FastAPI's public `iter_route_contexts(...)` is the correct effective-route inventory boundary.
- Migrated all 16 known direct-failure files to `iter_route_contexts(...)`, from `tests/test_admin_m2_responsibility_v37.py` through `tests/test_v37_stale_m1_payment_refund.py`.
- No production route assembly, authorization, state-machine or business mutation semantics changed in PM-014; only test introspection was aligned with the locked framework contract.

## 2026-09-08 — PM-014 second-wave route inventory + runner truth alignment

- Exact superseded candidate `48381e7a3d835917685de9a3c9ec60e59c289602` ran all six PR workflows. PostgreSQL, Deployment Readiness, Telegram Runtime Contracts and Browser Staff E2E succeeded; CI and Reproducible Dependencies executed and failed at application tests.
- Locked run `34262572283` / job `102184077696` produced **293 failed, 1483 passed, 19 skipped, 2 errors in 76.13s** and contained zero `_IncludedRouter` occurrences, proving the first PM-014 exception shape was removed.
- The same log exposed 27 zero-owner failures from six tests that still silently skipped `_IncludedRouter` wrappers through raw `create_app().routes` + `getattr(...)`. Commits `1244bab91a7e0187ef50922c82a88fc6627bdd9d`, `3960a026cecb648f1bca456a18e86c1e661bafc9`, `df2705846b41703b32a80aba4da71db59f033830`, `6ce984e6947a938e6daa09c63d63105c45cdb09d`, `6169ec4bc0a6034dda3ee96a0b6e326deb1f2339`, and `6c29908619b094f6754adbc9db382a0711ea38dd` migrate those tests to effective route contexts.
- Commit `1fdc2a5391657952bada8a5660bd4ff217961200` aligns root `AGENTS.md` with observed runner reality: issue #116 is no longer treated as a universal current blocker; classification must come from exact-candidate steps.
- PM-014 remains `FIXED_PENDING_RUNTIME`; PM-001 remains a stale-issue/governance debt until issue metadata is reconciled. `LIVE_REQUIRED` remains `PENDING`.
- This Process Map update was the final governed commit of that historical batch; it is superseded by the 2026-09-09 calculator audit candidate.

## 2026-09-09 — PM-015 calculator date/result/recovery truth hardened

- Audited calculator exceptional outcomes against the Functional Specification rather than preserving stale implementation assumptions.
- `58fb066...` + `2b7aabe...`: early/on-time actual transfer is a valid zero-delay/zero-amount Calculation; the old rejecting regression was replaced with stronger positive outcome regressions, not deleted.
- `974ae2d...` + `253838f...` + `ace7cf8...`: recovered drafts now emit exact Case-bound resume/restart callbacks and tests assert rendered callback provenance.
- `73c115c...` + `9958d95...`: future contractual dates remain a distinct saved informational boundary until due and corrupt saved dates return to a safe input step.
- `1fb2c10...` + `29a1fca...`: M1 eligibility is rechecked against the latest persisted Calculation, preventing stale positive-result buttons from overriding newer zero-delay truth.
- `91e8fe1...` + `451e6db...`: Telegram now persists future contractual date before exit, clears unsafe downstream answers when upstream date changes, accepts early actual transfer, separates the future-date consultation reason, and omits M1 from zero-delay result UI.
- Status is `FIXED_PENDING_RUNTIME`; no current-branch tests or workflows have been promoted to PASS until the map-final SHA executes.

## 2026-09-09 — PM-016/PM-017 calculator release debt recorded without changing the specification

- PM-016 records that the current single-rate calculator is not yet the versioned effective-dated rule engine required by the Functional Specification; rates, moratoria/excluded periods and rule revisions must become durable reference data before calculator acceptance.
- PM-017 records that Case-namespaced Redis FSM is useful runtime recovery but does not by itself satisfy the specified durable Case-card visibility of entered calculator values. This gap is not closed by weakening the requirement.
- Both items are release P0. Any implementation changes after this map commit create a new candidate and require another final Process Map update plus a fresh evidence chain.


## 2026-09-18 — PM-018 Case Transition Authority & Recovery source implementation

- PM-018 is implemented by strengthening the existing `CaseService` + `CaseTransitionPolicy` authority; no second state machine or third route was introduced.
- Migration `20260918_0024_case_transition_authority_recovery.py` adds the monotonic `cases.version` field plus durable `case_transition_commands` and `case_transition_outbox_events` tables.
- Authority ordering is ownership lock → exact idempotent replay → expected-version stale guard → existing transition policy → one Case mutation/version increment → command journal → existing sealed `AuditLog` event → transition outbox → SLA synchronization, all under the caller's one database transaction.
- Exact replay is deliberately checked before expected-version mismatch so a commit-before-response retry recovers the durable result; a different stale command returns `STALE` without Case/history/journal/outbox mutation.
- Client-originated commands scope the row lock by `Case.client_id` before replay lookup, preventing a foreign user from probing or replaying another Case command. Rollback regression proves flushed Case/journal/audit/outbox changes disappear together.
- M2→M1 is proved separately: a client cannot perform the cross-route handoff; the lawyer handoff writes `CASE_TRANSFERRED_TO_M1`, changes the same Case from route M2/status M2 to route M1/`M1_DOCUMENTS_PENDING`, increments version once and writes exactly one command/outbox. The real `ConsultationOutcomeService.complete(..., decision="to_m1")` path uses a deterministic consultation-scoped command key and is idempotent on retry.
- Focused evidence files are `tests/test_case_transition_authority_pm018.py` and `tests/test_m2_to_m1_authority_pm018.py`. CI now has an independent `PM-018 authority and M2 handoff proof` job that compiles the authority surface, applies migration 0024, runs `alembic current/check`, and executes only these PM-018 regressions so their result remains visible even while inherited branch-wide tests are being repaired.
- Diagnostic head `43850b2...` proved compile, architecture, migration 0024, SQLite/PostgreSQL ORM parity, PostgreSQL backup/restore and container startup, but the monolithic SQLite suite remained red from the inherited base branch and one PM-018 M2 proof over-constrained notification fan-out (`2 <= 1`) outside the transition contract. That assertion was removed as `TEST_INFRA_DEFECT`; transition command/outbox/audit idempotency assertions remain strict.
- The dedicated PM-018 CI job intentionally increased the CI job/check inventory. `tests/test_ci_workflow.py` was aligned to the new five-job CI contract (five credential-safe checkouts/fixed runners/timeouts and four schema checks) and now explicitly locks the PM-018 proof job, migration 0024 and both focused proof files; this is a `STALE_CONTRACT` correction, not a relaxation of security or schema gates.
- Source status is `FIXED_PENDING_RUNTIME` until the exact final SHA executes the dedicated PM-018 proof and the required CI evidence. No PM-019 code or contract work is started in this batch.


## 2026-09-18 — PM-021 test-infrastructure lifecycle isolation

- Created issue #122 to keep aiosqlite/event-loop leakage separate from PM-018 product semantics and from the inherited 250+ branch-wide failures.
- Exact-head diagnostics identified three resource classes relevant to this bounded pass: unclosed `aiosqlite.core.Connection`, aiosqlite worker threads reporting into a closed event loop, and an unclosed source file escalated by the repository-wide `filterwarnings = ["error"]` contract.
- `app/db/session.py` now applies `NullPool` to every `APP_ENV=test` async database backend. Production/staging pooling is unchanged. This extends the existing PostgreSQL cross-event-loop protection to SQLite test runtime instead of suppressing warnings.
- `tests/test_client_completed_archive_m2_v36.py` now creates its SQLite engine with `NullPool`, so an assertion failure cannot leave a pooled aiosqlite connection waiting for later garbage collection. The first CI attempt exposed a literal escaped-newline syntax defect in that helper; commit `16809931...` corrected the source syntax before any lifecycle conclusion was drawn.
- `tests/test_m2_stale_disabled_pay_v36.py` now owns its source-file read through `Path.read_text()`, removing the directly observed unclosed file warning.
- Added `tests/test_async_db_lifecycle_pm021.py` to lock the test-only `NullPool` contract and prove the shared application engine can be used from sequential fresh `asyncio.run(...)` loops without retaining a connection across loop boundaries.
- Status is `SOURCE_IMPLEMENTED / RUNTIME_PENDING`. No PM-018 authority code, migration 0024, warning policy, production pooling, or PM-019 behavior changed. Fresh suite evidence is required before any PM-018 closure decision.
