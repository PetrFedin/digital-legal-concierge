# LIVE_REQUIRED release runbook

Status: **SOURCE_OK / BLOCKED_INFRA until a hosted runner actually allocates and executes every job**.

This document defines the single fail-closed runtime gate for the current M1/M2 release. It supplements `docs/ACCEPTANCE_CURRENT.md`; it does not convert any acceptance item to `LIVE_PASS` by itself.

## Gate

Workflow: `.github/workflows/live-required.yml`

Trigger: manual `workflow_dispatch` only. The workflow is intentionally not a PR-time external side effect.

The aggregate `live-required` job is successful only when every required component reports `success`:

1. **PostgreSQL** — production migration chain plus multi-Case, payment/refund and staff concurrency contracts on PostgreSQL 16.
2. **Redis** — Redis FSM restart/loss recovery with PostgreSQL as persistent Case source of truth.
3. **Telegram** — a real Bot API identity call plus real silent message delivery to two distinct dedicated acceptance chats (admin and client), followed by cleanup. A mocked aiogram transport is not accepted as this component's evidence.
4. **Browser** — Playwright staff E2E against the application and PostgreSQL.
5. **Provider sandbox** — the production `YooKassaPaymentProvider` creates one small unconfirmed payment with test-shop credentials, repeats the exact create command to prove provider idempotency, retrieves the resulting provider truth, and requires `test=true`, `paid=false`, `status=pending` throughout. The confirmation URL is validated but never opened by the smoke.

A failed, cancelled or skipped component fails the aggregate gate. Missing external secrets fail their job explicitly. There is no `continue-on-error` escape hatch.

## Required GitHub Actions secrets

Configure these before dispatching the workflow:

- `LIVE_TELEGRAM_BOT_TOKEN` — token of the dedicated non-production Telegram bot used for acceptance;
- `LIVE_TELEGRAM_ADMIN_CHAT_ID` — dedicated admin acceptance chat already reachable by that bot;
- `LIVE_TELEGRAM_CLIENT_CHAT_ID` — a distinct dedicated client acceptance chat already reachable by that bot;
- `LIVE_YOOKASSA_SHOP_ID` — YooKassa **test-shop** identifier;
- `LIVE_YOOKASSA_SECRET_KEY` — matching YooKassa **test-shop** secret;
- `LIVE_PUBLIC_BASE_URL` — externally valid HTTPS staging/test base URL used by the provider redirect contract.

The workflow deliberately maps the YooKassa secrets to the application's existing `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` settings rather than introducing a second provider implementation.

## Provider safety contract

`scripts/live_required_smoke.py provider`:

- uses the existing `YooKassaPaymentProvider` code path;
- limits the smoke amount to 1..10,000 minor currency units; the workflow currently uses 100 minor units (`1.00 RUB`);
- creates a unique internal smoke identifier so the provider idempotence key cannot collide with an application Payment;
- calls the same create operation twice with the same internal payment id and requires the same provider payment id on retry;
- retrieves that payment from YooKassa after the retry and requires the retrieved id to match;
- requires create, retry and retrieve evidence to remain test-mode, unpaid and pending;
- never follows the returned confirmation URL and therefore never completes the user confirmation step;
- refuses to count the result as sandbox evidence unless the YooKassa response is marked `test=true`;
- does not print credentials or the confirmation URL.

A production-shop response, non-idempotent retry or unexpectedly paid/non-pending smoke payment is a hard failure, not a degraded pass.

## Telegram safety contract

`scripts/live_required_smoke.py telegram`:

- calls `getMe` against the real Telegram Bot API;
- requires two distinct configured acceptance chats rather than silently collapsing admin and client evidence into one recipient;
- sends one silent smoke message to each chat and verifies Telegram returned the exact target chat and a message id;
- deletes both smoke messages after delivery and fails if cleanup cannot be confirmed;
- prints only the bot id and delivery count, never the bot token or chat ids.

This proves external Bot API reachability and delivery to both acceptance roles. It complements, but does not replace, the dispatcher/Redis business-flow acceptance paths in the test suite or the later full M1/M2 persona walkthrough.

## Evidence required from a successful run

Do not change acceptance state from `LIVE_REQUIRED` to `LIVE_PASS` unless all of the following are true for the same workflow run:

- each of the five component jobs has a real runner allocation and executed steps;
- each component conclusion is `success`;
- the aggregate `LIVE_REQUIRED aggregate gate` job executed and concluded `success`;
- no component was skipped because of missing configuration;
- the commit SHA under test is the exact release candidate SHA;
- Telegram delivery succeeded to both distinct acceptance chats and cleanup succeeded;
- provider create + exact idempotent retry + retrieve resolved to one test-shop payment that stayed unpaid/pending;
- any provider-side test payment id retained in logs belongs to the configured test shop;
- browser/application logs do not show hidden server failures despite a passing browser assertion.

Record the workflow run URL/id, commit SHA, execution time and any external sandbox evidence in the release evidence package.

## Infrastructure-blocked state

GitHub Actions infrastructure issue **#116** currently governs runner execution evidence. A workflow run that terminates before runner allocation, has `runner_id=0`, or contains no executed steps is **BLOCKED_INFRA**. It is never `LIVE_PASS`, even if GitHub's high-level run object appears completed.

When runner execution resumes, run this workflow first. Only after it produces real step-level evidence should the wider manual M1/M2 persona, payment lifecycle, backup/restore and security drills in `docs/ACCEPTANCE_CURRENT.md` be promoted to live acceptance evidence.
