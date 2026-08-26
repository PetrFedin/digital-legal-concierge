# LIVE_REQUIRED release runbook

Status: **SOURCE_OK / BLOCKED_INFRA until a hosted runner actually allocates and executes every job**.

This document defines the single fail-closed runtime gate for the current M1/M2 release. It supplements `docs/ACCEPTANCE_CURRENT.md`; it does not convert any acceptance item to `LIVE_PASS` by itself.

## Gate

Workflow: `.github/workflows/live-required.yml`

Trigger: manual `workflow_dispatch` only. The workflow is intentionally not a PR-time external side effect.

The aggregate `live-required` job is successful only when every required component reports `success`:

1. **PostgreSQL** — production migration chain plus multi-Case, payment/refund and staff concurrency contracts on PostgreSQL 16.
2. **Redis** — Redis FSM restart/loss recovery with PostgreSQL as persistent Case source of truth.
3. **Telegram** — a real Bot API `getMe` call using the staging/test bot token; a mocked aiogram transport is not accepted as this component's evidence.
4. **Browser** — Playwright staff E2E against the application and PostgreSQL.
5. **Provider sandbox** — the production `YooKassaPaymentProvider` creates one small unconfirmed payment with test-shop credentials and the returned provider object must explicitly contain `test=true`. The confirmation URL is validated but never opened by the smoke.

A failed, cancelled or skipped component fails the aggregate gate. Missing external secrets fail their job explicitly. There is no `continue-on-error` escape hatch.

## Required GitHub Actions secrets

Configure these before dispatching the workflow:

- `LIVE_TELEGRAM_BOT_TOKEN` — token of the dedicated non-production Telegram bot used for acceptance;
- `LIVE_YOOKASSA_SHOP_ID` — YooKassa **test-shop** identifier;
- `LIVE_YOOKASSA_SECRET_KEY` — matching YooKassa **test-shop** secret;
- `LIVE_PUBLIC_BASE_URL` — externally valid HTTPS staging/test base URL used by the provider redirect contract.

The workflow deliberately maps the YooKassa secrets to the application's existing `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` settings rather than introducing a second provider implementation.

## Provider safety contract

`scripts/live_required_smoke.py provider`:

- uses the existing `YooKassaPaymentProvider` code path;
- limits the smoke amount to 1..10,000 minor currency units; the workflow currently uses 100 minor units (`1.00 RUB`);
- creates a unique internal smoke identifier so the provider idempotence key cannot collide with an application Payment;
- never follows the returned confirmation URL and therefore never completes the user confirmation step;
- refuses to count the result as sandbox evidence unless the YooKassa response is marked `test=true`;
- does not print credentials or the confirmation URL.

A production-shop response is a hard failure, not a degraded pass.

## Telegram safety contract

`scripts/live_required_smoke.py telegram` performs only the read-only Bot API identity request `getMe`. It confirms that the configured token reaches Telegram and resolves to a bot identity; it does not send a message to a real client.

This external smoke complements, but does not replace, the dispatcher/Redis acceptance paths in the test suite.

## Evidence required from a successful run

Do not change acceptance state from `LIVE_REQUIRED` to `LIVE_PASS` unless all of the following are true for the same workflow run:

- each of the five component jobs has a real runner allocation and executed steps;
- each component conclusion is `success`;
- the aggregate `LIVE_REQUIRED aggregate gate` job executed and concluded `success`;
- no component was skipped because of missing configuration;
- the commit SHA under test is the exact release candidate SHA;
- any provider-side test payment id retained in logs belongs to the configured test shop;
- browser/application logs do not show hidden server failures despite a passing browser assertion.

Record the workflow run URL/id, commit SHA, execution time and any external sandbox evidence in the release evidence package.

## Infrastructure-blocked state

GitHub Actions infrastructure issue **#116** currently governs runner execution evidence. A workflow run that terminates before runner allocation, has `runner_id=0`, or contains no executed steps is **BLOCKED_INFRA**. It is never `LIVE_PASS`, even if GitHub's high-level run object appears completed.

When runner execution resumes, run this workflow first. Only after it produces real step-level evidence should the wider manual M1/M2 persona, payment lifecycle, backup/restore and security drills in `docs/ACCEPTANCE_CURRENT.md` be promoted to live acceptance evidence.
