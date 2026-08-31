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

Evidence: `tests/test_m1_rejection_client_decision_contract.py` locks the rejected-M1 card wording/callback, the scope-guard delegation and presence of M2/message/close options. Runtime execution is still pending under PM-001/PM-007.

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

The protected deep-link UI is composed by `app/api/payment_review_renderer.py`: when `payment_id` is present it renders the same normalized timeline for active or terminal reviews, so a resolved payment stays explainable after disappearing from the active queue. `staff_ui_guards.py` owns authentication/redirect gating; the renderer owns final Payment Review HTML composition. `app/api/payment_review_product.py::NoStoreAPIRoute` now applies `Cache-Control: no-store` centrally to every response returned by the Payment Review product handlers and propagates the same directive through handler/dependency HTTP exceptions. The explicit history-JSON and protected-HTML headers remain as defense in depth. This history layer is presentation only and does not mutate `Payment`, `Case`, `Consultation` or slot state.

`PaymentLifecycleService` is the canonical application mutation boundary for an existing `Payment.status`. The model listener stamps/records invariant facts but is not the product decision boundary. Static architecture enforcement now recognizes obvious aliases/annotations/constructor aliases plus `setattr` and bulk SQLAlchemy status mutations outside `PaymentLifecycleService`.

Evidence: `tests/test_payment_review_history_contract.py` locks the admin-only read route, exact-payment filtering, terminal history availability, bounded matching timeline and privacy projection. `tests/test_payment_review_renderer_contract.py` locks single-owner composition, active/terminal deep-link history wiring, normalized fields only, deterministic rendering and endpoint-level non-cacheability. `tests/test_payment_review_product_contract.py` locks the central `NoStoreAPIRoute`, the exact five-route Payment Review surface and source-level propagation of `no-store` to returned responses and HTTP exceptions. `tests/test_browser_staff_e2e.py` binds the existing stale two-admin conflict scenario to the history UI: it checks the active REQUIRED event, terminal RESOLVED decision after the winner commits, the same server truth after stale 409 recovery, and absence of both administrators' free-text comments. Runtime execution remains pending under PM-001/PM-007/PM-011.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

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
- `app/api/workdesk_integrity.py` — integrity producer consumed through renderer;
- `app/api/payment_review_product.py` — single Payment Review product router plus central `NoStoreAPIRoute` response/HTTP-exception cache boundary;
- `app/api/payment_review_renderer.py` — single final Payment Review composition boundary for guided hierarchy + exact-payment audit-history panel;
- `app/api/staff_ui_guards.py` — protected Payment Review authentication/redirect gate and endpoint-level no-store response boundary, **no Payment Review template patching**.

Rules: one `(HTTP method, path)` runtime owner; include order must not define security; route/data/auth modules do not mutate foreign templates.

Evidence: `test_v37_api_import_inventory.py`, `architecture_check.py`, `test_browser_staff_e2e.py`, `test_workdesk_renderer_contract.py`, `test_payment_review_renderer_contract.py`, `test_payment_review_product_contract.py`.

State: `IMPLEMENTED / FIXED_PENDING_RUNTIME`.

## P-09 — Audit integrity and immutable evidence

Flow: `business/security action → AuditLog → chained integrity metadata → verification → backup/restore proof`.

Evidence families: `AuditLog` + `AuditChainHead`, immutable consent snapshot, append-only `PaymentEvent`, provider `PaymentWebhookEvent`, Case history.

Payment Review history is a privacy-bounded projection of `AuditLog`, not a new evidence store: the underlying audit rows and chain integrity remain authoritative and internal, while the staff API/UI expose only the minimum decision/provenance fields needed for reconciliation. The projection is non-cacheable through the Payment Review product route boundary, with endpoint-level history/UI headers retained as defense in depth, and does not duplicate or rewrite audit evidence.

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
| Case/action audit | `AuditLog`/Case history | timelines/audit center + privacy-bounded Payment Review history |
| Closure/archive/delete | separate lifecycle timestamps/reasons | terminal/read-only UI |
| Client inactivity | client/Case activity + stage-entry evidence | reminder scheduling |
| Runtime draft/navigation | Redis FSM | never legal source of truth |

# Automated verification map

## General pre-live gates

### `CI`

- `Process map maintenance contract`: file must exist, must be changed in the PR, and its latest commit must be at least as new as the latest governed repository commit;
- `tests/test_process_map_governance_contract.py` locks the presence/freshness source contract and root `AGENTS.md` wording;
- source compile;
- architecture check, including:
  - absolute/relative/literal-dynamic legacy assignment containment;
  - alias/type-aware direct/bulk `Case.status` and `Payment.status` mutation containment;
- SQLite suite, including CaseService/M2 compatibility, Workdesk renderer, legacy-assignment import guard, status-mutation architecture regressions, rejected-M1 client decision presentation/routing, Payment Review history privacy/exact-payment contracts, Payment Review renderer ownership/non-cacheability contracts and the central Payment Review product-router no-store boundary contract;
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

Staff role/session paths, Payment Review stale two-tab recovery, active→terminal exact-payment history rendering/privacy assertions and current canonical Workdesk route/rendering. The test source is wired for PM-011, but executable browser evidence remains pending until a runner actually executes steps.

## Manual `LIVE_REQUIRED`

One exact SHA/run/attempt must prove PostgreSQL, Redis, real Telegram delivery, browser, YooKassa test-shop provider baseline and aggregate manifest.

# Known inconsistency and debt register

| ID | Area | Finding | Risk | State / action |
| --- | --- | --- | --- | --- |
| PM-001 | Release infra | Actions jobs end before runner allocation (`runner_id=0`, empty/null steps); issue #116 | No runtime/test PASS can be claimed | `BLOCKED_INFRA`; external billing/spending/runner fix required |
| PM-002 | PostgreSQL dedicated gate | Auto-assignment capacity race existed in tests/acceptance/LIVE_REQUIRED but was omitted from dedicated PostgreSQL workflow | Dedicated gate could pass without assignment race proof | `FIXED_PENDING_RUNTIME`; test is now included in dedicated workflow |
| PM-003 | M2 state machine | Historical `M2_CONSULTATION_ROUTE` can exist in string-backed old rows, while canonical current M2 begins at `M2_DESCRIPTION_PENDING` | Re-entry would split semantics; immediate deletion could break historical rows | `FIXED_PENDING_RUNTIME / COMPATIBILITY_READ_ONLY`; CaseService initial validation + no-reentry transition rule + ORM backstop + historical-row hydration/forward-upgrade regression; physical removal deferred until real DB audit |
| PM-004 | Assignment architecture | Historical `AssignmentEngine`/`WorkloadService` coexist with hardened `CaseAssignmentService`; initial containment missed relative/literal-dynamic imports | Reintroduced legacy code could bypass locking/capacity/audit semantics while evading architecture check | `DEBT_OPEN / SOURCE_CONTAINED / HARDENED_PENDING_RUNTIME`; guard resolves relative `ImportFrom` and statically-resolvable literal dynamic imports; focused regression added; physical deletion still waits for historical/external-consumer audit + executable regressions |
| PM-005 | Workdesk composition | Final UI was assembled by route-level responsibility JS patch plus separate integrity injection | Distributed UI ownership could omit/duplicate behavior | `FIXED_PENDING_RUNTIME / COMPOSITION_CENTRALIZED`; single `workdesk_renderer.py`, route module no longer patches HTML, regression locks boundary |
| PM-006 | Repository governance | Private-repo ruleset required contexts cannot be independently enumerated through current GitHub API/plan | Formal branch protection contract is not API-proven | `DEBT_OPEN`; CI governance + root `AGENTS.md` implemented; manually verify repository rules after billing/runner recovery |
| PM-007 | Runtime evidence | Current storage/restore/retention/Payment Review/assignment/M2/Workdesk/governance regressions have not run on an Actions runner | Source correctness may hide runtime regressions | `RUNTIME_PENDING`; ordered gates required on current candidate |
| PM-008 | Living-map governance | Initial CI rule only required `PROCESS_MAP_CURRENT.md` to appear somewhere in total PR diff | A long PR could pass while the map had become stale | `FIXED_PENDING_RUNTIME / FRESHNESS_ENFORCED`; latest non-map governed commit must be an ancestor of latest map commit; AGENTS + focused regression lock the rule |
| PM-009 | Case/Payment mutation architecture | Initial `Case.status` check depended on literal variable name `case`; Payment check relied mainly on names containing `payment` and direct `Payment` class reference. Typed aliases, `setattr`, model aliases and several bulk SQLAlchemy forms could evade the static boundary | Product code could bypass `CaseService` or `PaymentLifecycleService`, splitting process/audit/SLA or financial timestamp/ledger semantics | `FIXED_PENDING_RUNTIME / ALIAS_AWARE_GUARD`: architecture check infers obvious model variables from imports/aliases/annotations/constructors/simple aliases, preserves conventional names, catches `setattr(..., "status", ...)` and model-referenced `.values/.update` bulk forms; `tests/test_status_mutation_architecture_guard.py` covers forbidden and allowed cases. This remains static containment, not a claim that arbitrary Python reflection is impossible |
| PM-010 | M1 rejection Telegram presentation | The `M1_REJECTED` backend/client recovery already exposed M2, message/team and close choices, but `ClientCaseView` described the callback as contact-only (`Уточнить решение`) | Client could miss valid next steps and the primary Case card contradicted the actual callback/product flow | `FIXED_PENDING_RUNTIME / PRESENTATION_ALIGNED`; card now says `Выбрать, что делать дальше`, describes consultation/close/team choices, preserves `contact_lawyer` and existing state machine; `tests/test_m1_rejection_client_decision_contract.py` locks presentation, delegation and choices; runtime pending under #116 |
| PM-011 | Payment Review audit traceability | Active Payment Review and 409 recovery used durable audit evidence internally, but staff had no stable exact-payment read surface once a review left the active queue | Terminal reconciliation could require raw audit inspection and risk leaking unrelated Case events, free-text comments or integrity/provider metadata | `FIXED_PENDING_RUNTIME / SAFE_HISTORY_UI_ADDED / NON_CACHEABLE_ROUTER_BOUNDARY`; admin-only `/{payment_id}/history` projects exact-payment REQUIRED/RESOLVED events through a strict whitelist, filters before the 20-event presentation bound, remains available for terminal payments, and `payment_review_renderer.py` surfaces that same normalized timeline on protected deep-links without adding a mutation path. `NoStoreAPIRoute` centrally applies `no-store` to returned Payment Review handler responses and propagates it through HTTP exceptions; explicit history/UI headers remain defense in depth. Deterministic regressions plus the real-browser stale-tab scenario are wired to assert topology, non-cacheability, active/terminal history and comment privacy. Runtime proof remains pending under #116 because the browser job still receives no executed steps |

# Development priority plan

## P0 — release truth and runtime recovery

1. Resolve #116 externally and prove real runner allocation (`runner_id != 0` + executed steps).
2. Freeze the then-current head and run `CI` + `Deployment Readiness` + `Reproducible Dependencies`.
3. Treat a real failure as application evidence; fix it, update affected P-/PM-items/change log, create a new candidate and restart full CI.
4. Validate PM-002, PM-003, PM-004, PM-005, PM-008, PM-009, PM-010, PM-011, storage/retention portability and Payment Review recovery/history in executable gates.
5. Run dedicated PostgreSQL/Redis/browser gates, then one complete LIVE_REQUIRED attempt only after general gates are green.

## P1 — controlled debt closure after executable CI exists

1. PM-004: finish exact historical/external consumer audit and delete legacy assignment modules only if safe-removal proof and regressions exist.
2. PM-006: verify actual branch/rules settings and exact required contexts; align them with the gate contract without weakening it.
3. Browser-check centralized Workdesk renderer for M1/M2 responsibility/action semantics.
4. Audit real staging/restore DB for remaining `M2_CONSULTATION_ROUTE` rows before considering a migration/removal of the compatibility enum.
5. Review PM-009 runtime results and decide whether a later model/session-level mutation authorization backstop is justified; do not add one speculatively before executable CI proves the static guard behavior.
6. Exercise PM-011 with an authenticated browser/API session against both active and terminal Payment Review records, including the post-409/terminal deep-link history panel and response cache headers, before promoting PM-011 out of `FIXED_PENDING_RUNTIME`.

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

## 2026-08-28 — PM-008 process-map freshness enforced

- Audited the initial `Process map maintenance contract` and found it only proved that `docs/PROCESS_MAP_CURRENT.md` appeared somewhere in total PR diff.
- Strengthened `.github/workflows/ci.yml`: latest non-map governed commit must be an ancestor of latest process-map commit. Same-commit changes pass; map-after-code passes; code-after-map fails closed.
- Updated root `AGENTS.md` and added `tests/test_process_map_governance_contract.py`.
- PM-008 remains runtime pending because Actions has not allocated a runner.

## 2026-08-28 — PM-004 legacy assignment containment hardened against bypass imports

- Re-audited legacy assignment guard and found relative and literal dynamic import bypasses.
- Added relative-module resolution plus detection for `importlib.import_module`, aliased `import_module`, and `__import__` when statically resolvable.
- Added `tests/test_legacy_assignment_import_guard.py` with forbidden bypass forms and allowed canonical assignment-service imports.
- Business assignment semantics were not changed; physical legacy-module deletion remains deferred.

## 2026-08-28 — PM-009 Case/Payment mutation guards made alias-aware

- Audited the two critical status-write architecture checks and found they could be bypassed by non-conventional variable aliases, typed aliases, `setattr`, aliased model classes or some bulk SQLAlchemy write forms.
- Added model symbol resolution for `Case` and `Payment`, including relative model imports, class aliases, module aliases, function/variable annotations, constructors and simple alias propagation.
- Expanded direct mutation detection to obvious subscript/conventional owners, `setattr(owner, "status", ...)`, `update(ModelAlias).values(status=...)`, model-referenced `.update({...})`, and equivalent statically-resolvable forms.
- `Case.status` remains owned by `CaseService`; `Payment.status` mutation remains owned by `PaymentLifecycleService`. Creation-time `Payment(status=...)` is deliberately not treated as a transition violation.
- Added `tests/test_status_mutation_architecture_guard.py`, including negative controls proving the rule does not globally ban another model such as `Document.status`.
- This is source-level containment only. Runtime CI must prove the stronger architecture check does not reveal additional current violations; if it does, those are to be handled as real application debt rather than weakening the check.

## 2026-08-29 — PM-010 M1 rejection client decision presentation aligned

- Re-audited the functional/UX rejection requirement against the exact production path and disproved the initial hypothesis that the negative M1 branch was absent: the state graph, lawyer rejection route and Telegram decision center already exist.
- Found the actual gap in `CLIENT_ACTIONS["M1_REJECTED"]`: the primary `Моё дело` card described `contact_lawyer` as only `Уточнить решение`, while that callback already opens the safe decision center with M2, team-message and close choices.
- Changed only the client presentation contract to `Выбрать, что делать дальше` with explicit consultation/close/team wording. Callback, Case state machine and mutation ownership were deliberately not changed.
- Added `tests/test_m1_rejection_client_decision_contract.py` to lock the card contract, `contact_lawyer_scope_guard` delegation and all three safe choices.
- Classified PM-010 as `FIXED_PENDING_RUNTIME / PRESENTATION_ALIGNED`; no runtime PASS is claimed because issue #116 still blocks actual Actions runner execution.

## 2026-08-31 — PM-011 Payment Review safe audit history added

- Added `app/api/payment_review_history.py` and the admin-only `GET /admin/payment-reviews/{payment_id}/history` route owned by the existing Payment Review product router.
- History remains readable after the payment leaves `PAID_REVIEW`, filters the exact payment after the Case/action query and only then applies the bounded 20-event presentation limit, so unrelated audit traffic cannot evict the target reconciliation evidence.
- Added a strict response whitelist: decision/status/actor/time and exact consultation/slot identifiers are exposed when relevant; free-text comments, raw audit JSON, provider/reservation payloads and audit-chain integrity fields remain internal.
- Added `tests/test_payment_review_history_contract.py` covering privacy projection, resolved/required normalization, exact-payment filtering before bounding, terminal payment history, 404 behavior, route ownership and missing-admin rejection.
- Classified PM-011 as `FIXED_PENDING_RUNTIME / SAFE_HISTORY_ADDED`; this source/test improvement creates a new candidate head but does not claim CI, browser, PostgreSQL or LIVE_REQUIRED PASS while #116 remains unresolved.

## 2026-08-31 — PM-011 Payment Review terminal history surfaced in protected UI

- Added `app/api/payment_review_renderer.py` as the single final composition owner for Payment Review guided hierarchy and the read-only exact-payment history panel; `staff_ui_guards.py` now handles only authentication/redirect policy plus the protected response boundary instead of patching the Payment Review template.
- Deep-links with `payment_id` load the normalized history alongside the existing queue/terminal state, so a resolved payment remains understandable after it leaves `PAID_REVIEW`; the panel reuses existing business-time formatting and decision labels and adds no Case/Payment/Consultation mutation path.
- Kept the UI projection privacy-bounded to the normalized history contract and marked both the history API response and protected Payment Review HTML `Cache-Control: no-store`.
- Added `tests/test_payment_review_renderer_contract.py` to lock unique/deterministic composition, exact-payment history wiring, normalized-field-only rendering, renderer ownership and non-cacheability.
- Extended `tests/test_browser_staff_e2e.py` so the existing two-admin stale-tab scenario now checks the REQUIRED history while active, the terminal RESOLVED decision after the winner commits, the same history after stale 409 recovery, and that neither the persisted winner comment nor the stale-tab draft comment appears in the history panel.
- PM-011 remains `FIXED_PENDING_RUNTIME`: the browser regression is now wired, but Actions/browser/PostgreSQL/LIVE_REQUIRED evidence is still required and no runtime PASS is claimed while issue #116 continues to produce jobs without executed steps.

## 2026-08-31 — PM-011 Payment Review no-store boundary centralized

- Added `NoStoreAPIRoute` to the single Payment Review product router so list, slot lookup, history, resolve and protected UI responses share one non-cacheable boundary rather than relying only on endpoint-by-endpoint headers.
- The route boundary sets `Cache-Control: no-store` on returned handler responses and preserves the same directive on `StarletteHTTPException` errors raised by handlers/dependencies; explicit history/UI headers remain as defense in depth.
- Added `tests/test_payment_review_product_contract.py` to lock the route class on the exact five-route surface and the source contract for returned-response/HTTP-exception header propagation.
- Updated P-05/P-08/P-09 and PM-011 without promoting runtime status: executable CI/browser evidence is still blocked by #116, so this is a source-level security hardening only.
