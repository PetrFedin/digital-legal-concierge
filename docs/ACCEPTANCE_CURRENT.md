# ACCEPTANCE — CURRENT

Status: **authoritative current acceptance contract** for the existing M1/M2 product.

Historical persona matrices and GO_LIVE/FINAL documents are evidence/history only. They do not override this file. Source inspection or the presence of a workflow/test is never presented as completed runtime proof.

The exact execution order and evidence-retention procedure is defined in `docs/POST_LIVE_RELEASE_EVIDENCE.md`. `docs/LIVE_REQUIRED_RUNBOOK.md` defines the automated LIVE_REQUIRED component contract. `docs/PROCESS_MAP_CURRENT.md` is the mandatory living implementation/debt/change inventory; it records what a repository change affected but never substitutes for runtime evidence required here.

## Evidence states

- **SOURCE_OK** — source/schema/test design satisfies the stated contract by inspection and/or non-live tests.
- **LIVE_REQUIRED** — must execute against the required production-like runtime before release.
- **BLOCKED_INFRA** — required infrastructure cannot execute; never treated as PASS or as an application test failure.
- **LIVE_PASS** — the scenario actually executed and UI/runtime state/PostgreSQL/audit/evidence agreed.
- **LIVE_FAIL** — an executed runtime result disagreed with the contract.

A release claim requires all mandatory gates for the frozen candidate SHA to be `LIVE_PASS` in the required order.

## Mandatory release evidence order

For one immutable candidate SHA:

1. GitHub Actions runner allocation is restored and jobs execute real steps.
2. Full CI and all required PR checks execute and pass, including the `Process map maintenance contract` governance check.
3. Dedicated PostgreSQL concurrency → Redis/Telegram runtime → browser staff E2E workflows execute and pass on that SHA.
4. One complete `.github/workflows/live-required.yml` run passes and produces one SHA/run/attempt-bound `LIVE_REQUIRED_MANIFEST.json`.
5. Real Telegram M1 and M2 persona walkthroughs execute with UI ↔ PostgreSQL ↔ Audit/PaymentEvent reconciliation.
6. Encrypted backup → separate empty staging/restore database and storage → application evidence → normal restored-runtime usability executes and passes.
7. Only after Gates 1–6 pass may YooKassa test-shop evidence be deliberately expanded to provider-side paid/refund scenarios that can be completed safely without any production credential, production shop, production callback or production operation.
8. Only then may the release/merge decision be made.

Any source, migration, workflow or evidence-script change after evidence collection begins creates a new candidate SHA and restarts the chain from full CI. Evidence from another SHA or another LIVE_REQUIRED attempt is diagnostic only.

## Living process-map acceptance

Every pull request must update `docs/PROCESS_MAP_CURRENT.md` in the same PR. The map must identify affected `P-*` process(es), update the `PM-*` inconsistency/debt register when a problem is discovered or resolved, and append the dated change log. CI enforces presence of that file in the PR diff through the `Process map maintenance contract` job.

This is a repository-governance gate, not application runtime proof. Passing it cannot promote any process from `SOURCE_OK`/`RUNTIME_PENDING` to `LIVE_PASS`; failure means the PR is incomplete even when application tests are otherwise green.

## Automated/source evidence present in the branch

The branch includes, among other gates:

- `tests/test_telegram_multi_case_runtime.py` — aiogram `Dispatcher.feed_update`, duplicate callbacks, multi-Case selector, stale Case-bound actions and read-only history;
- `tests/test_telegram_redis_runtime.py` + `.github/workflows/telegram-runtime.yml` — Redis FSM restart/persistence and Case binding;
- `tests/test_postgres_multi_case_concurrency.py` — Case creation idempotency/history races;
- `tests/test_postgres_payment_concurrency.py` — payment creation/success/refund/review and hold-expiry races;
- `tests/test_postgres_staff_concurrency.py` — conflicting Document/Case staff mutations;
- `tests/test_postgres_auto_assignment_concurrency.py` — two M1 Cases compete for one final lawyer capacity slot without oversubscription; it is included in the dedicated `.github/workflows/postgres-concurrency.yml` gate as well as LIVE_REQUIRED;
- `tests/test_browser_staff_e2e.py` — staff auth/role isolation and two-tab stale Payment Review 409 recovery;
- `tests/test_live_required_evidence.py` + `tests/test_live_required_evidence_workflow_contract.py` — exact SHA/run/run-attempt manifest contract;
- `tests/test_post_live_restore_evidence.py` — post-LIVE restore evidence tamper/source-target/storage/privacy contract;
- `tests/test_document_storage_portability.py` — portable encrypted storage keys, legacy absolute-path rebasing and traversal/symlink/case-scope protection;
- `tests/test_document_download_storage_scope_contract.py` — normal authorized document download must pass the authorized Case id into storage resolution;
- lifecycle/transaction tests for Payment timestamps/events, `PaymentEvent` immutability, Case closure/archive, business timezone and transaction boundaries;
- `tests/test_v37_api_import_inventory.py` + `scripts/architecture_check.py` — one runtime `(HTTP method, path)` owner.

Until runners execute these gates, their presence is **SOURCE_OK only**.

## Multi-Case acceptance

### C-010 — global Calculate while another Case is active

Expected:

1. Client has active Case A.
2. Persistent **Calculate** / explicit global `calc_start` means a new calculation/legal matter, not implicit resume of A.
3. A distinct operation may create active Case B while A remains unchanged.
4. Retry/redelivery of the same operation key returns the same Case rather than creating C.
5. Calculation history is one-to-many per Case.
6. Resume of unfinished A is a separate Case-bound `calc_recover:v2:<case_id>` operation.
7. Stale recovery fails closed and never fabricates a replacement Case.

State: **LIVE_REQUIRED**.

### C-011 — Back navigation

Expected:

1. Back replays only read/idempotent screens.
2. Payment creation, slot reservation, contract/legal confirmation and other mutations are never replayed by Back.
3. Missing logical history degrades to My Case/Home without deleting persisted data.
4. Redis/bot restart cannot corrupt persistent Case state.

State: **LIVE_REQUIRED**.

### Mandatory stale/multi-Case matrix

| ID | Scenario | Expected result | State |
| --- | --- | --- | --- |
| MC-01 | two distinct Calculate operations | two independent Cases | LIVE_REQUIRED |
| MC-02 | duplicate same Calculate operation | one Case for that operation key | LIVE_REQUIRED |
| MC-03 | My Case with two active Cases | explicit selector and visible selected Case | LIVE_REQUIRED |
| MC-04 | stale selector for terminal Case | rejected; context unchanged | LIVE_REQUIRED |
| MC-05 | stale payment/legal callback from A after selecting B | rejected; B untouched | LIVE_REQUIRED |
| MC-06 | old raw mutation callback with multiple active Cases | fail closed unless exact trusted context proves scope | LIVE_REQUIRED |
| MC-07 | message pagination after Case switch | exact Case only | LIVE_REQUIRED |
| MC-08 | Case-history pagination after switch | exact Case only | LIVE_REQUIRED |
| MC-09 | old document/replacement callback | exact Case/document/version only | LIVE_REQUIRED |
| MC-10 | selected unfinished calculator + global New Calculate | new Case; selected unfinished Case unchanged | LIVE_REQUIRED |
| MC-11 | stale Case-bound calculator recovery | fail closed; no replacement Case | LIVE_REQUIRED |

## M1 persona acceptance

Execute with a real Telegram acceptance client plus separate real lawyer/admin acceptance accounts against PostgreSQL/Redis and the same application image intended for release.

Required path:

calculation → M1 choice → exact-version consent → DDU/document upload → lawyer review → accept/request/reject branches → service-contract evidence → initial payment → power of attorney → claim preparation/sent evidence → 30-day gate → court/evidence → second payment → enforcement → actual recovered amount → success fee → structured `M1_CLOSED` → archive/read-only.

Mandatory recovery/edge variants include duplicate callback delivery, presentation failure after committed mutation, stale message after status change, document replacement/version history, payment timeout/retry/duplicate provider evidence, scheduler/client race and process restart between major stages.

Acceptance requires client UI, Case/Document/Payment projections, Case history/AuditLog, consent evidence and normalized payment ledger to agree.

State: **LIVE_REQUIRED**.

## M2 persona acceptance

Required path:

question description → optional documents → slot list → reservation → confirmation/payment → booked consultation → preparation → lawyer result → close/follow-up/to-M1.

Mandatory exceptions include two-client slot race, hold expiry during payment creation, paid stale reservation, duplicate success, late failure after success, stale reschedule after Case switch, cancellation, client no-show, lawyer no-show, rebook/refund, Payment Review recovery and Redis restart/loss behavior.

Persistent Case state must remain safe when Redis state is missing; no mutation may be inferred from transient FSM loss.

State: **LIVE_REQUIRED**.

## Payment acceptance

For every persisted financial transition verify:

1. current `payments.status`;
2. correct business timestamp (`paid_at`, `failed_at`, `cancelled_at`, `refunded_at`, `expired_at`);
3. one normalized `payment_events` record per persisted transition;
4. `payment_webhook_events` when provider evidence exists;
5. Case/Audit history explaining business application/review/refund outcome;
6. no stale money mutating the wrong Case, slot or legal stage.

`PaymentEvent` is append-only evidence. Corrections are later events, never history rewrites.

### Automated LIVE_REQUIRED provider baseline

The provider component of LIVE_REQUIRED is intentionally limited to a safe YooKassa **test-shop** preflight + create + exact idempotent retry + retrieve. Every object must prove `test=true`; the payment remains unpaid/pending and the smoke does not open/complete confirmation.

This baseline proves provider connectivity/idempotent creation only. It does **not** prove provider-side paid/refund lifecycle.

### Application/PostgreSQL financial semantics

Before provider-side paid/refund expansion, application/PostgreSQL gates must already prove duplicate success, webhook↔admin Payment Review convergence, stale reservation payment, refund confirmation/retry and hold-expiry/payment races. Those are application semantics, not provider-side sandbox proof.

### Provider-side paid/refund sandbox matrix

Only after Telegram persona acceptance **and** encrypted backup→restore acceptance pass may the test-shop run be expanded to safe provider-side paid/refund scenarios. Target scenarios include, where YooKassa test-shop behavior deterministically supports them: confirmation→paid, duplicate/retried success evidence, refundable paid object, refund creation/retrieve/terminal result and provider failure/review states.

Hard requirements:

- credentials are proven test-shop credentials before mutation;
- every provider object reports `test=true`;
- no production shop/payment/callback/credential is used;
- required user/test-card confirmation is performed explicitly rather than bypassed;
- unsupported or ambiguous sandbox behavior is recorded as **NOT PROVEN**, never fabricated;
- application-only tests cannot satisfy provider-side proof.

State before Gate 7: baseline **LIVE_REQUIRED** through LIVE_REQUIRED; paid/refund provider-side expansion **BLOCKED_BY_SEQUENCE / LIVE_REQUIRED AFTER RESTORE**.

## Consent and service-contract evidence

Consent must prove exact version→exact text/SHA, Case ownership, duplicate CallbackQuery idempotency, stale-version rejection, status/date/version/text/provenance persistence and Case history reference.

The service-contract procedure must store evidence that is neither stronger nor weaker than the legally approved signing/acceptance model. A Telegram click must not be described as a qualified electronic signature unless an approved legal mechanism actually makes it so.

State: consent source path **SOURCE_OK**; legal-operational signing approval **LIVE_REQUIRED / business approval required**.

## PostgreSQL concurrency acceptance

Must execute on PostgreSQL, not SQLite only:

- same Case creation operation twice;
- two distinct Case operations for one client;
- replay after original Case terminal state;
- repeated Calculations;
- two clients racing for one slot;
- slot cleanup vs payment success/webhook;
- double payment creation;
- duplicate provider webhook/success;
- webhook vs admin Payment Review/refund;
- duplicate refund confirmation/retry;
- two staff updating one Document;
- two staff updating one Case;
- two M1 Cases competing for one last lawyer capacity slot;
- scheduler vs client callback.

The dedicated `PostgreSQL Concurrency` workflow must include the final-capacity auto-assignment race rather than relying on LIVE_REQUIRED alone to prove it.

State: **LIVE_REQUIRED**.

## Telegram + Redis acceptance

Use real aiogram updates plus Redis FSM in automated runtime gates, then a real Telegram acceptance bot for persona proof. Cover commands, reply buttons, callbacks, stale v2/raw messages, drafts, Case switch, Back, restart and callback redelivery.

Verify global New Calculate never becomes implicit resume; `calc_recover:v2:<case_id>` resumes only its exact active Case. Client activity timestamps must not commit unfinished legal transactions. Reminder dedupe is based on stable client/stage facts, not unrelated staff `updated_at` changes.

State: **LIVE_REQUIRED**.

## Staff browser acceptance

Browser E2E must cover login → MFA where configured → role boundaries → Workdesk → lawyer workspace → document review → messages → consultation outcomes/no-show → Payment Review/refund → session expiry/revoke.

Payment Review must include the two-tab stale decision case: the winner commits exactly once; the stale tab receives 409 + authoritative server truth, preserves its local draft/comment and cannot create a second resolution.

A staff route must have one runtime owner; authorization may not depend on router include order.

State: **LIVE_REQUIRED**.

## Auto-assignment / SLA acceptance

Automatic assignment applies only to approved M1 working states after documents and never to M2/calculator/client-decision states.

Required proof includes active lawyer+staff identity, workload/capacity enforcement, assignment audit/SLA start, exact retry vs stale snapshot handling, and the PostgreSQL race where two eligible Cases compete for one final capacity slot: exactly one assignment is allowed and oversubscription is forbidden.

State: **LIVE_REQUIRED**.

## Timezone acceptance

Persist UTC timestamps and verify Telegram/staff/scheduler rendering uses configured `BUSINESS_TIMEZONE`, including DST-capable conversion behavior.

State: **LIVE_REQUIRED**.

## Security / encrypted document acceptance

At minimum execute: zero-byte/broken PDF, content-type disguise, oversized upload, duplicate content, same name/different content, replacement/version history, expired one-time grant, revoked staff access, reassignment, multiple tabs, historical decrypt after key rotation, quarantine cleanup, session revoke and compromised-admin response drill.

Storage-specific acceptance:

- new `Document.file_path` values are canonical portable keys `cases/<case_id>/<32hex>.dlcenc`;
- prefixed relative paths are rejected, not suffix-normalized;
- legacy absolute paths are reduced only to their terminal canonical key and rebased onto the **current** `STORAGE_DIR`;
- old source filesystem prefixes are never dereferenced after restore;
- traversal and symlink components fail closed;
- normal authorized download passes the authorized Case id to storage resolution, so a wrong-Case storage key cannot be opened through a valid grant.

State: **LIVE_REQUIRED**.

## Backup / restore acceptance

An encrypted archive alone is not acceptance.

This gate executes **after** Telegram M1/M2 personas so the backup contains exercised business evidence.

Required sequence:

1. capture a privacy-minimized pre-backup witness using `scripts/post_live_restore_evidence.py snapshot` from the exact candidate SHA;
2. independently retain the snapshot file SHA-256;
3. create and verify the authenticated encrypted backup;
4. restore only into a separate empty PostgreSQL database with a safe staging/restore/drill/test name plus separate restored storage;
5. run `post_live_restore_evidence.py verify` against the restored environment;
6. require current Alembic head, valid complete AuditLog hash-chain, exact Case/Document/Payment/PaymentEvent/Case-audit/staff facts, canonical storage key and historical V2 document decryption;
7. start the same application image against restored DB/storage and isolated Redis;
8. authenticate through normal staff login, open the Case/history/payment state and open/decrypt the historical document through the normal authorized application path;
9. start bot/dispatcher smoke without any production Telegram/provider side effect.

Evidence schema v2 uses the same portable-key rules as runtime. A legacy absolute DB path may be rebased to current restored storage; a prefixed relative path, wrong Case key, traversal or symlink fails closed.

Only encrypted backup verification + safe restore + `RESTORE_PASS` application evidence + normal restored-runtime usability equals **backup→restore LIVE_PASS**.

State: **LIVE_REQUIRED after Telegram personas**.

## CI / infrastructure gate

Required automated gate includes:

- `Process map maintenance contract` — mandatory repository/process/debt/change inventory update for every PR;
- compile/static architecture, Alembic chain and fast application tests;
- PostgreSQL migration/integration plus technical encrypted backup/restore drill;
- container build/start health;
- deployment readiness and Redis persistence;
- reproducible locked dependencies/images and complete locked-image test suite;
- dedicated PostgreSQL concurrency, including auto-assignment final-capacity race;
- dedicated Redis/Telegram runtime and browser E2E.

GitHub issue **#116** tracks the current runner/billing/spending blocker. `runner_id=0`, `steps=null`/empty steps or a run ending before runner execution is **BLOCKED_INFRA**. It is never reported as CI-green or as an application test failure.

## Production-release decision

Release/merge is allowed only when the frozen candidate SHA has one coherent evidence chain satisfying the ordered gates above, including:

- `docs/PROCESS_MAP_CURRENT.md` updated for the candidate with no discovered release blocker hidden outside its `PM-*` register;
- one runtime owner per `(method, path)`;
- clean PostgreSQL migration chain;
- full CI + dedicated runtime workflows PASS;
- one exact LIVE_REQUIRED manifest PASS;
- M1/M2 Telegram personas PASS;
- staff browser/Redis/security acceptance PASS;
- encrypted backup→restore PASS;
- provider-side paid/refund sandbox evidence completed to the extent safely and deterministically supported by YooKassa test shop, with unsupported scenarios explicitly marked rather than assumed;
- owned monitoring/alerts and approved legal consent/contract procedure;
- no unresolved release blocker.

PR #114 must remain unmerged until this evidence exists. No automatic merge is authorized.
