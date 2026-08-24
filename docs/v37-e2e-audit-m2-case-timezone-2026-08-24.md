# v37 E2E audit continuation — M2 Case scope, payment recovery and business time

Date: 2026-08-24  
Scope: existing M1/M2 product only. No new legal route or client web cabinet.

## Evidence status

All changes in this checkpoint are **SOURCE_OK only** until the corresponding GitHub Actions, PostgreSQL/Redis suites and staging persona walkthrough actually execute. GitHub Actions runner allocation is still blocked by issue #116; a workflow that ends before runner steps is **BLOCKED_INFRA**, not PASS and not application FAIL.

## Findings closed in source

### 1. M2 booking entry after a Case switch

Risk: historical raw `consult_booking_start` / `consult_slot_open` buttons could be pressed after the client switched from Case A to Case B. The initial calendar needed the same exact-Case protection already used for slot selection/payment.

Source contract now:
- fresh booking entry can use `:v2:<case_id>`;
- raw legacy entry is accepted with several active Cases only when the trusted bot message visibly identifies the selected Case;
- the calendar snapshot keeps Case, Consultation and Telegram message provenance;
- date/time callbacks cannot mutate a different Case after a switch;
- a slot race may render a fresh calendar without losing its new provenance.

### 2. M2 question/description workflow

Risk: a stale Case A `consult_subject_start` screen or intermediate draft callback could otherwise be reinterpreted against selected Case B. The previous text-provenance layer also released Case provenance too early after the first free-text message.

Source contract now:
- question entry is Case-scoped before the form opens;
- every intermediate callback (`subject`, `review`, `edit`, `confirm`, discard) re-validates exact Case + Consultation provenance;
- free text remains bound while the workflow moves from input to review and is cleared only when the full form ends;
- a draft from one Case is not carried into another Case entry;
- confirmation verifies that the domain service returned the same Case and Consultation before commit;
- committed `Continue without documents` uses `doc_skip_m2:v2:<case_id>` instead of a historical unbound mutation;
- the client sees the current Case number throughout the three-step question flow.

### 3. M2 reschedule/cancel and follow-up

Source contract now:
- current action-center emits Case-bound reschedule/cancel controls;
- intermediate reschedule date/time keyboard is bound to Case + Consultation + Telegram message;
- final confirmation remains bound to exact consultation, old slot and new slot;
- if the new slot is lost in a race, a newly rendered retry calendar remains usable and receives fresh provenance;
- follow-up consultation actions are exact-Case bound;
- a selected active Case with no terminal consultation result no longer falls back to a result from another Case.

### 4. Active payment controls after a Case switch

Risk: `pay_open:<payment_id>` is payment-specific, but an old Case A payment button could still reconcile or create a provider link after the client selected Case B.

Source contract now:
- completed/archive payments remain read-only accessible;
- a live payment action must belong to the currently selected active Case before reconciliation, provider-link creation or fake local confirmation;
- historical raw `consult_pay` is Case-scoped before entering M2 payment reconciliation;
- hold-expiry and stale-link recovery show the Case number and use an exact Case-bound booking action.

### 5. Business timezone consistency

Risk: staff could see/create a different consultation time depending on the workstation/browser timezone.

Source contract now:
- Telegram M2 calendar date keys and visible times use the shared configured business timezone;
- Lawyer Consultation Desk computes `Today` in the business timezone and renders all client times through the server-provided zone/label;
- Admin Consultation Outcomes overrides browser-local rendering with the configured business timezone;
- Consultation Schedule explicitly shows the business timezone and posts `datetime-local` wall time as `starts_local` / `ends_local`;
- the server converts that wall time to UTC with `ZoneInfo`; ambiguous DST wall times fail closed instead of silently choosing an offset;
- aware ISO input remains supported for programmatic/API clients.

## Mandatory live/staging scenarios added by this audit

| ID | Scenario | Expected result | State |
| --- | --- | --- | --- |
| M2-CX-01 | Case A booking screen → switch to B → press old A booking entry | no calendar/slot mutation for B; explicit recovery/Case selection | LIVE_REQUIRED |
| M2-CX-02 | Case A question draft → switch to B → press old review/confirm | draft is not written to B; stale flow closes safely | LIVE_REQUIRED |
| M2-CX-03 | Case A question text captured → review → restart/FSM persistence → confirm | exact A Case/Consultation preserved through review | LIVE_REQUIRED |
| M2-CX-04 | Case A reschedule calendar → switch to B → press old date/slot | B unchanged; old calendar rejected | LIVE_REQUIRED |
| M2-CX-05 | reschedule final confirm loses race for requested slot | existing booking unchanged; fresh retry calendar remains usable | LIVE_REQUIRED |
| M2-CX-06 | Case A `pay_open` → switch to B → press old A button | no reconciliation/provider redirect for A; B untouched | LIVE_REQUIRED |
| M2-CX-07 | selected Case B has no result while Case A has completed M2 result | result screen stays in B context; A result appears only after explicit Case/archive navigation | LIVE_REQUIRED |
| TIME-01 | staff browser timezone differs from `BUSINESS_TIMEZONE` | Telegram, schedule, lawyer desk and admin outcome desk show same business time | LIVE_REQUIRED |
| TIME-02 | create slot from browser in another timezone using `datetime-local` | persisted UTC corresponds to entered business wall time, not browser timezone | LIVE_REQUIRED |
| TIME-03 | DST-capable configured zone, ambiguous wall time | creation fails closed with human-readable validation | LIVE_REQUIRED |

## Regression source added/updated

Key regression surfaces in this checkpoint include:
- `tests/test_v37_m2_booking_case_and_timezone.py`;
- `tests/test_v37_consultation_booking_provenance.py`;
- `tests/test_v37_consultation_change_provenance.py`;
- `tests/test_v37_consultation_description_provenance.py`;
- `tests/test_v37_consultation_description_case_flow.py`;
- `tests/test_v37_consultation_description_foreign_draft.py`;
- `tests/test_v37_consultation_result_case_scope.py`;
- `tests/test_v37_payment_active_case_scope.py`;
- `tests/test_v37_my_case_m2_action_binding.py`;
- `tests/test_v37_lawyer_consultation_business_timezone.py`;
- `tests/test_v37_consultation_schedule_business_timezone.py`;
- `tests/test_v37_consultation_outcomes_business_timezone.py`.

These files are regression intent/evidence in source. They are not claimed as executed until runner allocation is restored.
