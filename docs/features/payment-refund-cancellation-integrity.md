# Payment, refund and paid cancellation integrity

## Scope

This document records the implemented invariants for consultation payments,
external refunds and client cancellation requests.

## Payment creation

For one `(case_id, payment_code)` the database permits:

- at most one open payment in `PENDING` or `WAITING_CONFIRMATION`;
- at most one confirmed payment in `PAID`;
- any number of terminal historical rows such as `FAILED`, `CANCELLED`,
  `EXPIRED` or `REFUNDED`.

`PaymentService.get_or_create_payment()` locks the Case before reading stage
payments. It reuses one open payment, stops on legacy duplicates and refuses to
create a new link when the stage is already paid.

## Successful provider callbacks

`PaymentService.mark_paid()` checks for another paid payment before changing
status and relies on the partial unique index as the final cross-process guard.
A duplicate successful payment is a financial conflict, not a second success.

The webhook layer records that condition as:

- `processing_outcome = CONFLICT`;
- `manual_review_required = true`;
- one `PAYMENT_WEBHOOK_REQUIRES_MANUAL_REVIEW` audit event.

Provider retries of the same conflict are idempotent.

## External refund confirmation

The project does not claim an automatic provider refund API. An administrator
records a completed external refund through `PaymentRefundService`.

Required conditions:

- Payment belongs to the Case;
- Payment status is `PAID`;
- refund reference is present;
- employee comment is present;
- refund amount exactly equals the paid amount.

Partial refunds are blocked until a separate financial model is implemented.
The successful operation changes `PAID -> REFUNDED`, removes the obsolete
payment URL and writes one `PAYMENT_REFUND_CONFIRMED_EXTERNALLY` audit event.
Repeating the same reference is idempotent; another reference is a conflict.

## Client cancellation request

A client cannot directly release a paid booked slot. Telegram records
`CONSULTATION_CANCELLATION_REQUESTED`, preserves the booking and payment, and
changes only the Case next action to manager review.

The client-facing flow never promises an automatic refund.

## Administrative resolution

`ConsultationCancellationResolutionService` supports two decisions:

### KEEP_BOOKING

- consultation remains booked;
- slot remains booked;
- payment remains paid;
- Case returns to the normal waiting action.

### REFUND_CONFIRMED

This decision is accepted only when the related consultation Payment is already
`REFUNDED`.

Then the service:

- releases the slot;
- clears the Consultation slot link;
- changes Consultation to `CANCELLED`;
- closes the M2 Case;
- writes `CONSULTATION_CANCELLATION_REQUEST_RESOLVED`.

Without confirmed refund status the transaction is rejected and the old booking
remains unchanged.

## Slot protection during payment

An expired consultation hold is not automatically released while the Case has
an open or paid consultation Payment. Scheduler cleanup extends the technical
hold protection and leaves the selected time reserved for payment reconciliation.

Terminal failed, cancelled or expired payment states permit the next cleanup
cycle to release the hold and return the client to slot selection.

## Database migration

Run the project migration/bootstrap command:

```bash
python scripts/init_db.py
```

The script performs preflight checks before installing partial unique indexes.
It stops with a concrete Case or Payment reference when legacy duplicates are
found. It never silently deletes or merges financial records.

Important indexes:

- `uq_cases_active_m2_client`;
- `uq_consultations_active_case`;
- `uq_consultations_slot_id`;
- `uq_consultation_slots_consultation_id`;
- `uq_consultation_slots_active_lawyer_start`;
- `uq_payments_open_case_code`;
- `uq_payments_paid_case_code`;
- `uq_payments_provider_payment_id`.

For PostgreSQL the migration also installs `btree_gist` and exclusion constraints
for overlapping active lawyer and client slot intervals.

## Operational rule

Do not resolve a paid cancellation by directly editing Consultation, Slot or
Case statuses. The supported order is:

1. record the client request;
2. investigate payment and cancellation terms;
3. confirm the external full refund when applicable;
4. resolve the cancellation request;
5. verify the resulting audit chain.

## Verification commands

```bash
python -m compileall -q app scripts tests
ruff check app scripts tests
pytest -q
```

A completed CI run remains required before merge or deployment.
