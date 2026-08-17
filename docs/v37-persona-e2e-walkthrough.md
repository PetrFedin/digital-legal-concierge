# v37 persona E2E walkthrough

This walkthrough records a source-level pass through the product as a client, administrator and lawyer. It is based on the current `feat/v37-guided-case-dashboard-telegram` branch and the approved MVP/Telegram UX specifications. It is deliberately not a claim that staging, Telegram API, payment-provider or full pytest execution has already succeeded.

## Audit method

For every persona the check is: entry -> current state -> visible primary action -> mutation boundary -> resulting state -> recovery from stale/repeated/failed actions -> archive/exit. The MVP invariant remains two client routes only: M1 (standard recovery) and M2 (consultation).

Severity:

- **P0**: normal production path can stop, mutate the wrong object, expose the wrong role, or lose the only safe continuation.
- **P1**: no hard corruption, but the user can be routed to a misleading/empty surface or needs unnecessary manual recovery.
- **P2**: usability/clarity debt that should be cleaned after P0/P1.

## Client — common entry and recovery

| Situation | Current behaviour reviewed | Result |
| --- | --- | --- |
| First `/start` or `/menu` | Home offers calculation or legal help, with preliminary-calculation wording | OK |
| Unknown text outside FSM | Fallback rebuilds the current home/menu instead of silently ignoring input | OK |
| Unknown/stale callback outside FSM | Fallback shows current state and acknowledges Telegram callback | OK |
| Unknown callback during active FSM | Current input is not cleared; client is told to finish or cancel | OK |
| Repeated rapid taps | Flood control returns a retry delay | OK |
| Telegram callback spinner | Callback acknowledgement middleware closes the spinner even after handler processing | OK |
| Active case + attempt to start another calculation | Second calculation is blocked so cases/documents cannot mix | OK |
| Home/cancel while calculator has partial data | Calculator draft middleware pauses and restores entered values | OK |
| Unsent message draft + navigation | Navigation is blocked until the client explicitly saves/discards the draft | OK |
| Generic `Назад` | A bounded FSM navigation history now replays only explicitly allowlisted read-only screens. Payment creation, slot reservation, document mutation and legal-stage callbacks are never replayed. If history is unavailable, recovery goes to `Моё дело`, then Home | **P2 gap fixed at source level**; live Telegram verification remains |
| Contract screen `Назад` | Contract presentation now exposes the global safe Back action without changing the version-confirmation/payment transition | OK |

## Client — M1 standard recovery

| Stage / exception | Current boundary reviewed | Result |
| --- | --- | --- |
| Calculation choice | M1/M2/postpone choices are case-bound; stale choices cannot select a newer case | OK |
| Consent | Consent is a separate decision and bound to the owned current case | OK |
| Document upload | Upload FSM carries exact case binding; replacement upload has separate protection | OK |
| Client finishes documents | Stops at documents received; wording does not falsely claim lawyer review started | OK |
| Staff review | First valid staff decision establishes the lawyer-review transition | OK |
| Lawyer rejects M1 | Client gets controlled follow-up: M2 or close; stale rejection actions are guarded | OK |
| Contract | Client confirmation is tied to the exact current contract/version before the 30,000 RUB stage | OK |
| First payment | Archived/wrong-stage payment actions are navigation/recovery only; client CTA names the 30,000 RUB amount | OK |
| POA | Client reports readiness only; factual receipt remains a staff action | OK |
| Claim / 30-day wait | Legal facts are lawyer-owned; court action is separated from client UI | OK |
| Court / 70,000 RUB | Court payment requires court-decision evidence before the obligation opens; client CTA names the 70,000 RUB amount | OK |
| Enforcement / recovered money | Recovered amount is a lawyer-owned fact; success-fee obligation is server-created | OK |
| Success fee | Client cannot manufacture the success-fee stage with a stale callback | OK |
| Closure/archive | Completed case is read-only; documents/payments/history remain available | OK |
| Persisted paid transient status | Workdesk integrity exposes proof-bound recovery rather than generic status editing | OK |

## Client — M2 consultation

| Stage / exception | Current boundary reviewed | Result |
| --- | --- | --- |
| New legal-help entry without active case | Creates/continues the consultation intake only through the canonical route | OK |
| Generic old `contact_lawyer` / `consult_start` on active M2 | Consultation navigation guard resumes the exact current M2 stage instead of creating a second consultation | OK |
| Active M1 + consultation shortcut | Route isolation prevents a parallel M2 case beside active M1 | OK |
| Description | Description provenance keeps input tied to the current consultation/case | OK |
| Optional documents | Documents do not block slot selection | OK |
| Slot selection | Booking provenance binds the selected live slot and retries safely | OK |
| Slot reserved but not paid | Hold/payment lifecycle remains reservation-specific | OK |
| Expired hold | Reservation/payment cleanup is scoped to the exact consultation/slot | OK |
| Late payment for stale reservation | Enters payment review instead of auto-booking another slot | OK by implemented guard/reconciliation path |
| Booked consultation | Generic help entry opens the current booking, not a duplicate | OK |
| Reschedule/cancel | Confirmation callbacks carry consultation + old/new slot snapshot IDs | OK |
| Client no-show | Requires an explicit admin resolution to new paid booking or close | OK |
| Lawyer no-show | Requires admin free rebooking or refund handling | OK |
| Lawyer outcome | Deterministic follow-up is close / to M1 / follow-up | OK |
| Closed M2 | Read-only archive and consultation result remain client-visible | OK |

## Administrator walkthrough

| Situation | Current behaviour reviewed | Result |
| --- | --- | --- |
| Login | Personal account session; canonical landing is `/admin-ui` -> Workdesk | OK |
| Workdesk home | Priority-first view, queues, search, case context, one primary action | OK |
| Direct Workdesk link `?case_id=` | Previously landed on overview and ignored the requested case. Early Workdesk guard now opens the exact card, keeps URL and selected card synchronized, and removes `case_id` when the card is closed | **P1 found and fixed** |
| Search by client name / Telegram / phone | Previously stopped at a client row. Search now pulls related cases and provides a one-click Workdesk handoff to the latest case | **P1 found and fixed** |
| Unassigned queue | Dashboard count already used assignment policy, but the generic queue listed every unassigned active case, including client-owned pre-document states and M2. Early exact queue now contains only statuses where M1 assignment is actually required | **P1 found and fixed** |
| M2 responsibility in Workdesk | Legacy case workspace/consultation queue derived responsibility from `Case.assigned_lawyer_id`, which is the wrong model for M2 and could show `не назначен`/suggest assignment. Workdesk projections now use effective consultation-slot responsibility and suppress the M1 SLA assignment shortcut for M2 | **P1 found and fixed** |
| Unreachable historical assignee | Integrity surface provides guarded repair instead of manual legal-status mutation | OK |
| Document queue | Canonical document review is role-protected before staff HTML is served | OK |
| Payment received but ambiguous | Payment review is a separate controlled queue | OK |
| M2 stale reservation payment | Dedicated reservation-reconciliation path exists; old provider link is not treated as current booking | OK |
| Refund pending/declined | Controlled refund workflow; declined refund can be explicitly returned to processing | OK |
| SLA overdue without lawyer | Primary action satisfies assignment prerequisite instead of opening an empty SLA action | OK |
| M2 no-show/legacy outcome | Consultation-outcome control routes to an actionable admin surface; wrong/incomplete staff role now returns to canonical staff recovery instead of raw 403/409 | OK |
| Settings / diagnostic UI | Admin-only shells now recover expired sessions to login and role/configuration mismatch to canonical staff landing | OK |
| Staff bookmarks | Workdesk, lawyer shells, shared schedule, payment review, SLA, document review and message center are protected before or while serving staff HTML and recover through login/canonical staff landing | OK |
| Manual full scheduler | Restricted to personal MFA superadmin and audited | OK |
| Legacy technical/admin pages | Canonical redirects reduce parallel/demo control surfaces | OK |
| Process contradiction | `/admin/workdesk/integrity` surfaces critical/warning contradictions and safe deep links | OK |
| Staff account has only a non-product role or incomplete lawyer link | Canonical landing shows an explicit access-setup screen with safe logout instead of raw JSON 403/409; it does not invent permissions or assignment. Canonical access management now rejects creating/updating a standalone technical-only role set | **P1 found and fixed** |

## Lawyer walkthrough

| Situation | Current behaviour reviewed | Result |
| --- | --- | --- |
| Login before this audit pass | Password login created a valid lawyer session, then redirected every non-superadmin user to `/admin-ui`; the early `/admin-ui` guard accepted only admin/superadmin, so a lawyer could hit HTTP 403 immediately after successful login | **P0 found** |
| Login after fix | `/admin-ui` is a role-aware staff landing: admin/superadmin -> Workdesk, lawyer -> Lawyer Workspace | **FIXED** |
| Incomplete/legacy lawyer account after login | Missing/inactive/unlinked lawyer business profile produces a human-readable setup prerequisite and logout recovery instead of a dead-end JSON error; permissions remain fail-closed | **FIXED** |
| Lawyer workspace / consultation shell | Early server-side guards resolve the personal lawyer identity before returning staff HTML; anonymous session goes to login, unusable lawyer linkage goes to canonical recovery | OK |
| Shared schedule | Staff identity is resolved before the common schedule shell is served | OK |
| M1 ownership | M1 cards/actions use real assignment | OK |
| M2 ownership | M2 cards are rebuilt from consultation-slot ownership, not fake M1 assignment | OK |
| Deep link from case context | Lawyer workspace supports `case_id` focusing/recovery | OK |
| Document review | Lawyer access is constrained to cases for which the lawyer is responsible | OK |
| Consultation result draft | Unsaved result/decision is preserved in session storage when form is closed/reopened | OK |
| Consultation completion | Uses slot ownership and domain outcome service | OK |
| M1 acceptance/rejection | Domain-specific transitions, not generic status editing | OK |
| POA / claim / court / enforcement | Lawyer-owned factual/legal actions have dedicated guarded services/routes | OK |

## Cross-role contradictions checked

- A client cannot start two active routes for one current request.
- A stale Telegram button must not mutate a newer case/payment/document/consultation.
- Generic Back must never replay a business mutation; the new history allowlist is read-only by construction.
- Client-visible status wording is separated from internal technical status codes.
- Admin assignment and lawyer responsibility are not treated as the same concept for M2.
- Workdesk no longer tells an administrator to create generic M1 assignment merely because an M2 case has no `assigned_lawyer_id`.
- Financial receipt, legal facts and document review are staff-owned facts; client buttons only request/report client-side actions.
- Closed cases are archives, not re-openable mutation surfaces.
- Unsupported staff roles or incomplete lawyer linkage do not gain fallback permissions merely to make the UI open.
- Client search/deep links must end on the requested work context, not an unrelated dashboard.

## Remaining gaps / next priority

1. **Execute real CI after restoring GitHub Actions runners.** A real pull-request workflow attempt exists, but jobs did not start because GitHub reported an account billing/spending-limit problem before runner allocation. That is infrastructure-blocked, not a test result.
2. **Live Telegram walkthrough.** Static guards are extensive, but a real bot session is still required to validate message-edit limits, callback age, Redis FSM continuity, process restart, Telegram network retries, the new logical Back stack and visual density on mobile.
3. **Live payment-provider walkthrough.** Provider webhook ordering, late/duplicate callbacks, refunds and real reconciliation require staging credentials/provider callbacks.
4. **Staff browser walkthrough.** Validate responsive density, deep links, session expiry and every primary action with separate admin/lawyer accounts in a real browser. Source-level UI shell recovery is not a substitute for browser execution.
5. **Session-token surface hardening.** `/auth/session` still exposes an API token to staff JavaScript because many legacy staff APIs use `X-Admin-Token`. Removing that exposure safely is cross-cutting and must be done only after mapping/migrating all consumers to cookie-backed authorization; do not patch it piecemeal.
6. **Staging persona script.** Run one seeded case through every normal and exceptional transition with three separate accounts (client/admin/lawyer), capturing screenshots and resulting DB/status/audit evidence.

## Release rule

Do not call the branch production-ready until the current head has an actual successful CI run plus staging persona walkthrough. Static source review can prove that a guard exists and is mounted; it cannot prove Telegram, Redis, database, payment provider and deployment configuration behave correctly together.
