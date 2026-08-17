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
| Generic `Назад` | Global handler currently resolves to canonical current/home state rather than a true previous-screen history | **P2 gap** — safe, but not a complete implementation of the UX-spec previous-logical-screen rule |

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
| First payment | Archived/wrong-stage payment actions are navigation/recovery only | OK |
| POA | Client reports readiness only; factual receipt remains a staff action | OK |
| Claim / 30-day wait | Legal facts are lawyer-owned; court action is separated from client UI | OK |
| Court / 70,000 RUB | Court payment requires court-decision evidence before the obligation opens | OK |
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
| Late payment for stale reservation | Must enter payment review instead of auto-booking another slot | OK by implemented guard/reconciliation path |
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
| Unassigned case | Assignment service checks an active lawyer business profile and a live lawyer login | OK |
| Unreachable historical assignee | Integrity surface provides guarded repair instead of manual legal-status mutation | OK |
| Document queue | Canonical document review is role-protected before staff HTML is served | OK |
| Payment received but ambiguous | Payment review is a separate controlled queue | OK |
| M2 stale reservation payment | Dedicated reservation-reconciliation path exists; old provider link is not treated as current booking | OK |
| Refund pending/declined | Controlled refund workflow; declined refund can be explicitly returned to processing | OK |
| SLA overdue without lawyer | Primary action satisfies assignment prerequisite instead of opening an empty SLA action | OK |
| M2 no-show/legacy outcome | Consultation-outcome control routes to an actionable admin surface | OK |
| Manual full scheduler | Restricted to personal MFA superadmin and audited | OK |
| Legacy technical/admin pages | Canonical redirects reduce parallel/demo control surfaces | OK |
| Process contradiction | `/admin/workdesk/integrity` surfaces critical/warning contradictions and safe deep links | OK |
| Staff account has only a non-product role or incomplete lawyer link | Canonical landing now shows an explicit access-setup screen with safe logout instead of raw JSON 403/409; it does not invent permissions or assignment | **P1 found and fixed** |

## Lawyer walkthrough

| Situation | Current behaviour reviewed | Result |
| --- | --- | --- |
| Login before this audit pass | Password login created a valid lawyer session, then redirected every non-superadmin user to `/admin-ui`; the early `/admin-ui` guard accepted only admin/superadmin, so a lawyer could hit HTTP 403 immediately after successful login | **P0 found** |
| Login after fix | `/admin-ui` is now a role-aware staff landing: admin/superadmin -> Workdesk, lawyer -> Lawyer Workspace | **FIXED** in commit `53053f41a6488fc8e3e423def02fc4ebac665bb2` |
| Incomplete/legacy lawyer account after login | Missing/inactive/unlinked lawyer business profile now produces a human-readable setup prerequisite and logout recovery instead of a dead-end JSON error; permissions remain fail-closed | **FIXED** in commit `2192339d6038d0c5c479f29307e966e59956655f` |
| Regression protection | Static contracts cover role-aware login and incomplete staff landing recovery | **UPDATED** in commit `6154d96c5622cd884b6f2421e8759b6225f4e384` |
| Lawyer workspace HTML | Earlier contract-aware route protects `/lawyer/workspace/ui` before returning the HTML shell | OK |
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
- Client-visible status wording is separated from internal technical status codes.
- Admin assignment and lawyer responsibility are not treated as the same concept for M2.
- Financial receipt, legal facts and document review are staff-owned facts; client buttons only request/report client-side actions.
- Closed cases are archives, not re-openable mutation surfaces.
- Unsupported staff roles or incomplete lawyer linkage do not gain fallback permissions merely to make the UI open.

## Remaining gaps / next priority

1. **Execute real CI after restoring GitHub Actions runners.** A real pull-request workflow attempt exists, but jobs did not start because GitHub reported an account billing/spending-limit problem before runner allocation. That is infrastructure-blocked, not a test result.
2. **Live Telegram walkthrough.** Static guards are extensive, but a real bot session is still required to validate message-edit limits, callback age, Redis FSM continuity, Telegram network retries and visual density on mobile.
3. **Live payment-provider walkthrough.** Provider webhook ordering, late/duplicate callbacks, refunds and real reconciliation require staging credentials/provider callbacks.
4. **`Назад` UX.** Current global recovery is safe but not a true navigation history. Implement a bounded logical navigation stack or explicit per-screen back targets without weakening stale-action guards. Calculator steps already use explicit safe back callbacks.
5. **Non-MVP staff roles.** `operator`/`tester` role constants still exist and can be assigned as technical roles. Their standalone landing is now fail-closed and recoverable, but product policy should ultimately either formalize a read-only scope or prevent standalone assignment.
6. **Staging persona script.** Run one seeded case through every normal and exceptional transition with three separate accounts (client/admin/lawyer), capturing screenshots and resulting DB/status/audit evidence.

## Release rule

Do not call the branch production-ready until the current head has an actual successful CI run plus staging persona walkthrough. Static source review can prove that a guard exists and is mounted; it cannot prove Telegram, Redis, database, payment provider and deployment configuration behave correctly together.
