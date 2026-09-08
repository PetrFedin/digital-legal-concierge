# v37 E2E audit checkpoint — payments, slots, async rollback — 2026-08-19

This checkpoint continues the client/admin/lawyer source-level audit on `feat/v37-guided-case-dashboard-telegram`. It records concurrency and recovery defects found after the main 2026-08-19 continuation checkpoint. It is not a claim of successful live Telegram/provider/staging execution.

The product invariants remain: one active client route (M1 or M2), one clear next action, no duplicate financial obligation from retries, and no confirmed consultation reopened by stale cleanup.

## 1. Message-history callback rollback stability

### Finding

The historical callback history flow released the SQLAlchemy read transaction with `db.rollback()` before Telegram network I/O and later reused `case.id`. Rollback can expire ORM instances; touching an expired ORM attribute from an async handler can trigger an unexpected lazy load / async ORM failure while the client is merely opening or paging message history.

### Fix

`app/bot/screens/message_history_guard.py` is now the effective callback presenter for both:

- `message_history`
- `message_history:<page>`

It captures scalar `case_id` before rollback, renders the canonical history/pagination/read-only archive, and marks only team messages actually displayed on the sent page. After rollback it never reads the Case ORM instance.

`navigation_history_guard.py` owns both the first history page and pagination before the legacy messages router. The direct persistent `💬 Переписка` handler follows the same scalar-before-rollback rule.

Source regression: `tests/test_v37_message_history_rollback_guard.py`.

## 2. Payment creation race

### Finding

`PaymentService.get_or_create_payment()` historically used a normal `find active payment -> create` sequence. Two simultaneous Telegram retries/clicks could both observe no active payment and create two internal attempts for the same case/payment stage.

### Fix — application serialization

`PaymentService.get_or_create_payment()` now locks the stable Case row with `SELECT ... FOR UPDATE`, then re-reads the active payment and inserts only when the re-check is empty.

For M2 slot changes, stale active links are marked `EXPIRED` and flushed before a replacement attempt is inserted.

### Fix — database backstop

Migration `20260819_0016_payment_idempotency_invariants.py` adds:

- `uq_payments_one_active_attempt_per_case_code` — partial unique index on `(case_id, payment_code)` for `PENDING` / `WAITING_CONFIRMATION` only;
- `uq_payments_provider_operation` — partial unique index on `(provider, provider_payment_id)` whenever provider identity exists.

Migration preflight stops deployment when historical duplicates exist. It does not automatically expire, cancel, refund, merge or choose financial records.

Source regression: `tests/test_v37_payment_concurrency_invariant.py`.

## 3. Provider-side idempotency

### Finding

YooKassa payment creation previously generated a random `Idempotence-Key` per HTTP call. Two concurrent calls for the same internal Payment could therefore become two provider operations even if application state later reconciled to one row.

### Fix

`payment_idempotence_key(payment_id)` is deterministic: SHA-256 over application environment + internal Payment id. The same internal payment therefore retries the same provider operation; a later intentionally new Payment id receives a new key.

The fake provider mirrors this deterministic identity so local/test behaviour models the production contract.

YooKassa create responses are also fail-closed: missing provider id or missing confirmation URL raises before the internal payment can move to `WAITING_CONFIRMATION`. A retry uses the same idempotency key and can recover the provider operation instead of creating another charge.

## 4. Duplicate webhook re-check

`payment_webhook_service.py` already locks the Payment row with `FOR UPDATE` before applying success/failure state. A duplicate successful event that arrives after the first commit sees `PAID` and returns without applying the legal transition twice. No additional duplicate-webhook patch was required in this pass.

## 5. Received money must block a second M2 charge

### Finding

A particularly dangerous state exists when money has already arrived but is not yet resolved automatically:

- `PAID_REVIEW`
- `REFUND_PENDING`
- `REFUND_DECLINED`

Without an explicit received-money guard, a client could choose another slot and ask the bot for another M2 payment attempt while the first money was still being reconciled/refunded.

### Domain fix

`PaymentService._received_payment_conflict()` blocks a new automatic charge while received money is unresolved.

For one-off M1 stage codes, protected received states block a new attempt for the same code. For M2, unresolved review/refund states block every new automatic consultation charge until the team resolves the money. A normal historical `PAID` M2 consultation can coexist with a later follow-up only when the reservation context is clearly different.

### Client UX fix

`payment_received_money_guard.py` is mounted before `payment_archive_guard` and `payments.router`.

When unresolved received M2 money exists:

- `payments_open` shows the canonical payment history without materialising another obligation;
- `consult_pay` says explicitly that repeat payment is not needed, a second link will not be created, and changing slot only for another charge is not required;
- any historical `pay_open:<id>` for an active M2 `PENDING/WAITING_CONFIRMATION` link is suppressed, so an old provider link cannot bypass the received-money guard.

Client recovery actions are payments history, message to team, My Case and Home. The flow does not loop the client back to slot selection.

Source regression: `tests/test_v37_received_money_client_guard.py`.

## 6. Slot hold expiry versus successful booking race

### Finding — P0

Slot acquisition itself was already atomic: hold/book operations update only `status='available'`, and confirmation updates only the exact `held` slot for the exact consultation.

However `release_expired_holds()` had a stale-snapshot race:

1. cleanup queried expired `held` slots without locking them;
2. a payment webhook/no-payment confirmation could book one of those slots;
3. cleanup later updated slot rows by captured id only, without re-checking `status='held'` and expiry;
4. the stale cleanup could theoretically convert the newly `booked` slot back to `available`.

That could lead to a confirmed consultation and an apparently free slot at the same time.

### Fix

`SlotService.release_expired_holds()` now treats the first query only as a candidate scan.

It then:

1. follows the payment-webhook lock order by locking matching active Payment rows first;
2. re-queries candidate ConsultationSlot rows with `FOR UPDATE`;
3. requires them to still be `held`, to still have an expiry, and to still be expired;
4. mutates only the revalidated `actual_slot_ids`;
5. repeats the `held + expired` predicate on the final slot UPDATE even though rows are locked;
6. returns/events only for slots that survived the revalidation.

If provider/no-payment booking wins between candidate scan and cleanup, the locked re-read sees `booked` and cleanup performs no slot/consultation reopen.

Source regression: `tests/test_v37_slot_expiry_race_guard.py`.

## 7. Related database invariants from the same audit chain

The migration chain for the new concurrency barriers is now:

- `20260819_0014` — one non-terminal Case per client;
- `20260819_0015` — one live Consultation context per Case;
- `20260819_0016` — one active Payment attempt per case/code + unique provider operation identity.

All three migrations preflight historical contradictions and fail closed. None auto-closes legal matters, cancels consultations or rewrites financial history.

## 8. Live verification still mandatory

Before production acceptance:

- run migrations 0014–0016 on a staging copy and deliberately resolve every preflight conflict;
- execute real GitHub Actions after account billing/spending runner allocation is restored (issue #116);
- run concurrent Telegram tests: double tap, retry after network timeout, process restart and stale callback;
- run real provider tests: same-idempotency-key retry, duplicate/late webhook, refund/review/reconciliation;
- exercise the exact expiry-vs-payment timing race with PostgreSQL transactions;
- browser-walk admin payment review/refund queues and client My Case/payment history after each exceptional state.

## Release rule

Source-level guards and regression assertions materially close the identified races, but v37 is not production-accepted until CI actually executes and the staging persona/concurrency matrix confirms the resulting DB state, audit history and visible next action together.
