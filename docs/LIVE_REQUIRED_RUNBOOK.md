# LIVE_REQUIRED release runbook

Status: **SOURCE_OK / BLOCKED_INFRA until a hosted runner actually allocates and executes every job**.

This document defines the single fail-closed runtime gate for the current M1/M2 release. It supplements `docs/ACCEPTANCE_CURRENT.md`; it does not convert any acceptance item to `LIVE_PASS` by itself.

## Gate

Workflow: `.github/workflows/live-required.yml`

Trigger: manual `workflow_dispatch` only. The workflow is intentionally not a PR-time external side effect.

The aggregate `live-required` job is successful only when every required component reports `success`:

1. **PostgreSQL** — production migration chain plus multi-Case, payment/refund, staff and auto-assignment capacity concurrency contracts on PostgreSQL 16.
2. **Redis** — Redis FSM restart/loss recovery with PostgreSQL as persistent Case source of truth.
3. **Telegram** — a real Bot API identity call plus real silent message delivery to two distinct dedicated acceptance chats (admin and client), followed by cleanup. A mocked aiogram transport is not accepted as this component's evidence.
4. **Browser** — Playwright staff E2E against the application and PostgreSQL, including a two-tab Payment Review stale-state scenario where the losing tab must recover from HTTP 409 with authoritative server truth and must not overwrite the committed decision.
5. **Provider sandbox** — the production `YooKassaPaymentProvider` first retrieves a pre-existing known test-shop payment to prove the configured credentials are non-production before any create call, then creates one small unconfirmed payment, repeats the exact create command to prove provider idempotency, retrieves the resulting provider truth, and requires `test=true`, `paid=false`, `status=pending` for the new operation. The confirmation URL is validated but never opened by the smoke.

A failed, cancelled or skipped component fails the aggregate gate. Missing external secrets fail their job explicitly. There is no `continue-on-error` escape hatch.

## Durable evidence contract

A green workflow UI alone is not the release record. Every successful component job writes a machine-readable JSON record through `scripts/live_required_evidence.py` and uploads it as an Actions artifact. Artifact names include the current workflow attempt so a rerun cannot silently reuse another attempt's evidence:

- `live-required-evidence-postgres-<run_attempt>`;
- `live-required-evidence-redis-<run_attempt>`;
- `live-required-evidence-telegram-<run_attempt>`;
- `live-required-evidence-browser-<run_attempt>`;
- `live-required-evidence-provider-sandbox-<run_attempt>`.

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

The manifest field `status: LIVE_PASS` means **the automated LIVE_REQUIRED matrix represented by this workflow passed for that one SHA/run/attempt**. It does not waive separate manual acceptance items that `docs/ACCEPTANCE_CURRENT.md` still marks as required, including full persona walkthroughs, backup/restore or other explicitly separate drills.

## Required GitHub Actions secrets

Configure these before dispatching the workflow:

- `LIVE_TELEGRAM_BOT_TOKEN` — token of the dedicated non-production Telegram bot used for acceptance;
- `LIVE_TELEGRAM_ADMIN_CHAT_ID` — dedicated admin acceptance chat already reachable by that bot;
- `LIVE_TELEGRAM_CLIENT_CHAT_ID` — a distinct dedicated client acceptance chat already reachable by that bot;
- `LIVE_YOOKASSA_SHOP_ID` — YooKassa **test-shop** identifier;
- `LIVE_YOOKASSA_SECRET_KEY` — matching YooKassa **test-shop** secret;
- `LIVE_YOOKASSA_TEST_PAYMENT_ID` — id of an existing payment in that same YooKassa test shop; the gate retrieves it and requires provider `test=true` before creating anything;
- `LIVE_PUBLIC_BASE_URL` — externally valid HTTPS staging/test base URL used by the provider redirect contract.

The workflow deliberately maps the YooKassa secrets to the application's existing `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` settings rather than introducing a second provider implementation.

## Provider safety contract

`scripts/live_required_smoke.py provider`:

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

This provider smoke proves connectivity, test-shop isolation and create/retry/retrieve idempotency. It **does not** by itself prove a real paid/refund lifecycle at YooKassa because the gate deliberately never confirms the payment. Paid/review/refund race and recovery semantics are covered separately by the PostgreSQL/application suites and must not be described as provider-side live proof until a dedicated safe sandbox scenario actually executes them.

## Telegram safety contract

`scripts/live_required_smoke.py telegram`:

- calls `getMe` against the real Telegram Bot API;
- requires two distinct configured acceptance chats rather than silently collapsing admin and client evidence into one recipient;
- sends one silent smoke message to each chat and verifies Telegram returned the exact target chat and a message id;
- deletes both smoke messages after delivery and fails if cleanup cannot be confirmed;
- prints only the bot id and delivery count, never the bot token or chat ids.

This proves external Bot API reachability and delivery to both acceptance roles. It complements, but does not replace, the dispatcher/Redis business-flow acceptance paths in the test suite or the later full M1/M2 persona walkthrough.

## Evidence required from a successful run

Do not change acceptance state from `LIVE_REQUIRED` to `LIVE_PASS` unless all of the following are true for the same workflow run and attempt:

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
- the pre-existing YooKassa probe was retrieved as `test=true` before the new create operation;
- provider create + exact idempotent retry + retrieve resolved to one test-shop payment that stayed unpaid/pending;
- any provider-side test payment id retained in logs belongs to the configured test shop;
- browser/application logs do not show hidden server failures despite a passing browser assertion.

The release evidence package must retain the workflow run URL/id, run attempt, commit SHA, execution time, aggregate manifest and any external sandbox evidence required by the acceptance record.

## Infrastructure-blocked state

GitHub Actions infrastructure issue **#116** currently governs runner execution evidence. A workflow run that terminates before runner allocation, has `runner_id=0`, or contains no executed steps is **BLOCKED_INFRA**. It is never `LIVE_PASS`, even if GitHub's high-level run object appears completed.

When runner execution resumes, run this workflow first. Only after it produces real step-level evidence and the same-run/same-attempt manifest should the wider manual M1/M2 persona, payment lifecycle, backup/restore and security drills in `docs/ACCEPTANCE_CURRENT.md` be promoted to live acceptance evidence.
