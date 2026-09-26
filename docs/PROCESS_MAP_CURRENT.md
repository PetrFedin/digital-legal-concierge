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

Flow: `Calculate → source operation key → CaseCreationRequest → new Case → durable Case-bound CalculationIntake → schema-v2 legal rule authority → immutable Calculation evidence → client decision`.

Facts and exceptional-result rules:

- `(client_id, operation_key)` deduplicates the same source action;
- distinct Calculate operations may create distinct Cases;
- `Calculation.case_id` is one-to-many and newest stored Calculation is authoritative for current calculation outcome;
- `calc_start` means a new matter;
- `calc_recover:v2:<case_id>` resumes an exact existing matter; every resume/restart/back/transfer action emitted from recovery must also carry that exact Case id;
- an actual transfer before or on the contractual date is a valid factual outcome: the calculation is saved with zero delay/zero amount, M1 is not offered, and the client may finish/postpone, correct data, or choose M2;
- a contractual transfer date later than the current date is persisted before the informational boundary, no delay calculation is performed yet, and resume stays at the future-date boundary until the date arrives; after it arrives the same saved draft advances to transfer status;
- M1 continuation is permitted only when the **latest stored Calculation** for that Case has both positive delay days and positive penalty amount; a stale positive-result Telegram button cannot override a newer zero-delay result;
- accepted calculator facts are persisted to the Case-scoped `CalculationIntake`; Redis FSM is recovery/presentation state rather than the sole business source. PM-017 remains open only for exact final runtime proof of loss/restart, multi-Case isolation and the complete operational handoff, not for creation of a durable intake model;
- PM-016 v2 makes participant type (`consumer/other`) and unique-object status explicit facts. An unknown legally significant fact never receives a hidden default: it records a manual-review flag and routes to the lawyer path;
- a new automatic amount can be produced only from one published `PRODUCTION` schema-v2 revision. The engine fixes the base rate on the contractual due date, then applies configured moratoria/excluded periods, rate caps, participant coefficient and the unique-object branch/amount cap. Missing/ambiguous authority, CBR coverage gaps, tampered hashes and configured stop factors fail closed;
- each completed calculation keeps the immutable rule key/SHA-256, applied/excluded segments and source references. Staff see only the calculation-relevant provenance; the client can reopen `Основания и детализация расчёта` from the result and from `Моё дело`/archive.

Canonical path: `NEW → CALCULATOR_STARTED → CALCULATED → CLIENT_DECISION` plus explicit exceptional completion/M2 branches and compatibility transitions from `case_transition_policy.py`.

Anchors: `app/domain/calculator/*`, `app/api/calculator_builder.py`, `app/api/workdesk_calculator_projection.py`, `app/bot/calculator_durable.py`, `app/bot/calculator_draft.py`, `app/bot/screens/calculator.py`, `app/bot/screens/calculator_active_case_recovery.py`, `app/domain/cases/case_service.py`, `CalculationIntake`, `Calculation`, `CalculationRuleRevision`, `CaseCreationRequest`.

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

The production manual-payment path is now explicit `PAYMENT_PROVIDER=offline`, not the old `disabled` ambiguity. Offline creation persists the real obligation with provider identity `offline` and no external URL; it never marks money received. Authenticated administrator confirmation requires expected status, bank/accounting reference and comment, then reuses the canonical successful-payment application boundary so Payment/PaymentEvent, M1 stage or exact M2 reservation, notification and Audit evidence advance together. `disabled` remains local/test bypass only and is fail-closed/not-ready in production.

Payment Review audit traceability is read-only and exact-payment scoped: `GET /admin/payment-reviews/{payment_id}/history` remains available after a payment leaves the active `PAID_REVIEW` queue, queries the Case audit actions, filters the exact `payment_id` before bounding the visible timeline, and returns only a normalized whitelist. Free-text reconciliation comments, raw `old_value`/`new_value`, provider/reservation payloads and audit-chain integrity metadata are deliberately not exported by this endpoint.

The stale-command 409 recovery projection follows the same minimization rule. `payment_review_conflict_snapshot()` returns only server-authoritative state needed to explain the conflict and prevent overwrite: payment/Case state, normalized resolution decision, exact consultation/slot identifiers where applicable, actor id and resolution time. The persisted free-text `AuditLog.comment`, raw provider/reservation values and unrelated audit payload are not part of the conflict response. The browser keeps its own local draft/comment separately and reloads current server truth before any retry.

The Payment Review composition boundary is now single-owner by responsibility. `app/api/payment_review_center.py::PAYMENT_REVIEW_CENTER_HTML` owns the guided decision hierarchy (`Сейчас`, `Главный следующий шаг`, `Вторичные действия`). `app/api/payment_review_renderer.py` no longer tries to rewrite those guided markers and adds only the read-only exact-payment history panel. This removes the stale second guided-copy layer that raised `RuntimeError` and returned HTTP 500 when the base template evolved. `staff_ui_guards.py` owns authentication/redirect gating. `app/api/payment_review_product.py::NoStoreAPIRoute` applies `Cache-Control: no-store` centrally to Payment Review handlers and propagates the same directive through handler/dependency HTTP exceptions. The history layer is presentation only and does not mutate `Payment`, `Case`, `Consultation` or slot state.

`PaymentLifecycleService` is the canonical application mutation boundary for an existing `Payment.status`. The model listener stamps/records invariant facts but is not the product decision boundary. Static architecture enforcement recognizes obvious aliases/annotations/constructor aliases plus `setattr` and bulk SQLAlchemy status mutations outside `PaymentLifecycleService`.

Evidence: `tests/test_payment_review_history_contract.py`, `tests/test_payment_review_renderer_contract.py`, `tests/test_payment_review_product_contract.py`, `tests/test_payment_review_conflict_privacy_contract.py`, and `tests/test_browser_staff_e2e.py`. Browser Staff E2E run `33910691169` on superseded candidate `dd8e038f554fa9364242c1c9585ffe0655f3c4d4` actually executed and failed; one proven failure was the stale Payment Review composition described in PM-012. Source fix `544f7f5daa6003aa51e0c24097f89bd0ce053781` plus renderer regression commit `8a63323f709d3e6d9e283e749078741faf8fd1df` therefore remain `FIXED_PENDING_RUNTIME` until the exact final candidate reruns the browser/runtime gates.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-06 — Messages and notifications

Flow: `client/staff message → exact Case provenance → Message → priority/read state → notification event → dedupe → delivery → Telegram outcome`.

Rules: history/pagination are Case-bound; draft protection prevents silent loss; delivery failure after a committed mutation must not replay the mutation; reminders/notifications do not establish legal facts.

On Timeweb, Telegram polling and durable notification delivery run in the same host-network background worker because the host has working Telegram IPv6 while its Docker bridge currently has no usable IPv6 route and Telegram IPv4 times out. The HTTP container never starts a second bot/dispatcher. PostgreSQL remains the durable source and the polling advisory lock remains the singleton authority.

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

Anchors: `app/scheduler/scheduler.py`, `jobs.py`, `lease.py`, `notification_dispatcher.py`, `app/bot/worker.py`.

Timeweb release topology assigns scheduler + notification dispatcher + Telegram polling to one supervised host-network background worker. The web process is HTTP-only. The worker runs the same immutable image/SHA, waits for PostgreSQL/Redis, runs strict production preflight, and exits if any supervised background service unexpectedly terminates. Deployment/status/rollback verify the Telegram identity and held polling lease and treat app + worker as one release unit.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

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
4. one complete manual `LIVE_REQUIRED` run passes with `payment_mode` equal to the candidate's actual mode and creates one exact SHA/run/attempt manifest;
5. real Telegram M1/M2 personas pass with UI ↔ PostgreSQL ↔ Audit/PaymentEvent reconciliation, including the selected payment mechanism;
6. encrypted backup→restore application/runtime proof passes;
7. YooKassa provider-side paid/refund proof is expanded only if YooKassa is actually being enabled; an offline-only candidate records that gate as not applicable rather than fabricating provider evidence;
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
| PM-016 | Calculator legal rule authority | The old raw rule bundle has been replaced by governed schema v2: due-date CBR rate policy, explicit rate directory, moratoria, rate caps, participant type, unique-object rules, stop factors and deterministic control examples. Every legal/rate value is source-linked; each source carries an exact provision/table locator, HTTPS URL and verification date. DRAFT edits are mutable, while legal review is bound to the exact SHA-256 before separate APPROVED and PRODUCTION publication | Without this chain the system could either guess legal truth, lose the basis for a changed value, or show a client an amount that cannot later be reproduced/defended. A stale CBR directory must also never be silently treated as current legal authority | `SOURCE_IMPLEMENTED / FOCUSED_RUNTIME_GREEN / FINAL_DIFFERENTIAL_BLOCKED_INFRA / PRODUCTION_ACTIVATION_PENDING / RELEASE_P0`; editor/lifecycle/provenance, migration 0025, six-step Telegram facts, source-pruning clears and client/staff detail surfaces are implemented. The reviewed template contains official CBR history through **25.09.2026** but is DRAFT input only; no migration auto-publishes legal truth. Pre-final candidate `ff930706...` passed the dedicated PM-016 proof (60 tests), Browser Staff E2E, Telegram Runtime Contracts and PostgreSQL Concurrency. The later final-source heads could not execute exact reruns: GitHub created jobs with no steps/logs and immediate failure, including after explicit rerun, so full final-head differential proof is not claimed. Production still requires lawyer SHA review → SUPERADMIN APPROVED → explicit PRODUCTION publish → one real persona calculation reconciled with PostgreSQL/Audit evidence |
| PM-017 | Calculator incomplete-answer durability / Case-card visibility | `CalculationIntake` now exists as the durable Case-scoped intake authority and the accepted calculator facts are written before Telegram progression; Workdesk projects the intake separately from the latest completed `Calculation`. PM-016 v2 extends the same durable row with participant type, unique-object status and manual-review flags | The remaining risk is evidence, not absence of a model: Redis loss/restart or ambiguous multi-Case recovery must never roll back accepted PostgreSQL facts or bind them to the wrong Case | `SOURCE_IMPLEMENTED / FINAL_RUNTIME_PROOF_PENDING / RELEASE_P0`; focused durable-intake/write-order/recovery regressions exist and are included in the PM-016 proof. Before closing PM-017, execute exact-candidate Redis loss/restart + multi-Case isolation + staff Case-card reconciliation and verify the expected audit/transition evidence. Redis remains a recoverable UI cache, not legal fact authority |
| PM-018 | Case Transition Authority & Recovery | Existing CaseService row locking and transition policy did not provide an aggregate version plus durable command identity for every applied transition; after commit-before-response an old callback could not be distinguished from a new stale command at the shared process boundary | Duplicate/stale actions could create ambiguous recovery behavior; cross-Case ownership checks and M2→M1 handoff authority were not proven by one durable transition contract | `BOUNDED_COMPLETE / DIFFERENTIAL_PROOF_ACCEPTED / RELEASE_BASELINE_RED`; migration 0024, focused authority/negative/M2→M1 proof and dedicated runtime workflows pass. After PM-021 removed aiosqlite/event-loop pollution, exact candidate `a048e7a...` had **zero current-only FAIL/ERROR test node ids** versus stacked base `c1aac0d...` in both normal and locked comparisons: all 253 current normal failures and all 254 current locked failures already existed on the base. Therefore no PM-018-owned product regression remains identified and the bounded change is complete. This does **not** make the release baseline green: full CI/Reproducible Dependencies remain mandatory before merge/release. PM-019 may start only after this recorded decision and remains `NOT_STARTED` in this batch |
| PM-021 | Test infrastructure lifecycle / event-loop isolation | Full-suite diagnostics on PM-018 exact head showed order-dependent teardown/setup failures from unclosed `aiosqlite` connections and worker threads reporting into already-closed event loops | Resource leakage moved failures between unrelated tests and contaminated the differential signal used to judge PM-018 | `RUNTIME_PROVEN / TARGET_FIXED / TEST_INFRA_ONLY`; corrected candidate `a048e7a...` preserves in-memory SQLite semantics, uses `NullPool` for shared test DB access and file-backed test-owned aiosqlite engines, and aligns the locked image to `APP_ENV=test`. Normal and locked logs both contain **0** `Connection ... deleted before being closed` and **0** `RuntimeError: Event loop is closed`; focused lifecycle tests and PM-018 proof pass. Remaining full-suite failures are inherited baseline debt. A separate non-DB unclosed source-handle warning is tracked as PM-023 rather than being folded into PM-021 |
| PM-023 | Residual test resource hygiene — source handles | After DB lifecycle leakage was removed, strict-warning diagnostics exposed an inherited test that reads `app/api/access_management.py` through raw `open(...).read()` and leaves the file for GC | An unrelated test may fail through `PytestUnraisableExceptionWarning`, but this no longer contaminates DB/event-loop ownership or PM-018 differential classification | `DEBT_OPEN / P1 / TEST_HYGIENE`; GitHub issue #128. Replace only proven unclosed source handles with context-managed/`Path.read_text()` reads; keep warnings strict and do not mix in inherited business assertion repair |
| PM-022 | End-to-end role journey and visual UX acceptance | Source/route audit confirmed the core M1/M2 client and staff journeys exist, but the canonical staff landing mixed daily operational links with supervisory controls, rendered normalized superadmin roles as redundant `Администратор · Суперадминистратор`, omitted consultation schedule from the administrator daily-work group, duplicated SLA/Telegram control links, exposed the superadmin-only Security Center to normal administrators, and Browser Staff E2E verified route availability more strongly than responsive visual composition. Telegram direct reply-menu ownership was already case-bound and one-screen, but the persistent `Моё дело` entry inherited Home labels instead of the canonical `СЕЙЧАС / ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ` hierarchy | Staff can waste time finding the correct workspace or hit a role-denied dead end; leadership controls look like ordinary admin operations; raw technical diagnostics/English control-center copy weakens the human workflow; mobile regressions or presentation drift can survive an HTTP-200 test; client visual hierarchy can differ depending on how the same screen is opened | `BOUNDED_COMPLETE / DIFFERENTIAL_PROOF_ACCEPTED / RELEASE_BASELINE_RED / PRESENTATION_ONLY`; exact PM-022 head `ceca95365dad69f72972c5f50e87e6d18c82e7a9` passed Deployment Readiness, PostgreSQL Concurrency, Telegram Runtime Contracts and Browser Staff E2E (5 passed), while the normal/locked common suites retained only inherited failing node ids. `/operator` de-duplicates role naming, adds consultation schedule to daily admin work, removes duplicated control links, sends normal administrators to canonical Diagnostics and keeps Security in the superadmin-only leadership group together with Access/Audit/Backup/Retention. Diagnostics is now an action-oriented human dashboard instead of raw JSON; Settings/Health/Security recover to the canonical role hub; leadership/control pages use consistent Russian navigation and visible keyboard focus/live feedback. Browser Staff E2E covers admin/lawyer support surfaces and MFA-verified superadmin leadership surfaces at 390px with horizontal-overflow checks. Telegram direct `Моё дело` keeps the same safe Case data/actions but normalizes the headings to the canonical current-state/next-step hierarchy; the persistent reply menu now hides unavailable `Моё дело`/`Документы` before a Case exists, keeps completed `Моё дело` read-only, and preserves `Связаться с юристом` as the global M2/help entry. PM-019 business projection semantics are untouched |
| PM-019 | Deterministic My Case Projection & Next Action | The existing shared client presenter already centralized much of My Case/Home, but it could fall back to free-form `Case.next_action`, document replacement state could globally override later court/payment/consultation stages, the My Case blocker could reuse `Document.lawyer_comment`, payment/history facts were not part of the stale-action fingerprint, and Home/My Case disagreed on whether unread lawyer messages silently replace the process action | A client can be shown non-authoritative/internal copy, a stale document can pull the apparent next step backwards, free-text staff reasoning can leak into the client cabinet, and a button may remain valid after a payment/history fact changed | `IMPLEMENTED / FIXED_PENDING_EXACT_RUNTIME`; `client_case_view.py` now owns an explicit safe projection for every supported CaseStatus: client stage → what is happening → what is required → blocker → one projected action → document/payment/history summaries. Unsupported or route-conflicting state fails closed; arbitrary `Case.next_action` is never rendered; document precedence is limited to the M1 document contour; M2 optional documents do not block slot selection; document-review audit comments stay staff-only; snapshot identity includes Case.version, payment state and latest client-visible history. Home and My Case consume the same projection. Focused PM-019 CI proof is green (9 passed); exact final differential full-suite classification is pending the governed map commit |
| PM-024 | Timeweb Telegram production egress / split runtime | Live diagnostics proved general IPv4 egress works, Telegram IPv4 TCP/443 times out, host IPv6 reaches Telegram, and ordinary Docker bridge containers have no usable IPv6 route | A healthy web release can silently leave polling, notifications and scheduled Telegram delivery offline; ad-hoc manual containers are not reboot/release reproducible | `SOURCE_FIXED / RUNTIME_PENDING / RELEASE_P0`; v47 adds a reproducible split Timeweb compose overlay: HTTP app on bridge+proxy, host-network background worker for bot + notification dispatcher + scheduler, loopback-only PostgreSQL/Redis access, operator-managed Telegram IPv6 mapping, same-image identity, polling-lease probe, unified deploy/status/rollback and restart-policy checks. Must still pass real /start, post-reboot acceptance and exact-SHA runtime evidence before closure |
| PM-025 | Production payment mode ambiguity | Live v46 had `PAYMENT_PROVIDER=disabled`; preflight tolerated it while /ready correctly rejected it, and old copy conflated provider-off with manual/offline receipt handling | Production could stay permanently not-ready or operators could mistake a disabled test bypass for a legitimate financial process | `SOURCE_FIXED / RUNTIME_PENDING / RELEASE_P0`; v47 introduces explicit `offline` provider mode with no external URL, canonical PaymentEvent/Audit lifecycle, admin bank/accounting confirmation for M1 and exact M2 reservation, fail-closed production `disabled`, role-aware readiness and payment-mode-specific LIVE_REQUIRED evidence. Must pass real M1/M2 offline persona reconciliation before closure |
| PM-026 | CBR rate-directory freshness authority | PM-016 deliberately stores an immutable, reviewed rate directory inside each rule revision. The reviewed template is verified through 25.09.2026 and the engine fails closed outside declared coverage; production revisions are never silently mutated by a newer CBR value | A static reviewed directory is legally safer than guessing, but without a controlled freshness workflow a future due date can legitimately block calculation until an operator updates the official CBR history and creates/reviews a new rule revision | `DEBT_OPEN / P1 / FAIL_CLOSED_SAFE`; after PM-016 production activation, add a controlled official-CBR import/freshness assistant that can populate **DRAFT only**, records retrieval/source evidence and change diff, reruns control examples, and still requires lawyer SHA review + APPROVED + explicit publication. It must never rewrite an existing PRODUCTION snapshot automatically |
| PM-027 | Самостоятельная подача в суд — пакет документов | Новый запрос заказчика расширяет коммерческий M1-контур: клиент не покупает полное ведение суда, а оплачивает 15 000 ₽ за подготовку комплекта для самостоятельной подачи. Исходная MVP-спецификация содержит только маршруты M1/M2, поэтому новый продукт не создаёт M3: это отдельный service mode внутри M1 | Без отдельной authority-модели пакет легко смешать с полным представительством: неверные обязательства, платежи, доверенность, SLA и обещание результата. Для клиентов по РФ также нельзя угадывать конкретный суд/подсудность только из адреса | `CONTRACT_DEFINED / IMPLEMENTATION_PENDING / ISSUE_134 / P0_PRODUCT_EXPANSION`; обязательны DDU + документ личности + приложения/допсоглашения и иные затребованные юристом материалы; 15 000 ₽; SLA 2 рабочих дня начинается только после подтверждённой оплаты **и** юридически принятого полного комплекта; итоговый пакет версионируется, доступен защищённо и доставляется на подтверждённый email; территориальная/родовая подсудность подтверждается юристом; услуга проектируется для клиентов по всей России. Нужны миграция/service mode, payment code, email outbox/provider, business-calendar authority, Workdesk/Telegram/Audit/PaymentEvent и полный proof |  
| PM-028 | Fresh start + несохраняемый предварительный расчёт | Текущий Home автоматически вытаскивает выбранное/последнее дело, а canonical calculator сразу создаёт Case. Заказчик хочет при каждом входе нейтральный новый лист: старую работу открывать только кнопкой «Продолжить общение», а новый расчёт можно пройти как временный preview без записи в дело | Автоматическое открытие истории создаёт ощущение, что бот «тащит прошлое» в каждый вход; немедленное создание Case превращает исследовательское использование калькулятора в юридическую/операционную запись и захламляет кабинет | `SOURCE_IMPLEMENTED / RUNTIME_PENDING / ISSUE_135 / P0_UX`; branch `pm-028-fresh-start-preview-20260925` вводит отдельные PreviewCalculatorStates, neutral Home без User/Case creation/selection, canonical `preview_calc_start`, pure read-only rule calculation и explicit save. Материализация теперь привязана не к Telegram callback id, а к стабильному preview token, который переносится в exact save callback: два разных callback id одного double-tap сходятся к одному CaseCreationRequest/Case. Токен также позволяет восстановить уже сохранённый результат после сбоя Telegram presentation; stale token не может использовать факты другого preview. DB commit выполняется до очистки FSM, поэтому неудачный commit не уничтожает несохранённый draft. Save остаётся привязан к exact rule key/SHA и fail-closed при изменении rule authority. Добавлен отдельный `PM-028 fresh start and preview proof` CI job. Home/preview не обновляют activity выбранного Case; старый durable `calc_start` остаётся compatibility/recovery boundary |

# Development priority plan

## P0 — production acceptance before new feature scope

1. **PM-024 / v47 production topology** — get the exact map-final candidate through CI/deployment gates, deploy web + host-network background worker as one immutable SHA, prove public web login, real Telegram `/start`, one polling consumer, durable notification delivery, restart policies and controlled Timeweb reboot recovery.
2. **Real M1 persona** — `/start → calculation → M1 → consent → documents → lawyer request/accept → contract → offline payment confirmation → POA → claim → court → second payment → enforcement → recovered amount → success fee → close/archive`. At every mutation reconcile Telegram/staff UI ↔ PostgreSQL ↔ AuditLog/PaymentEvent.
3. **Real M2 persona** — description → optional document/skip → slot → exact offline payment obligation/confirmation → booked consultation → result/no-show/reschedule where applicable → close or M2→M1, again with UI ↔ PostgreSQL ↔ immutable evidence reconciliation.
4. **PM-025 payment closure** — current release mode is explicit `offline`. Do not enable YooKassa production credentials until offline acceptance, personas and restore proof are complete. If YooKassa is later selected, use only its test-shop sequence first.
5. **Document production proof** — real test PDF/DOCX/JPEG upload, validation/quarantine behavior, encryption-at-rest, version/replacement/rejection, authorized one-time download and encrypted backup→separate restore/decrypt proof.
6. **Notification/scheduler time proof** — prove actual delayed/due behavior for document request, contract/payment reminder, consultation reminder, SLA escalation, claim waiting event and durable Telegram delivery after transient failure.
7. **Release baseline debt** — after live behavior is proven, classify the inherited common-suite failures by product/stale-contract/fixture/resource ownership and drive the required release baseline to green rather than relying indefinitely on differential proof.
8. **PM-017 durable calculator intake proof** — the durable `CalculationIntake` source now exists; prove on the exact release candidate that Redis loss/restart and multi-Case ambiguity cannot erase/rebind accepted facts and that the same facts are visible in the authorized Case card.
9. **PM-028 fresh-start acceptance** — prove that /start, /menu and Home never auto-open old Cases; preview creates no Case/CalculationIntake/history and does not refresh selected-Case activity; distinct callback ids from one double-tap converge through one stable preview token to exactly one Case/Calculation; stale preview tokens fail closed; commit failure preserves the unsaved FSM draft; a post-commit Telegram presentation failure can reopen the already-materialized result from the same token.
10. **PM-027 self-filing package** — implement as M1 service mode, not a third route: 15 000 ₽, complete-document gate, lawyer-confirmed jurisdiction, Russia-wide client facts, 2-business-day SLA from payment+completeness, protected package + durable email delivery.
11. **UX consolidation** — after the two client-requested changes: simplify staff surfaces to `what is happening → what is overdue/blocking → what is required from me → one main action`, remove legacy navigation/duplicate controls/technical copy without adding parallel authority.
12. **PM-016 production activation** — code/editor authority is implemented, but the reviewed template is not production by design. Create/inspect the DRAFT, run control examples, obtain lawyer confirmation bound to the exact SHA-256, record SUPERADMIN APPROVED, publish explicitly to PRODUCTION and prove one real calculation end-to-end. Keep the CBR directory verified for every due date actually accepted; current reviewed coverage ends **25.09.2026**. No legal value may be guessed or silently refreshed in an immutable production revision.

## P1 — controlled debt closure after P0 runtime truth

1. PM-013: collapse duplicate Message Center document-route source ownership while preserving canonical role-aware review.
2. PM-004: remove legacy assignment modules only after historical/external-consumer audit and regressions.
3. PM-006: verify actual repository branch/rules settings and required contexts.
4. Reconcile stale issue #116 with current runner reality.
5. Complete PM-023 residual test-resource hygiene without weakening strict warnings.
6. Review PM-009/PM-011 runtime evidence and close only evidence-backed remaining architecture/privacy debt.
7. PM-026: add controlled official-CBR freshness/import assistance for DRAFT revisions only; keep lawyer SHA review and immutable PRODUCTION snapshots mandatory.

## P2 — post-acceptance expansion

1. Encrypted backup→restore application/runtime evidence package retained for the accepted persona state.
2. YooKassa test-shop paid/refund expansion only if online payment is actually selected.
3. Resolve or explicitly accept every remaining release-relevant PM item.
4. Release/merge decision only after required evidence gates. The user has delegated the eventual GitHub merge action to the assistant; do not merge while the PR is Draft or while required checks/production acceptance remain unresolved.
5. Only after release stability: consider new services/routes/AI scope through a new approved product contract.

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

## 2026-09-18 — PM-021 test-infrastructure lifecycle isolated from PM-018

- Opened PM-021 as a separate bounded test-infrastructure change; it does not alter PM-018 authority code, migration 0024, M1/M2 product behavior or release scope.
- Exact-head PR #121 diagnostics show strict-warning teardown/setup pollution from `aiosqlite.core.Connection.__del__`, aiosqlite worker threads calling a closed event loop, and one directly observed unclosed source file handle in `tests/test_m2_stale_disabled_pay_v36.py`.
- `app/db/session.py` now uses `NullPool` for every `APP_ENV=test` database backend so a test event loop cannot inherit an async DBAPI connection created by an earlier loop; production/staging pooling remains unchanged.
- The directly observed source file is read with `Path.read_text(...)`, removing that ResourceWarning without suppressing warnings.
- Added `tests/test_test_db_event_loop_lifecycle.py` to prove the shared test engine uses `NullPool` and can execute the same DB probe from two sequential fresh `asyncio.run(...)` loops.
- This source change is `FIXED_PENDING_RUNTIME`. Fresh SQLite/locked-suite evidence must now determine whether the resource-pollution-only delta disappears and whether PM-018 can close on differential proof or still requires a completely green common baseline. PM-019 remains `NOT_STARTED`.

## 2026-09-19 — PM-021 first runtime result and direct-engine isolation follow-up

- Exact head `d15a1dcb68ee5a9e1621e5f34bed29e788ce9620` executed all six PR workflows. Deployment Readiness, PostgreSQL Concurrency, Telegram Runtime Contracts and Browser Staff E2E passed; PM-018 focused authority proof also passed inside CI.
- Main SQLite result was **256 failed, 1575 passed, 20 skipped, 2 errors**. Locked result was **261 failed, 1568 passed, 20 skipped, 3 errors**. The directly fixed `consultation_intake.py` unclosed-file warning disappeared, but aiosqlite unclosed-connection / closed-event-loop warnings remained, so PM-021 was not closed and no PM-018 closure decision was made.
- The first run exposed two test-infrastructure gaps rather than a PM-018 product regression: many historical tests create their own `sqlite+aiosqlite` engines outside `app.db.session`, and `Dockerfile.test` declared `APP_ENV=local`, so the focused shared-engine `NullPool` assertion failed only in the locked image.
- Added pytest-only `tests/conftest.py` interception before test-module import: test-owned SQLite/aiosqlite engines that do not explicitly request a pool now use `NullPool`. Explicit pool tests can still override `poolclass`; production/staging code is unchanged.
- Aligned `Dockerfile.test` to `APP_ENV=test` and expanded `tests/test_test_db_event_loop_lifecycle.py` to prove both the shared application engine and a direct historical-style aiosqlite engine use `NullPool`, plus sequential fresh-loop DB access.
- A fresh exact-head rerun is required. Until that result, PM-021 remains `FIXED_PENDING_RUNTIME`; PM-018 remains bounded with focused proof green but not formally closed, and PM-019 remains `NOT_STARTED`.

## 2026-09-19 — PM-021 second runtime isolated a NullPool overreach

- Exact candidate `43fcf4e843da5ff5ddbd1d037447b3391cfe7053` removed the targeted file-backed aiosqlite leakage signal from both suites: the CI SQLite job and locked-image suite reported no `Connection ... deleted before being closed` and no `RuntimeError: Event loop is closed` markers.
- The same candidate also exposed a bounded test-infrastructure regression in the new pytest constructor guard: it applied `NullPool` to `sqlite+aiosqlite:///:memory:`, so PM-018 focused tests that intentionally create one in-memory database per engine lost their schema between connection checkouts and failed with `sqlite3.OperationalError: no such table`. This is not a PM-018 product failure.
- Aggregate results on that superseded candidate were SQLite **275 failed, 1557 passed, 20 skipped** and locked **276 failed, 1556 passed, 20 skipped**. The increase is not accepted as remediation evidence because the in-memory pool regression contaminated the suite.
- The test-only constructor guard is now narrowed to **file-backed** aiosqlite only. In-memory SQLite retains SQLAlchemy's normal shared-engine pool semantics. Focused lifecycle proof now locks both contracts: file-backed test engines use `NullPool`; in-memory engines preserve schema/data across logical connections.
- PM-021 remains `FIXED_PENDING_RUNTIME` until this corrected exact head reruns. PM-018 remains bounded and not formally closed; PM-019 remains `NOT_STARTED`.

## 2026-09-19 — PM-021 corrected runtime closes the PM-018 differential decision

- Corrected candidate `a048e7abd9f3c3259c86d77ea2f03351e2d58a0a` passed Deployment Readiness, PostgreSQL Concurrency, Telegram Runtime Contracts, Browser Staff E2E, PostgreSQL migration/backup/restore, container startup, Process Map governance and the dedicated PM-018 authority/M2→M1 proof.
- Full SQLite remained inherited-red at **253 failed, 1580 passed, 20 skipped**; locked remained inherited-red at **254 failed, 1579 passed, 20 skipped**. These numbers are not presented as a green release baseline.
- The PM-021 target is proven fixed: both normal and locked logs contain **0** unclosed-aiosqlite connection markers and **0** `RuntimeError: Event loop is closed` markers. The focused lifecycle tests also pass.
- Exact failure-identity comparison against stacked base `c1aac0d977b0a26fc0e21a13cbcfe5a6ffc2f5aa` found **0 current-only FAIL/ERROR node ids** in either suite. Normal: all 253 current failing nodes are shared with the base, with 8 base failures absent. Locked: all 254 current failing nodes are shared with the base FAIL/ERROR set, with 7 base nodes absent.
- Decision: **differential proof is sufficient to close PM-018 as a bounded product change**. A completely green branch-wide baseline is still mandatory for merge/release under the Acceptance contract, but it is no longer a prerequisite for deciding whether PM-018 itself introduced a regression.
- PM-019 is therefore allowed to start after this recorded decision, but remains `NOT_STARTED` in this batch.
- One residual non-database unclosed source-file warning is explicitly separated into PM-023 / GitHub issue #128; it is inherited test hygiene and does not reopen PM-021 or PM-018.

## 2026-09-19 — PM-022 role journey and visual UX hardening

- Audited the client Telegram cabinet plus current human staff roles against the approved M1/M2 boundary. Current runtime roles are administrator, lawyer and superadministrator; operator/tester are auxiliary access flags rather than independent product workspaces.
- Preserved the canonical client routing and business authority. The direct Telegram reply-menu router already precedes historical compatibility handlers, keeps exact selected-Case provenance, fails closed when several active Cases are ambiguous, and presents client-safe status/next action instead of CRM codes.
- Normalized the persistent `📁 Моё дело` presentation to the same `СЕЙЧАС / ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ` hierarchy as the canonical inline My Case screen without changing callbacks, Case selection, state transitions or payment/document authority.
- Improved `/operator`: superadmin no longer renders the redundant inherited admin label; consultation schedule is directly discoverable in administrator daily work; duplicated SLA/Telegram-control links were removed; normal administrators now use canonical Diagnostics rather than the compatibility Monitoring URL; and a distinct superadmin-only `Руководство и контроль` group exposes Access, Security, Audit, Backup and Retention.
- Added visible `:focus-visible` treatment and `aria-live` status feedback to the staff landing.
- Extended real Browser Staff E2E: admin and lawyer primary/support surfaces are checked at a 390px phone viewport for horizontal overflow; a real signed MFA-verified superadmin session verifies the leadership landing and protected `/access/ui`, `/security-events/ui`, `/audit-center/ui`, `/backup-center/ui`, `/retention/ui` surfaces.
- Added focused source contract `tests/test_pm022_journey_visual_contract.py` to lock role composition and the Telegram visual hierarchy without weakening inherited tests.

- Continued the same visual pass through the supervisory surfaces themselves: Audit, Backup and Retention now use Russian page titles/navigation and expose a direct return to `Руководство и контроль`; financial Payment Review/Refund surfaces return to the canonical `/operator` hub instead of the legacy `/admin-ui` shortcut.
- Leadership Browser Staff E2E now runs at a 390px viewport and checks horizontal overflow on Access, Security, Audit, Backup and Retention. Administrator mobile acceptance also covers Notification Delivery, Diagnostics, Health and Settings, so secondary/support navigation is tested rather than only the primary Workdesk.
- Closed a real RBAC/UX dead end discovered during the role walk: `/security-events/ui` is superadmin-only, so it is no longer advertised to normal administrators. The administrator control group links directly to `/diagnostic-center/ui`; Security moved under leadership.
- Rebuilt the Diagnostics presentation from a raw JSON `<pre>` into `Сейчас → Главный следующий шаг → Вторичные действия`, added responsive cards and canonical `/operator` recovery. Settings and Health also recover to `/operator`; Health copy is fully Russian; Security is now `Контроль безопасности` with leadership navigation and role-safe 403 recovery.
- Client journey pass also corrected persistent Telegram navigation against the approved visibility contract: first entry shows Home, Calculator and Contact Lawyer; active Case adds My Case and Documents; completed state keeps Home, New Calculation, read-only My Case and Contact Lawyer as a new M2/help entry. Old/stale Telegram buttons remain fail-closed in their destination handlers.
- Lawyer workspace, consultation desk and contract surface now expose visible keyboard-focus treatment in addition to their existing responsive layouts; this closes a presentation accessibility gap without changing legal actions or authority.
- Removed the duplicate Diagnostics naming from the administrator hub: full Diagnostics and the lightweight Health check are now clearly separated as `Диагностика сервиса` and `Быстрая проверка`; the diagnostic API projection no longer advertises legacy `/admin-ui`.
- First full locked comparison on exact pre-fix PM-022 head found four **current-only** failing node IDs. They were all presentation/test-contract deltas owned by PM-022: one operator role-source assertion, one stale stable-menu assertion that contradicted the approved dynamic visibility rule, and two recovery assertions still expecting legacy `/admin-ui`. These tests were updated to the approved role/menu/navigation contract; inherited baseline failures were not repaired.
- The menu test correction is requirement-driven rather than failure suppression: the functional/UX specification explicitly makes `Моё дело` and `Документы` conditional on Case existence while Home/Calculator remain globally available and Contact Lawyer is the M2 entry.
- Source status is `FIXED_PENDING_RUNTIME`. PM-022 is stacked on PM-021 so its runtime evidence must not be used to make the PM-018 closure decision until the lower test-infrastructure candidate is classified independently.
- First PM-022 Browser Staff E2E on superseded head `6a08fb0...` executed 5 scenarios: 4 passed and the new superadmin scenario failed before navigation because the test called `asyncio.run()` from inside Playwright's synchronous event-loop bridge. This was a test-harness defect, not a portal failure. The session token is now generated before entering `sync_playwright()`; exact-head rerun is required.

## 2026-09-21 — PM-019 My Case projection and next-action authority

- Started PM-019 only after PM-018/PM-021 were separated and PM-022 had a bounded differential result. Base of PR #130 is the exact PM-022 candidate `ceca95365dad69f72972c5f50e87e6d18c82e7a9`; PM-018 transition authority and migration 0024 are unchanged.
- Added one deterministic client projection in `app/bot/client_case_view.py`. Every supported `CaseStatus` has explicit client-safe copy for the current situation and client requirement. Unknown state or an explicit route/status conflict fails closed to `Статус уточняется` + legal-team contact rather than exporting the raw state.
- Removed the free-form `Case.next_action` fallback from client presentation. Internal status strings, `Case.internal_comment` and arbitrary staff text are not a source for My Case copy.
- Limited document precedence to the M1 document collection/review contour. A historical/rejected document no longer overrides a later court/payment/enforcement state. M2 documents remain optional: `M2_DOCUMENTS_OPTIONAL` projects slot selection as the main step.
- Removed `Document.lawyer_comment` from the My Case blocker. The detailed document workflow may render its own deliberate client instruction, but the aggregate case projection never treats the staff comment field as client-safe copy.
- Client Case history now strips document-review audit comments; staff history retains its bounded audit comment projection. My Case shows only the latest normalized client-visible event title.
- Strengthened the stale-action fingerprint with monotonic `Case.version`, action copy, document facts, consultation state, unread-message facts, all persisted payment state/timestamps and latest client-visible history event id.
- Home and My Case now consume the same fields: current stage → what is happening now → required from client → blocker → main next step → factual summary. Unread lawyer messages remain visible as a secondary signal and no longer silently replace an unrelated process action shown in the projection.
- Kept a compatibility adapter for `my_case._payment_summary`, but it delegates to the shared client payment summarizer; active My Case itself no longer runs a second payment-projection path.
- Added dedicated CI job `PM-019 My Case projection proof` and `tests/test_pm019_my_case_projection.py`. The focused proof executes 9 tests covering full status coverage, fail-closed route/state mismatch, document precedence, internal-copy privacy, audit-comment privacy, payment/version stale snapshot and shared Home/My Case semantics.
- The first complete normal-suite pass before contract reconciliation exposed eight PM-019-owned current-only nodes. They were all expected contract deltas: one client-history comment expectation, two CI job-count assertions, two old `ГОТОВНОСТЬ` visual assertions, and three unread-message/action-key expectations. Those tests were updated to the approved PM-019 contract; inherited branch-wide business failures were not repaired.
- Current source classification before exact governed rerun: `IMPLEMENTED / FOCUSED_PROOF_GREEN / DIFFERENTIAL_RERUN_REQUIRED`. The next exact candidate must show no PM-019-owned current-only failures in both normal and locked suites before bounded completion can be recorded.

## 2026-09-24 — v47 production acceptance topology and explicit offline payments

- Recorded live Timeweb evidence: GitHub/Google IPv4 egress works; Telegram IPv4 times out; host Telegram IPv6 works; current bridge containers have no IPv6 route. PM-024 owns this infrastructure/runtime gap.
- Added `docker-compose.timeweb.split.yml`: web is HTTP-only on app/proxy networks; `legal-concierge-bot` uses host networking and owns polling, durable notification delivery and scheduler. PostgreSQL/Redis are exposed only on loopback for this worker.
- Added `app/bot/worker.py` supervision plus `scripts/telegram_worker_probe.py`; deployment, status, acceptance and rollback now treat web + worker as one immutable release and verify polling singleton identity.
- Replaced the v29 source-only acceptance script with live PostgreSQL/Alembic/audit/security-key/backup/payment/release evidence and bumped application identity to `1.0.0-v47`.
- PM-025 formalizes `PAYMENT_PROVIDER=offline` as the current production manual-reconciliation mode. It persists real obligations without external links and uses authenticated staff receipt confirmation through the canonical payment-success lifecycle for M1/M2. `disabled` is now fail-closed/not-ready in production.
- LIVE_REQUIRED now records the candidate's selected payment mode: offline contract evidence when offline is deployed, or YooKassa test-shop evidence only when YooKassa is explicitly selected.
- Updated PRODUCT_SCOPE, SYSTEM_CONTRACT, ACCEPTANCE, RUNBOOK, LIVE_REQUIRED and POST_LIVE documents to the same semantics. No new legal route, migration or client web cabinet was introduced.
- This map commit is the governed end of the source batch. All changes remain `FIXED_PENDING_RUNTIME`; no live /start, controlled reboot, persona, document, scheduler-time or full-baseline PASS is claimed yet.

## 2026-09-24 — Calculator final-step incident exposes missing production rule authority

- Real Telegram acceptance reached step 4 with a valid factual date `25.05.2026`, then showed the generic “Расчёт временно не сохранён / повторите дату” recovery. The accepted date itself is not the identified defect: PM-017 middleware persists accepted calculator facts before the final handler.
- Source inspection confirms `CalculatorService` now resolves an APPROVED `CalculationRuleRevision` for the calculation date and deliberately has no env/default legal-rate fallback. Migration 0022 deliberately seeds no legal rates, coefficients, moratoria or exclusions.
- PM-016 is corrected from the stale “engine not implemented” description to the current truth: engine implemented, production rule data/approval still required.
- Telegram now catches the full `CalculationRuleError` family as a rule/configuration blocker, tells the client the answers are saved, and no longer asks to repeat the same date. A focused regression locks this behavior.
- `scripts/production_acceptance.py` now resolves the current-date rule authority and fails acceptance when it is absent/ambiguous/tampered. It exposes only safe readiness metadata (revision key/error type), never rule secrets.
- This is a source-side correction only. The live incident is not considered fixed until a lawyer-approved production revision is activated and the same persona calculation succeeds against PostgreSQL/Audit evidence.

## 2026-09-25 — PM-016 v2 governed legal rule editor, provenance and production authority

- Rebuilt PM-016 around an explicit legal workflow: `Базовая формула → Источник ставки → Моратории → caps → Тип клиента → Уникальный объект → stop-factors → Справочник ставок ЦБ → Контрольные примеры → DRAFT → LEGAL_REVIEWED → APPROVED → PRODUCTION`.
- Added migration `20260924_0025`, exact legal-review SHA-256, separate approval/publication facts and immutable calculation evidence for gross amount, amount cap, excluded segments and manual-review outcomes.
- The engine now fixes the base rate on the contractual due date and applies only source-backed rule data. Missing authority, rate gaps, unverified due-date coverage, hash mismatch, unknown legally significant facts and configured stop factors all fail closed.
- Every source used by schema v2 requires a human-readable title, exact article/point/table locator and HTTPS URL. The staff Case card shows only sources actually used by the saved calculation; the client can reopen the same legal/calculation basis from the result and from `Моё дело`/archive. Long Telegram provenance is paginated without dropping links.
- DRAFT clearing/replacement is source-aware: deleting a section/row removes its references and orphan sources are pruned; deleting a source that is still referenced cannot pass validation. Reviewed/approved/production rows are not edited in place.
- Added a guided browser editor while preserving raw JSON as an advanced audit/diagnostic surface. Administrators edit DRAFT; lawyers can first run control examples and then confirm the exact SHA; SUPERADMIN separately approves/publishes.
- The repository review template is not a migration/default. Its Bank of Russia key-rate directory was rechecked against the official CBR history and is currently explicit through **25.09.2026 at 14.00%**; later official dates require a new reviewed DRAFT rather than mutation of historical PRODUCTION evidence.
- Bounded runtime evidence exists on the last runner-backed predecessor: PM-016 focused proof passed **60 tests**, Browser Staff E2E passed, Telegram Runtime Contracts passed and PostgreSQL Concurrency passed. On locked full-suite candidate `0106d14...`, five current-only failing node IDs were identified; four deterministic PM-016-owned deltas (installed-template path and CI meta-counts) were corrected. The fifth was an unclosed-aiosqlite warning surfacing inside an inherited payment guard test and was not shown to be a deterministic PM-016 product failure.
- After those corrections, GitHub Actions stopped allocating executable steps to the new PR jobs: jobs complete almost immediately with `steps=null`/no log blob, and explicit failed-job reruns behave the same. Therefore the final source candidate is recorded as **FINAL_DIFFERENTIAL_BLOCKED_INFRA**, not falsely promoted to a green release.
- Production activation remains deliberately separate: lawyer SHA review → SUPERADMIN APPROVED → explicit PRODUCTION publish → real client calculation → PostgreSQL/Audit/source-link reconciliation. PR #133 stays Draft until those gates are complete. The user subsequently delegated the eventual merge action to the assistant; this is authorization to merge **after** the evidence gates, not authorization to bypass them.
- Added PM-026 for controlled future CBR freshness. Any future importer may prepare DRAFT evidence only; it must never rewrite an immutable published revision.

## 2026-09-25 — Client scope expansion: PM-027 self-filing package and PM-028 fresh start

- Recorded two post-MVP client requests. The approved original specifications contain only M1/M2 and currently treat Home as a Case-aware cabinet; neither a self-filing package nor a non-persistent calculator preview exists in the original MVP contract. These are explicit new scope, not reinterpretations of the old documents.
- PM-027 is intentionally modelled as an **M1 service mode**, not M3. Business contract: client files in court independently; legal team prepares the filing package for 15 000 ₽; service is available for clients across Russia; SLA is 2 working days only after both confirmed payment and lawyer-confirmed document completeness. The final court/jurisdiction fact must be confirmed by a lawyer. Delivery requires protected in-product access plus durable email evidence. GitHub issue #134 owns the implementation.
- PM-028 source work started on branch `pm-028-fresh-start-preview-20260925` stacked on the governed PM-016 head. /start, /menu and Home now render a neutral start surface instead of auto-rendering saved Case detail. Existing work is opened only through an explicit Continue/My Cases action.
- Added a six-step ephemeral calculator using `PreviewCalculatorStates`. Before save it reads the same production legal-rule authority but creates no User/Case/CalculationIntake/Calculation/history. Unknown legal facts remain fail-closed. Home discards the preview; selected saved Case activity is not refreshed by neutral/preview browsing.
- `preview_calc_save` is the single materialization boundary: it creates an exact idempotent Case, copies validated preview facts into durable intake, saves the immutable Calculation and refuses to materialize if the production rule key/SHA changed after the client saw the preview result. Duplicate delivery reopens the already-created calculation instead of appending another one.
- Canonical inline/persistent calculator entry points now lead to preview. Historical `calc_start` remains mounted as a compatibility/recovery path for old Telegram buttons and existing durable calculator contracts.
- Added focused PM-028 source regressions. Exact runner-backed runtime evidence is still pending; no green claim is made until GitHub Actions executes the new branch.
- Merge governance changed only in ownership, not in threshold: the user explicitly asked the assistant to perform the eventual merge. PRs remain Draft and unmerged until their required exact-head/runtime/production gates are satisfied.

## 2026-09-26 — PM-028 preview save hardened against double-tap and presentation failure

- Re-audited the explicit-save boundary after the first PM-028 source batch and found a real idempotency gap: `preview_calc_save` was keyed by Telegram `callback.id`. Two distinct callback ids generated by a rapid double-tap of the same preview result could therefore be interpreted as two source operations and create two Cases.
- Replaced callback-id ownership with a stable 32-hex preview materialization token generated once per ephemeral preview. The exact token is carried in `preview_calc_save:v2:<token>` and mapped to the durable operation key `calculator_preview_save:<token>`.
- The database idempotency ledger remains `CaseCreationRequest(client_id, operation_key)`; therefore distinct Telegram callbacks for the same preview now converge on the same operation/Case. A stale save button from another preview cannot borrow the current FSM facts: if no already-materialized Case exists and callback token != current state token, save fails closed.
- Already-materialized results are resolved by the stable preview token before requiring current FSM facts. This preserves recovery when PostgreSQL committed successfully but Telegram presentation failed or the FSM was subsequently cleared.
- Corrected write ordering in `_present_saved`: PostgreSQL commit now precedes `state.clear()`. If commit fails, the unsaved preview remains available for retry instead of being erased first.
- Expanded `tests/test_pm028_fresh_start_preview.py` to lock stable materialization-key ownership, reject callback-id idempotency, prove stale-token comparison and enforce commit-before-FSM-clear ordering.
- Added dedicated CI job **PM-028 fresh start and preview proof**. Source status remains `SOURCE_IMPLEMENTED / RUNTIME_PENDING`; no green runtime claim is made until Actions allocates a runner and executes the exact-head job.

