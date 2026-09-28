# SYSTEM CONTRACT — CURRENT

Status: **authoritative current implementation contract**. Historical architecture/audit documents are non-authoritative when they conflict with this file.

## 1. Process-state ownership

`Case.status` is the process-state source of truth. Legal/process transitions are performed through `CaseService` or dedicated domain services that use it. Generic UI status editing is not a supported business mechanism.

The transition policy defines legal ordering. Client buttons cannot establish lawyer, court, provider or recovered-money facts. Dedicated domain actions own those facts and write Case history/audit evidence.

Terminal Case lifecycle facts are separate fields: `closed_at`, `close_reason`, `archived_at`, `content_deleted_at`.

## 2. Cardinality and idempotency

Physical/implemented mapping:

| Logical contract | Physical implementation |
| --- | --- |
| Client → Cases = one-to-many | `cases.client_id` non-unique; no one-active-Case-per-client index |
| one active M1/M2 route per Case | `Case.route` + state-machine transition policy |
| M1 commercial service mode | `Case.service_mode`; self-filing is `SELF_FILING_PACKAGE`, never M3 |
| self-filing aggregate | one `self_filing_packages` row per Case with confirmed email, lawyer-confirmed act/jurisdiction, payment/delivery evidence and exact four deliverable pointers |
| Case → Calculations = one-to-many | `calculations.case_id` non-unique; latest calculation selected by query order |
| Telegram selected Case | `client_case_contexts(client_id, selected_case_id)` |
| idempotent Case creation for same source action | `case_creation_requests` unique `(client_id, operation_key)` |
| exact consent evidence | `consent_acceptances` with version/text/SHA/time/Telegram provenance |
| current payment projection | `payments` |
| provider receipt ledger | `payment_webhook_events` |
| normalized payment lifecycle ledger | `payment_events` append-only status snapshots |

No implementation may reintroduce a client-wide unique active-Case invariant.

Calculator creation and calculator recovery are intentionally different commands:

- `calc_start` is the global **new calculation / new Case** action. It remains available when another Case, including an unfinished calculator Case, already exists. Duplicate delivery of the same source action is deduplicated by the source operation key.
- recovery of an unfinished calculation in an existing Case uses the distinct Case-bound action `calc_recover:v2:<case_id>` (followed by the existing draft-resume actions inside that exact Case).
- router precedence must never reinterpret an explicit `calc_start` as recovery of the currently selected Case. Likewise, a stale recovery action must never create a replacement Case implicitly; it fails closed and offers an explicit new calculation separately.

## 3. Telegram exact-Case mutation contract

Every business mutation emitted by current Telegram screens must carry exact Case provenance when it can be ambiguous. Current v2 callback form is `<action>:v2:<case_id>` or another action-specific callback containing exact domain identifiers.

A bound callback is valid only when:

1. the Case belongs to the Telegram client;
2. the Case is still active when the action requires an active Case;
3. the selected Case still matches the bound Case for selected-context mutations;
4. the current state/action key still permits the action.

A bound stale callback does **not** silently switch selected Case.

Historical unbound mutation callbacks fail closed when the client has multiple active Cases. A narrowly scoped compatibility path may accept a historical raw callback only if a trusted bot-rendered message visibly identifies the exact currently selected Case and the downstream domain identifiers/state are still current.

Read-only history/message pagination now carries exact Case id. Viewing another owned Case is allowed without silently mutating selected context; mutating from that screen requires an explicit Case switch.

## 4. Navigation and FSM

`navigation_history_guard` stores a bounded replay-safe logical stack in FSM/Redis. Replayable targets are read/idempotent screens only. Payment creation, legal confirmation, slot reservation and other mutations are excluded.

Production Telegram FSM must use Redis. Loss/restart of bot process must not delete persistent Case data. Missing FSM state must degrade to database-backed current Case/Home rather than inventing a new mutation context.

Calculator/message drafts have explicit protection. Home/Back/Cancel do not delete persisted business data implicitly.

## 5. Documents and security

Documents are versioned domain records. Active and archived versions are distinct. Client replacement callbacks bind to exact `document_id` and expected version; stale replacement actions fail closed.

File upload controls include size/type/content checks, quarantine, hashing, encryption-at-rest/key versioning and protected download grants. File authorization is checked at access time; stale grants and revoked roles must not preserve access.

Document review decisions belong to staff role/domain services, not the client.

### Self-filing document authority

For new `SELF_FILING_PACKAGE` work, the deliverable set is closed and exact: `SELF_FILING_PRETRIAL_CLAIM`, `SELF_FILING_STATEMENT_OF_CLAIM`, `SELF_FILING_CLAIM_CALCULATION`, `SELF_FILING_CLIENT_ROADMAP`. A legacy `SELF_FILING_PACKAGE` document pointer may remain readable for historical rows but is not a fifth deliverable.

Each deliverable is an individually versioned, hashed, encrypted, lawyer-approved `Document`. The package may move to READY/email queue only when all four exact pointers resolve to usable APPROVED documents. Email delivery attaches those four documents and no legacy substitute.

The responsible lawyer also owns the transfer-act fact used for the claim-calculation cutoff. `transfer_act_signed`, `transfer_act_date`, confirmer and confirmation time are persisted before payment opens. If signed, the cutoff is the confirmed act date. If not signed, the cutoff is the received service-payment date in the configured business timezone and `claim_update_in_court_required=true`. The resulting calculator snapshot remains source material (`is_preliminary=true`) for the lawyer-authored claim-calculation document, not an automatic legal conclusion.

## 6. Consent evidence

`ConsentDecisionService` resolves a version token to an exact text/version/SHA, verifies Telegram ownership, locks the Case, deduplicates by Telegram CallbackQuery id and writes immutable `ConsentAcceptance` evidence plus Case history provenance.

Required logical evidence fields are stored, not derived: consent status, date, version, text snapshot, SHA and Telegram source identifiers.

The technical evidence record does not assert a legally stronger signature class than the approved legal procedure.

## 7. Payment lifecycle

`Payment` is the current projection. Business timestamps are first-class facts:

- received-money family → `paid_at`;
- failed → `failed_at`;
- cancelled → `cancelled_at`;
- refunded → `refunded_at`;
- expired → `expired_at`.

Application payment status transitions go through `PaymentLifecycleService`. A model-level invariant remains only as a defensive backstop and is not the primary business write path. An exact provider timestamp set before the status change is preserved.

`payment_events` records the initial Payment snapshot and each persisted status transition in the same database transaction. Existing pre-ledger payments receive one `LEGACY_BASELINE` event rather than a fabricated historical sequence. Application ORM update/delete of an existing `PaymentEvent` is rejected; corrections are represented by later financial events. Database-level permissions remain a separate deployment control.

`payment_webhook_events` remains the provider-event evidence/idempotency ledger when an external provider exists. Case/Audit history remains the actor/business-context ledger. These are complementary layers.

Production payment modes are explicit and non-equivalent:

- `offline` is a valid deployed manual-reconciliation mode. Creating the obligation records provider identity `offline` and no external URL. It does not mark the Payment paid.
- receipt confirmation is an authenticated administrator mutation requiring an expected current status plus a bank/accounting reference and explanatory comment. It uses the same `PaymentWebhookService.process_successful_payment` application boundary as verified provider success so Payment, PaymentEvent, Case transition, consultation booking, notification and Audit evidence stay coherent;
- M2 offline receipt remains bound to the exact consultation/reservation key. Confirmation of stale or inconsistent money follows the existing review/refund safety semantics instead of silently booking another slot;
- `disabled` is fail-closed in staging/production and is not release-ready. The historical no-payment bypass remains local/test only;
- `fake` is local/test only;
- `yookassa` requires the provider-specific credentials and evidence defined by acceptance/runbook.

Received money is protected from late failure overwrites. Stale money enters review/refund flow; it cannot silently reserve or reopen another stage.

### Self-filing bank-payment and delivery boundary

New `M1_SELF_FILING_PACKAGE` sales use a dedicated bank-transfer contract. The `Payment` snapshots the bar-association recipient/bank requisites plus the mandatory purpose `для адвоката Гамза Д.Г.` at obligation creation. Reopening an old payment must show that frozen snapshot rather than silently adopting later requisites. No provider URL is created for self-filing.

`PaymentService` creates the persisted 15 000 ₽ obligation and freezes `payment_purpose` plus `payment_details_snapshot`. Telegram and staff UI read those values. The client must not be sent to YooKassa/Robokassa/card/SBP for this service. Actual receipt is confirmed only after bank/accounting reconciliation; a client screenshot alone is not receipt authority.

Confirmed receipt of 15 000 ₽ is the immutable start of the customer delivery promise. `SelfFilingPackage.payment_confirmed_at` and `sla_started_at` are the received payment time; `sla_due_at = payment_at + 3 calendar days`. Business-calendar coverage does not extend this customer promise.

Before the Case moves into preparation, the service freezes the calculation-cutoff source snapshot using the lawyer-confirmed transfer-act fact and the legal-rule revision effective on that cutoff date. Failure to establish the rule snapshot, email channel or commercial contract sends received money to `PAID_REVIEW`; it never discards the received-money fact or asks the client to pay again.

The verified profile/delivery email is written to `User.email` and `SelfFilingPackage.delivery_email`. Successful completion requires actual email send evidence for the exact four documents; retry does not create a second payment or silently change the cutoff.

## 8. Client activity and reminders

`User.last_activity_at` and `Case.last_client_action_at` are updated in a short independent transaction after a Telegram update finishes and the handler session closes. This prevents activity tracking from committing unfinished legal/payment state and prevents read-only rollbacks from losing activity evidence.

Stage-aware inactivity reminders are generated only for approved unfinished client-action stages. Dedupe identity includes Case, stage and exact last-client-action timestamp, so one quiet snapshot produces one reminder; new activity can arm a new reminder later. The quiet-period anchor for the current stage comes from auditable Case stage-entry events; unrelated staff/system writes to generic `Case.updated_at` do not postpone a client reminder. Legacy records without stage evidence use a conservative fallback rather than an invented precise timestamp.

Reminders never mutate Case state.

## 9. Time handling

Database timestamps are UTC. `app.presentation_time` is the outward presentation boundary. `BUSINESS_TIMEZONE` defaults to `Europe/Moscow`; label defaults to `МСК`.

Telegram, notification and staff presentation code must use the shared formatter instead of individually calling `strftime` and appending an assumed timezone.

## 10. Staff/API route ownership

Runtime public route ownership contract is **one `(HTTP method, path)` → one owner**.

Security and business behavior must not depend on FastAPI include order. Application code must not mutate another router's `.routes` table and must not patch a foreign UI template at import time.

A compatibility module that no longer owns a public path is a **route-free facade**. Its historical implementation may remain importable in a `*_impl.py` module while consolidation is in progress, but mounting the facade cannot recreate a shadow route. If a historical URL must remain, it has one explicit compatibility owner (for example the technical-case redirect) rather than a competing early guard.

Current ownership boundaries include:

- `backup_manager` — existing `/backup-center/*` status/UI/verification paths; historical backup-center/guard facades are route-free;
- `message_center_product` — Message Center API + role-safe UI; role-UI compatibility facade is route-free;
- `lawyer_product` — shared lawyer UI and M2 completion/no-show paths; base `lawyer` facade owns only its unique M1/API paths;
- `admin_queue_guard` — the existing hardened compatibility paths `/admin/queue`, `/admin/lawyers`, `/admin/scheduler/run-once`; base `admin` facade excludes those paths;
- `payment_safety_guard` — fake local/test payment endpoints; `payment_webhooks` owns only real YooKassa/result endpoints;
- `workdesk_product` — canonical Workdesk UI; `workdesk_timeline` owns timeline/recovery composition without import-time HTML mutation;
- `technical_cases_compat` — the one legacy technical-card UI redirect; technical recovery owns only data/mutation endpoints;
- `payment_review_product`, `refund_product`, `sla_product`, `consultation_outcomes_product`, `document_access_product` — their existing staff product surfaces.

`tests/test_v37_api_import_inventory.py` asserts that every runtime `(method, path)` has exactly one owner. `architecture_check.py` remains a release gate and must pass naturally; neither gate may be weakened to tolerate duplicates.

The target assembly layers remain:

- client/bot product;
- staff product;
- platform/operations.

Daily legal Workdesk and Lawyer workspace must not expose release/maintenance internals to ordinary staff roles.

## 11. Logical spec → storage policy

Current policy for notable logical fields:

| Logical field | Policy |
| --- | --- |
| case number/client/route/status/source/assigned lawyer | stored |
| selected client Case | stored in `client_case_contexts` |
| calculation history/latest | history stored; latest derived |
| priority | derived from SLA/attention; no independent mutable priority truth required |
| responsible admin | intentionally queue/workdesk-based for now; no mandatory `responsible_admin_id` truth |
| close reason | stored structured code |
| closed time | stored |
| archive time | stored when archive semantics apply |
| retention deletion time | stored separately |
| client last activity | stored |
| Case last client action | stored |
| unread message counts | derived from message records |
| document readiness/blockers | derived from document versions/review state |
| payment current state/timestamps | stored |
| payment financial history | stored append-only in `payment_events`; provider evidence stored separately |

Any new column must resolve an existing approved M1/M2 fact that cannot be reliably derived; schema growth is not a goal itself.

## 12. Transaction boundary rule

Async SQLAlchemy ORM objects must not be used for lazy/expired attribute access after `commit()` or `rollback()` in presentation code. Values required after a transaction boundary are snapshotted to scalars/dataclasses before the boundary.

Read-only rendering should release DB transactions before Telegram/network I/O when safe. Durable business writes are committed before best-effort Telegram presentation; a Telegram delivery failure must not invite re-execution of an already committed legal/financial mutation.

## 13. Quality contract

Source inspection is not runtime proof. Production acceptance requires the same behavior to agree across:

**UI → domain result → PostgreSQL state → audit/history/financial evidence**.

Current automated regression surfaces include:

- real aiogram `Dispatcher.feed_update` multi-Case/stale-callback tests;
- Redis FSM restart/Case-binding contract;
- PostgreSQL multi-Case/calculation concurrency;
- PostgreSQL payment creation, duplicate-success, refund-idempotency and M2 hold-expiry/payment races;
- PostgreSQL conflicting Document-review and Case-branch staff races;
- transaction-boundary, payment lifecycle/event ledger, Case lifecycle/timezone and route-ownership regression tests.

These test files/workflows being present is **SOURCE_OK evidence only until they actually execute**. SQLite/source tests remain fast regression layers but cannot replace PostgreSQL concurrency, Redis FSM, provider contract, browser E2E and staging persona tests.

## 14. Living process-map governance

`docs/PROCESS_MAP_CURRENT.md` is the mandatory living implementation inventory for this contract. It is subordinate to the product/system/acceptance/runbook contracts: it may describe implementation, evidence state, discovered inconsistencies and technical debt, but it may not silently expand M1/M2 scope or weaken any authoritative rule above.

Every pull request that changes repository behavior, data, migrations, API/UI, Telegram, scheduler, security, storage, deployment, tests, workflows, evidence tooling or release procedure must update `docs/PROCESS_MAP_CURRENT.md` in the same PR. The `CI` job **Process map maintenance contract** enforces this requirement for pull requests.

The map must keep four things current together:

1. affected end-to-end `P-*` process and source-of-truth ownership;
2. actual evidence state (`SOURCE_AUDITED`, `RUNTIME_PENDING`, `BLOCKED_INFRA`, `LIVE_PASS` or `LIVE_FAIL` as applicable);
3. every discovered inconsistency/debt as a stable `PM-*` item until it is explicitly fixed or retired with evidence;
4. a dated change-log entry describing the repository change and its release/evidence impact.

A `PM-*` item is not closed merely because files were renamed or logic moved. Closure requires the corrective action to be identified and, where the contract requires runtime proof, the relevant gate to execute on the current candidate SHA. The process map itself is documentation/governance evidence, not runtime proof.
