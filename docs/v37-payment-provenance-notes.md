# Payment provenance invariant

Client stage-payment buttons are historical Telegram UI, not proof of the case or payment they were rendered for. The safe payment guard therefore treats raw `pay_start_30000`, `pay_court_70000` and `pay_success_fee` callbacks as navigation/confirmation only. A mutating payment-link action must be bound to the exact `case_id` and exact existing `payment_code`, re-check ownership/current status under a row lock, and must never create a legal stage or mark a payment paid.

The existing payment obligation remains the source of truth. Missing obligations are process-integrity errors and are not recreated from a client Telegram callback.
