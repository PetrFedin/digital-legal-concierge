# LIVE_REQUIRED release runbook

Status: **SOURCE_OK / RUNTIME_PENDING for the current candidate**. Historical no-runner evidence remains diagnostic only; each candidate must prove its own runner allocation and executed steps.

This document defines the single fail-closed automated LIVE_REQUIRED matrix for the current M1/M2 release. It supplements `docs/ACCEPTANCE_CURRENT.md`; it does not convert any separate acceptance item to `LIVE_PASS` by itself.

**Prerequisite order:** after runner allocation is restored, first execute full CI/required PR checks, then the dedicated PostgreSQL concurrency → Redis/Telegram runtime → browser staff E2E workflows for the same frozen candidate SHA. Dispatch LIVE_REQUIRED only after those gates pass. The complete ordered release chain is authoritative in `docs/POST_LIVE_RELEASE_EVIDENCE.md`.

## Gate

Workflow: `.github/workflows/live-required.yml`

Trigger: manual `workflow_dispatch` only. The workflow is intentionally not a PR-time external side effect.

The aggregate `live-required` job is successful only when every required component reports `success`:

1. **PostgreSQL** — production migration chain plus multi-Case, payment/refund, staff and auto-assignment capacity concurrency contracts on PostgreSQL 16.
2. **Redis** — Redis FSM restart/loss recovery with PostgreSQL as persistent Case source of truth.
3. **Telegram** — a real Bot API identity call plus real silent message delivery to two distinct dedicated acceptance chats (admin and client), followed by cleanup. A mocked aiogram transport is not accepted as this component's evidence.
4. **Browser** — Playwright staff E2E against the application and PostgreSQL, including a two-tab Payment Review stale-state scenario where the losing tab must recover from HTTP 409 with authoritative server truth and must not overwrite the committed decision.
5. **Payment mode** — workflow input `payment_mode` must match the candidate. For `offline`, the gate proves the explicit manual-reconciliation contract and never fabricates an external provider operation. For `yookassa`, the production `YooKassaPaymentProvider` retrieves a known test-shop payment before create, then proves safe create + exact idempotent retry + retrieve with `test=true`, unpaid/pending state.

A failed, cancelled or skipped component fails the aggregate gate. Missing external secrets fail their job explicitly. There is no `continue-on-error` escape hatch.

## Durable evidence contract

A green workflow UI alone is not the release record. Every successful component job writes a machine-readable JSON record through `scripts/live_required_evidence.py` and uploads it as an Actions artifact. Artifact names include the current workflow attempt so a rerun cannot silently reuse another attempt's evidence:

- `live-required-evidence-postgres-<run_attempt>`;
- `live-required-evidence-redis-<run_attempt>`;
- `live-required-evidence-telegram-<run_attempt>`;
- `live-required-evidence-browser-<run_attempt>`;
- `live-required-evidence-payment-mode-<run_attempt>`.

A component record is created only after that component's verification command has succeeded. It contains the component name, exact `GITHUB_SHA`, workflow run id, workflow run attempt when available, UTC recording time and a non-secret description of the evidence. Component artifacts are retained for 30 days.

After the aggregate status gate proves all five jobs concluded `success`, the aggregate job downloads only artifacts suffixed with its own `github.run_attempt` and runs:

`python scripts/live_required_evidence.py aggregate ...`

The aggregator fails closed when:

- any required component record is missing;
- the same component appears more than once;
- a record is not marked successful;
- a record belongs to another commit SHA;
- a record was created by another workflow run when `GITHUB_RUN_ID` is available;
- a record was created by another workflow attempt when `GITHUB_RUN_ATTEMPT` is available;
- the evidence schema is unsupported.

Only after those checks does it create `LIVE_REQUIRED_MANIFEST.json` and upload `live-required-release-evidence-<SHA>-attempt-<run_attempt>` for 90 days. The manifest names the exact release SHA and embeds the verified component records, so the acceptance package no longer depends on reconstructing five transient job pages later.

The manifest field `status: LIVE_PASS` means **the automated LIVE_REQUIRED matrix represented by this workflow passed for that one SHA/run/attempt**. It does not waive separate manual acceptance items that `docs/ACCEPTANCE_CURRENT.md` still marks as required, including full persona walkthroughs and the later backup/restore/provider-side lifecycle gates.

## Required GitHub Actions secrets

Configure these before dispatching the workflow:

- `LIVE_TELEGRAM_BOT_TOKEN` — token of the dedicated non-production Telegram bot used for acceptance;
- `LIVE_TELEGRAM_ADMIN_CHAT_ID` — dedicated admin acceptance chat already reachable by that bot;
- `LIVE_TELEGRAM_CLIENT_CHAT_ID` — a distinct dedicated client acceptance chat already reachable by that bot;
For `payment_mode=offline`, no YooKassa secret is required.

For `payment_mode=yookassa`, configure:

- `LIVE_YOOKASSA_SHOP_ID` — YooKassa **test-shop** identifier;
- `LIVE_YOOKASSA_SECRET_KEY` — matching YooKassa **test-shop** secret;
- `LIVE_YOOKASSA_TEST_PAYMENT_ID` — id of an existing payment in that same YooKassa test shop; the gate retrieves it and requires provider `test=true` before creating anything;
- `LIVE_PUBLIC_BASE_URL` — externally valid HTTPS staging/test base URL used by the provider redirect contract.

The workflow deliberately maps those test-shop secrets to the existing `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` settings rather than introducing a second provider implementation.

## Payment-mode safety contract

For `payment_mode=offline`, LIVE_REQUIRED runs the focused offline payment contract: no external payment URL, explicit administrator receipt authority, M1/M2 eligibility and Telegram presentation. Actual bank-receipt reconciliation is still proven in the later real persona/manual acceptance and is never simulated.

For `payment_mode=yookassa`, `scripts/live_required_smoke.py provider`:

- uses the existing `YooKassaPaymentProvider` code path;
- retrieves `LIVE_YOOKASSA_TEST_PAYMENT_ID` first and requires the exact id plus `test=true` before making any create request;
- therefore a production credential set cannot be accepted as sandbox evidence and is rejected before a new provider payment object is created;
- limits the new smoke amount to 1..10,000 minor currency units; the workflow currently uses 100 minor units (`1.00 RUB`);
- creates a unique internal smoke identifier so the provider idempotence key cannot collide with an application Payment;
- calls the same create operation twice with the same internal payment id and requires the same provider payment id on retry;
- retrieves that new payment from YooKassa after the retry and requires the retrieved id to match;
- requires create, retry and retrieve evidence for the new operation to remain test-mode, unpaid and pending;
- never follows the returned confirmation URL and therefore never completes the user confirmation step;
- does not print credentials or the confirmation URL.

A failed test-shop probe, production-shop response, non-idempotent retry or unexpectedly paid/non-pending smoke payment is a hard failure, not a degraded pass.

This provider smoke proves connectivity, test-shop isolation and create/retry/retrieve idempotency. It **does not** prove a real paid/refund lifecycle at YooKassa because the gate deliberately never confirms the payment. Paid/review/refund race and recovery semantics are covered separately by PostgreSQL/application suites and must not be described as provider-side live proof.

Provider-side paid/refund sandbox expansion is a later Gate 7 operation. It is not permitted until the same candidate SHA has passed LIVE_REQUIRED, real Telegram M1/M2 persona walkthroughs and the encrypted backup→restore drill. See `docs/ACCEPTANCE_CURRENT.md` and `docs/POST_LIVE_RELEASE_EVIDENCE.md`.

## Telegram safety contract

`scripts/live_required_smoke.py telegram`:

- calls `getMe` against the real Telegram Bot API;
- requires two distinct configured acceptance chats rather than silently collapsing admin and client evidence into one recipient;
- sends one silent smoke message to each chat and verifies Telegram returned the exact target chat and a message id;
- deletes both smoke messages after delivery and fails if cleanup cannot be confirmed;
- prints only the bot id and delivery count, never the bot token or chat ids.

This proves external Bot API reachability and delivery to both acceptance roles. It complements, but does not replace, the dispatcher/Redis business-flow acceptance paths in the test suite or the later full M1/M2 persona walkthrough.

## Evidence required from a successful run

Do not change this automated matrix from `LIVE_REQUIRED` to `LIVE_PASS` unless all of the following are true for the same workflow run and attempt:

- prerequisite full CI and dedicated runtime workflows already passed for the same candidate SHA;
- each of the five component jobs has a real runner allocation and executed steps;
- each component conclusion is `success`;
- the aggregate `LIVE_REQUIRED aggregate gate` job executed and concluded `success`;
- no component was skipped because of missing configuration;
- the commit SHA under test is the exact release candidate SHA;
- all five component evidence artifacts for the current run attempt were uploaded;
- `LIVE_REQUIRED_MANIFEST.json` was generated by the aggregate job from those exact records and uploaded under the SHA/attempt-specific artifact name;
- the manifest SHA equals the release candidate SHA and all embedded component records belong to the same workflow run and attempt;
- PostgreSQL auto-assignment capacity race produced no oversubscription;
- the browser Payment Review stale tab observed a 409/server-truth recovery and produced no second resolution event;
- Telegram delivery succeeded to both distinct acceptance chats and cleanup succeeded;
- the selected payment-mode component passed for the exact candidate;
- for `offline`, no provider payment object was fabricated and manual receipt semantics stayed fail-closed until staff confirmation;
- for `yookassa`, the pre-existing probe was retrieved as `test=true` before create and exact create/retry/retrieve resolved to one unpaid/pending test-shop payment;
- browser/application logs do not show hidden server failures despite a passing browser assertion.

The release evidence package must retain the workflow run URL/id, run attempt, commit SHA, execution time, aggregate manifest and any external sandbox evidence required by the acceptance record.

## Infrastructure-blocked state

GitHub Actions infrastructure issue **#116** governs runner execution evidence. A workflow run that terminates before runner allocation, has `runner_id=0`, or contains no executed steps is **BLOCKED_INFRA**. It is never `LIVE_PASS`, even if GitHub's high-level run object appears completed.

When runner execution resumes, **do not dispatch LIVE_REQUIRED first**. Freeze the candidate SHA, run full CI/required checks, then the dedicated PostgreSQL concurrency → Redis/Telegram runtime → browser E2E gates. Only if those pass, dispatch one complete LIVE_REQUIRED run for that same SHA. After its SHA/run/attempt-bound manifest passes, execute real Telegram M1/M2 personas, then encrypted backup→restore, and only then the safe YooKassa provider-side paid/refund expansion. No later gate can compensate for a missing earlier gate.
