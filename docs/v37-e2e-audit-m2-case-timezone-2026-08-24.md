# v37 E2E audit continuation — multi-Case provenance, stale screens and business time

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

### 3. Client message entry/draft provenance

Risk: a fresh `message_create:v2:<case_id>` was not accepted by the historical exact-equality router, and an existing draft for Case A could be shown from a fresh Case B entry and then accidentally inherit B provenance. Explicit retarget-to-new also had to retire old Case provenance.

Source contract now:
- fresh message entry accepts raw or `message_create:v2:<case_id>` and resolves exact Case before opening the form;
- Case/route identity remains in FSM through category, urgency, free text and final confirmation;
- pressing a fresh Case B message button while a Case A draft exists never silently retargets the draft;
- the draft is preserved and offers a separate exact `message_retarget_current:v2:<case_id>` action;
- explicit retarget to a current Case updates both business draft identity and middleware provenance, then requires a second submit confirmation;
- explicit retarget to a new request clears previous Case provenance and still requires a second submit confirmation;
- message history and post-send “write again” actions keep exact Case identity and use business-time presentation;
- an old intermediate workflow callback with no recoverable Case provenance fails closed instead of attaching to whichever Case is currently selected.

### 4. M2 reschedule/cancel and follow-up

Source contract now:
- current action-center emits Case-bound reschedule/cancel controls;
- intermediate reschedule date/time keyboard is bound to Case + Consultation + Telegram message;
- final confirmation remains bound to exact consultation, old slot and new slot;
- if the new slot is lost in a race, a newly rendered retry calendar remains usable and receives fresh provenance;
- follow-up consultation actions are exact-Case bound;
- a selected active Case with no terminal consultation result no longer falls back to a result from another Case.

### 5. Active payment controls after a Case switch

Risk: `pay_open:<payment_id>` is payment-specific, but an old Case A payment button could still reconcile or create a provider link after the client selected Case B.

Source contract now:
- completed/archive payments remain read-only accessible;
- a live payment action must belong to the currently selected active Case before reconciliation, provider-link creation or fake local confirmation;
- historical raw `consult_pay` is Case-scoped before entering M2 payment reconciliation;
- hold-expiry and stale-link recovery show the Case number and use an exact Case-bound booking action;
- remaining historical raw recovery CTAs in legacy payment error branches are navigation-only under the current booking/message provenance guards: they must still be exercised in live multi-Case walkthroughs before release.

### 6. M1 stale contextual screens

Risk: an old read/action screen for Case A could be pressed after Case A closed or after the client selected Case B. Even if the final mutation was guarded, rendering a contract, POA or court screen in the wrong current context could mislead the client into the next action.

Source contract now:
- fresh My Case primary actions for `contract_open`, `poa_instruction` and `court_status` carry exact Case IDs;
- old bot screens that visibly name another canonical Case fail closed even when only one active Case exists now;
- contract entry resolves exact Case before reading the protected contract;
- retries and new-version links stay Case-bound;
- historical generic `contract_sign` cannot confirm a contract or create a payment because it identifies no exact document/version;
- only `contract_confirm:v2:<case>:<document>:<version>` can confirm the published version;
- POA instruction and court-status screens pass through an early exact-Case boundary before delegating to the existing M1 renderer;
- proof-bearing M1 facts remain staff-owned and are not inferred from client clicks.

### 7. Business timezone consistency

Risk: staff could see/create a different consultation time depending on the workstation/browser timezone.

Source contract now:
- Telegram M2 calendar date keys and visible times use the shared configured business timezone;
- Lawyer Consultation Desk computes `Today` in the business timezone and renders all client times through the server-provided zone/label;
- Admin Consultation Outcomes overrides browser-local rendering with the configured business timezone;
- Consultation Schedule explicitly shows the business timezone and posts `datetime-local` wall time as `starts_local` / `ends_local`;
- the server converts that wall time to UTC with `ZoneInfo`; ambiguous DST wall times fail closed instead of silently choosing an offset;
- Lawyer Workspace computes “today” in business time and its action note now uses the shared business datetime formatter rather than raw ISO;
- aware ISO input remains supported for programmatic/API clients.

### 8. Canonical staff visual hierarchy

The operational decision surfaces now converge on the same mental model rather than separate page-specific conventions:

`role / Case context -> NOW -> one main next step -> secondary navigation -> explicit recovery/confirmation`.

Source status:
- Workdesk keeps exact Case context and adds `Сейчас` + `Главный следующий шаг` without breaking its M2 action projection;
- Lawyer Consultation Desk already uses `now-box`, `next-box`, secondary actions and a collapsed exception block;
- Document Review already separates current status, main next step and review-confirm before any destructive decision;
- Payment Review now labels the safety reason as `Сейчас`, one main step, and separate secondary navigation while retaining its original financial semantics;
- Consultation Outcomes separates navigation links from mutation controls at the final product presentation boundary, labels `Сейчас` and `Главный следующий шаг`, and keeps all original confirmation forms;
- SLA uses the same wording and business timezone at its authenticated UI boundary;
- Message Center already exposes client context, `СЕЙЧАС`, `ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ`, draft-preserving conflict recovery and business-time message rendering;
- Document Access exposes exact Case and staff role before listing files, and every download remains grant-based, short-lived and one-time.

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
| MSG-01 | open fresh `message_create:v2:A`, switch to B during category/text/review | no message is written to B; draft remains associated with A | LIVE_REQUIRED |
| MSG-02 | Case A draft already exists → from fresh Case B screen press “write” | A draft is preserved; it is not re-labelled as B; explicit retarget is required | LIVE_REQUIRED |
| MSG-03 | explicit retarget A draft to B, then review and submit | first retarget does not send; second confirmation writes exactly one message to B | LIVE_REQUIRED |
| MSG-04 | explicit retarget old draft to new request while no active Case exists | previous Case provenance cleared; new Case is created only on final submit; no duplicate on retry | LIVE_REQUIRED |
| M1-STALE-01 | old Case A contract screen → A closes/B selected → press old contract/open/sign | B contract/payment untouched; generic sign cannot confirm; exact new version required | LIVE_REQUIRED |
| M1-STALE-02 | old Case A POA/court screen → B selected → press old action | B unchanged; old screen fails closed or routes to explicit Case recovery | LIVE_REQUIRED |
| M1-STALE-03 | fresh Case-bound contract retry after temporary file-read error | reopens same Case and current published version only | LIVE_REQUIRED |
| TIME-01 | staff browser timezone differs from `BUSINESS_TIMEZONE` | Telegram, schedule, lawyer desk, Workdesk, outcomes, payment/document/SLA surfaces show the same business time | LIVE_REQUIRED |
| TIME-02 | create slot from browser in another timezone using `datetime-local` | persisted UTC corresponds to entered business wall time, not browser timezone | LIVE_REQUIRED |
| TIME-03 | DST-capable configured zone, ambiguous wall time | creation fails closed with human-readable validation | LIVE_REQUIRED |
| STAFF-UI-01 | Admin: Workdesk → Payment Review → Refund → Outcomes → Document Review | each screen preserves role/Case context, one main step, secondary navigation and safe confirmation/recovery | LIVE_REQUIRED |
| STAFF-UI-02 | Lawyer: Workspace → Consultation Desk → Message Center → Document Access | exact assigned/consultation Case remains visible; business time identical; no admin-only mutation exposed | LIVE_REQUIRED |
| STAFF-UI-03 | stale browser tab after another staff actor updates same document/dialog | action fails with conflict/snapshot recovery; draft/comment is not silently applied to stale state | LIVE_REQUIRED |
| DOC-ACCESS-01 | lawyer/admin opens exact Case materials, downloads one file, retries same grant | Case/role access rechecked; first grant is one-time; reused grant fails safely; new grant required | LIVE_REQUIRED |

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
- `tests/test_v37_client_message_provenance.py`;
- `tests/test_v37_m1_contextual_stale_screens.py`;
- `tests/test_v37_lawyer_consultation_business_timezone.py`;
- `tests/test_v37_consultation_schedule_business_timezone.py`;
- `tests/test_v37_consultation_outcomes_business_timezone.py`;
- `tests/test_v37_lawyer_workspace_business_timezone.py`;
- `tests/test_v37_staff_visual_hierarchy.py`.

These files are regression intent/evidence in source. They are not claimed as executed until runner allocation is restored.
