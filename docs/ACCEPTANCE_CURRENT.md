# ACCEPTANCE — CURRENT

Status: **authoritative current acceptance contract** for the existing M1/M2 product.

Historical persona matrices are evidence/history only. They do not override this file. A scenario may be marked source-correct while still requiring live proof; source inspection is never presented as a completed runtime test.

## Evidence states

- **SOURCE_OK** — current source/schema design satisfies the stated contract by inspection and/or non-live tests.
- **LIVE_REQUIRED** — must be executed against the production-like runtime stack before release.
- **BLOCKED_INFRA** — cannot be executed because required infrastructure is unavailable; never treated as PASS.
- **LIVE_PASS** — scenario was actually executed and UI, PostgreSQL state and required audit/history/evidence agreed.
- **LIVE_FAIL** — runtime result disagreed with the contract.

A release claim requires the mandatory scenarios below to be `LIVE_PASS`, not merely `SOURCE_OK`.

## Current automated evidence present in the branch

The following gates now exist in source and are part of the intended CI/runtime pyramid:

- `tests/test_telegram_multi_case_runtime.py` — real aiogram `Dispatcher.feed_update` coverage for persistent Calculate, duplicate callback idempotency, multi-Case selector, stale Case-bound payment action and exact read-only message history;
- `tests/test_telegram_redis_runtime.py` + `.github/workflows/telegram-runtime.yml` — Redis FSM restart/persistence and exact Case binding;
- `tests/test_postgres_multi_case_concurrency.py` — same/different Case creation operation keys, terminal-operation replay and Calculation history under PostgreSQL;
- `tests/test_postgres_payment_concurrency.py` — payment creation, duplicate success, duplicate refund confirmation and M2 hold-expiry/payment-success races;
- `tests/test_postgres_staff_concurrency.py` — conflicting Document review and conflicting Case branch decisions under PostgreSQL;
- `tests/test_v37_api_import_inventory.py` — exactly one runtime `(HTTP method, path)` owner plus canonical hardened owners for staff/payment/lawyer/backup/workdesk surfaces;
- lifecycle/transaction tests for Payment timestamps/events, PaymentEvent immutability, Case closure/archive, business timezone and AsyncSession presentation boundaries;
- source regressions for calculator new-vs-resume separation, exact Case-bound unknown-data recovery and stale selected-Case reply-menu behavior.

Until the workflows actually execute on allocated runners, this is **SOURCE_OK evidence only**. It does not change any `LIVE_REQUIRED` state below.

## Corrected multi-Case acceptance

### C-010 — global Calculate while another Case is active

Expected contract:

1. Client has active Case A; Case A may itself be an unfinished calculator flow.
2. Client presses persistent **Calculate** or current inline **new calculation** entry.
3. The explicit global `calc_start` means **new calculation / new legal matter** and must not be reinterpreted by router precedence as “resume Case A”.
4. A genuinely new source operation may create active Case B.
5. Case A remains unchanged and active.
6. A redelivery/retry of the **same operation key** returns/deduplicates to the same Case instead of creating Case C.
7. Calculation history is one-to-many per Case.
8. No client-wide active-Case uniqueness error occurs.
9. Resuming an unfinished calculation in Case A is a separate explicit Case-bound action (`calc_recover:v2:<case_id>`); a stale recovery action must fail closed rather than implicitly creating a replacement Case.

The former expectation “a second active Case must not be created” is obsolete and incorrect. Likewise, overloading the global Calculate callback as an implicit resume command is incorrect.

State before release: **LIVE_REQUIRED**.

### C-011 — Back navigation

Expected contract:

1. Client opens a sequence of replay-safe screens such as My Case → Documents → History.
2. Back returns to the previous logical replay-safe screen when available.
3. A payment creation, slot reservation, contract/legal confirmation or other mutation is never replayed by Back.
4. When logical history is unavailable, the fallback is My Case/Home without deleting persisted data.
5. Redis/bot restart must not corrupt persistent Case state; missing navigation state degrades safely.

The former statement “true Back is a P2 gap” is obsolete.

State before release: **LIVE_REQUIRED**.

## Mandatory multi-Case / stale Telegram scenarios

| ID | Scenario | Expected result | Pre-release state |
| --- | --- | --- | --- |
| MC-01 | two distinct Calculate operations by same client | two independent Cases; neither overwrites the other | LIVE_REQUIRED |
| MC-02 | duplicate same Calculate callback/operation | one Case for that source operation | LIVE_REQUIRED |
| MC-03 | My Case with two active Cases | explicit selector; selected Case visibly identified | LIVE_REQUIRED |
| MC-04 | stale selector for now-terminal Case | selection rejected; context unchanged | LIVE_REQUIRED |
| MC-05 | stale payment/legal v2 callback from Case A after selecting B | mutation rejected; Case B untouched | LIVE_REQUIRED |
| MC-06 | old raw mutation callback with multiple active Cases | fails closed unless narrowly proven by trusted exact message context | LIVE_REQUIRED |
| MC-07 | message-history pagination after Case switch | exact Case id preserved; no cross-Case messages | LIVE_REQUIRED |
| MC-08 | Case-history pagination after Case switch | exact Case id preserved; no cross-Case events | LIVE_REQUIRED |
| MC-09 | documents/replacement callback from old Case | exact document/Case/version check; no cross-Case upload mutation | LIVE_REQUIRED |
| MC-10 | selected unfinished calculator Case + persistent/global New Calculate | new Case is created; unfinished selected Case is not resumed or modified | LIVE_REQUIRED |
| MC-11 | Case-bound calculator recovery after switching/closing Case | stale recovery fails closed; current selected Case remains untouched; no replacement Case is created | LIVE_REQUIRED |

## M1 persona acceptance

Execute as real Telegram client + separate real lawyer/admin accounts against PostgreSQL/Redis and the same application image intended for production.

Required full path:

calculation → M1 choice → exact-version consent → DDU/document upload → lawyer review → accept/request/reject branches → service contract evidence → initial payment → power of attorney → claim preparation/sent evidence → 30-day gate → court stage/evidence → second payment → enforcement → actual recovered amount → success fee → `M1_CLOSED` with structured close reason → archive/read-only access.

Failure/recovery variants must include:

- duplicate callback delivery;
- Telegram presentation failure after committed mutation;
- stale messages after status change;
- document replacement while old version exists;
- provider timeout/duplicate webhook/late failure;
- scheduler and client action race;
- process restart between major stages.

Acceptance rule: client-facing result, PostgreSQL Case/Document/Payment state, Case history/audit, consent evidence and payment ledger must agree.

State before release: **LIVE_REQUIRED**.

## M2 persona acceptance

Required full path:

question description → optional documents → slot list → reservation → confirmation/payment → booked consultation → preparation → lawyer result → close / follow-up / to-M1.

Mandatory exception paths:

- two clients race for one slot;
- hold expires during payment creation;
- paid stale reservation;
- duplicate payment success;
- success followed by late failure;
- reschedule after Case switch/stale Telegram message;
- cancellation;
- client no-show → explicit rebook or close with structured reason;
- lawyer no-show → free rebook or refund path;
- payment review/refund resolution;
- restart with Redis FSM preserved;
- Redis state unavailable/lost: persistent Case remains safe and no accidental mutation is inferred.

State before release: **LIVE_REQUIRED**.

## Payment acceptance

For each real/sandbox financial transition verify:

1. current `payments.status`;
2. correct business timestamp (`paid_at`, `failed_at`, `cancelled_at`, `refunded_at`, `expired_at`);
3. one normalized `payment_events` transition per persisted status change;
4. provider evidence in `payment_webhook_events` when event came from provider;
5. Case/Audit history explains business application/review/refund outcome;
6. no stale money changes the wrong Case/slot/legal stage.

Historical `payment_events` rows are append-only evidence at the application boundary: ordinary ORM update/delete must fail closed; corrections are represented by later financial events, not rewritten history.

Mandatory provider matrix: create, provider timeout after successful creation, idempotent retry, duplicate success, duplicate webhook, late failure, stale reservation payment, stale M1 payment, refund pending, refund confirmed, refund declined/review, process restart.

State before release: **LIVE_REQUIRED**.

## Consent / service-contract evidence acceptance

For consent:

- exact version token resolves to exact text/SHA;
- Telegram user owns Case;
- duplicate CallbackQuery id is idempotent;
- stale version is rejected;
- acceptance/decline writes status/date/version/text hash/provenance;
- Case history references the evidence.

For the service contract, execute the legally approved acceptance/signing model and verify the stored evidence is no stronger/weaker than the approved legal procedure.

Legal decision about click-accept vs signed document/external e-sign must be recorded before production. Technical implementation must not call a Telegram click a qualified electronic signature.

State before release: consent source path **SOURCE_OK**, legal-operational signing decision **LIVE_REQUIRED / business approval required**.

## PostgreSQL concurrency acceptance

Must run on real PostgreSQL, not SQLite only:

- same Case creation operation twice;
- two distinct Case operations for one client;
- delayed replay of a creation operation after its original Case has closed while another Case is selected;
- repeated Calculations;
- two clients attempt same slot;
- slot cleanup vs payment webhook;
- double payment creation;
- duplicate provider webhook;
- webhook vs admin payment review/refund;
- duplicate refund confirmation/retry;
- two staff update one Document;
- two staff update one Case;
- scheduler vs client callback.

Source tests now cover the Case/calculation, core payment/refund/slot and staff Document/Case races listed above. Provider/admin/scheduler race variants that require a fuller runtime remain mandatory.

State before release: **LIVE_REQUIRED**.

## Telegram + Redis integration acceptance

Feed real aiogram `Update` objects through the dispatcher with Redis FSM and production router order. Cover commands, persistent reply buttons, callbacks, stale v2/raw messages, drafts, multi-Case switch, Back, restart and callback redelivery.

The branch now contains direct `Dispatcher.feed_update` multi-Case tests and a Redis-backed restart contract. These do not replace a real Telegram test-bot/staging walkthrough.

Explicitly verify that an unfinished selected calculator Case does not shadow global **New Calculate**, while `calc_recover:v2:<case_id>` resumes only its exact still-active Case.

Verify client activity timestamps do not commit unfinished legal transactions and inactivity reminders deduplicate by stable client/stage snapshot. Unrelated staff/system updates to `Case.updated_at` must not postpone a reminder; a newly entered client-action stage receives its own quiet period.

State before release: **LIVE_REQUIRED**.

## Staff browser acceptance

Use browser E2E (Playwright or equivalent) for:

login → MFA where configured → role boundaries → Workdesk → Lawyer workspace → document review → messages → consultation outcome/no-show → payment review/refund → session expiry/revoke.

A staff UI route must have one runtime owner; role safety must not depend on router include order. Current regression contract requires route-free retired facades rather than shadow routes for superseded staff UI guards.

State before release: **LIVE_REQUIRED**.

## Timezone acceptance

Persist sample consultation timestamps in UTC and verify Telegram/home/action-center/scheduler notifications render the configured business timezone consistently, including DST-capable zone conversion even if default Moscow does not currently switch DST.

State before release: **LIVE_REQUIRED**.

## Security / document acceptance

Run at minimum:

0-byte/broken PDF, content-type disguise, oversized upload, duplicate content, same filename different content, replacement while old version is open, expired download grant, revoked staff access, reassigned Case, multiple tabs, historical decrypt after key rotation, quarantine cleanup, session revoke, compromised admin response drill.

State before release: **LIVE_REQUIRED**.

## Backup / restore acceptance

A backup is not accepted merely because an encrypted archive exists.

Mandatory drill:

backup → separate staging restore → migrations/schema check → staff login → open Case → decrypt historical Document → read Audit/Case history → verify Payment + payment events → start bot smoke against restored DB.

State before release: **LIVE_REQUIRED**.

## CI / release gate

Required automated gate includes at least compile/static architecture check, migration chain, SQLite fast tests, PostgreSQL migration/integration/concurrency tests, Redis/deployment contract tests, container build/start smoke and backup/restore checks configured by CI.

The repository contains dedicated PostgreSQL concurrency and Telegram/Redis runtime workflows, and PR-trigger rules were widened so non-`main` base PRs are eligible to run. A configured workflow is not evidence of execution.

GitHub Actions runner execution is tracked by infrastructure issue **#116**. If a job stops before runner allocation/steps, current status is **BLOCKED_INFRA**, not PASS. The branch must not be described as CI-green or production-ready until runners actually allocate and mandatory jobs pass.

## Production-release decision

Release is allowed only when:

- no duplicate runtime `(method, path)` ownership;
- current migrations apply cleanly to production-like PostgreSQL;
- mandatory M1/M2 personas are LIVE_PASS;
- payment sandbox matrix is LIVE_PASS;
- Redis/Telegram restart cases are LIVE_PASS;
- staff browser E2E is LIVE_PASS;
- backup restore/security drills are evidenced;
- actionable monitoring/alert ownership exists;
- legal consent/contract signing procedure is approved;
- GitHub Actions/infrastructure blocker is removed and required CI gates pass.

Visual polish may continue after these technical gates, but no new legal route is part of this release.
