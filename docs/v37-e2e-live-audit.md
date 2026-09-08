# v37 live E2E audit

This document records production invariants that are being enforced while the
client Telegram flow, admin Workdesk and lawyer workspace are hardened. It is a
review checklist, not a claim that the automated test suite has been executed.

## Core rule

A Telegram message can remain clickable after a case, consultation, document or
payment has changed. A mutating callback therefore needs server-side provenance:
exact case/document/payment/consultation identifiers plus a fresh state check at
the mutation boundary. Historical raw callbacks are navigation/recovery only.

## Client Telegram invariants

- Post-calculation M1/M2/postpone choices are version-bound to `case_id`.
- M1 choice stops at `CLIENT_DECISION`; consent is a separate decision.
- Consent acceptance/decline is version-bound to the exact owned case and row
  locked. A historical consent button cannot silently choose M1.
- Calculator fallbacks for unknown price/date only work on the exact live FSM
  step; M2 route creation and consultation context commit together.
- M1 rejection choice is version-bound to the rejected case; transfer to M2 and
  consultation context commit together.
- Generic document type selection stores `document_case_id` in FSM state and the
  file message re-checks that binding before the legacy encrypted upload path.
- Client document handoff stops at `M1_DOCUMENTS_RECEIVED`. It never claims that
  lawyer review started.
- The first valid staff document decision establishes
  `M1_DOCUMENTS_RECEIVED -> M1_LAWYER_REVIEW`.
- Client POA readiness is version-bound to the exact case and only records
  `M1_POA_CLIENT_READY_REPORTED`; lawyer receipt is a separate staff action.
- Fake payment mutation is local/test-only. A presentation/demo flag is not
  financial authorization.
- Client cannot create the success-fee business stage from
  `M1_MONEY_RECEIVED`; the lawyer enforcement flow creates the obligation.
- Closed cases and completed/refunded payment records are read-only archives.

## Admin Workdesk invariants

- The canonical cabinet is `/admin/workdesk/ui`; legacy parallel cabinets route
  into the canonical Workdesk.
- Workdesk is personal-session protected and supports `?case_id=` deep links.
- Process integrity issues are visible in the live Workdesk, not only through an
  API endpoint.
- An assigned lawyer must have an active business profile and an active personal
  staff login with lawyer role.
- A stale unreachable assignee has an atomic repair flow with status/assignee
  snapshot checks. The repair does not manually edit a legal case status.
- Manual full scheduler execution is restricted to a personal MFA superadmin
  action and is audited.
- M2 responsibility follows the consultation slot lawyer, not the M1 case
  assignment field.
- Client no-show ends in either a new paid booking flow or case closure.
- Lawyer no-show ends in a free rebooking or refund lifecycle.
- Refund declines stay controlled and can be explicitly re-opened after the
  cause is fixed.

## Lawyer/staff invariants

- A successful personal lawyer login must have a reachable product landing. The
  canonical `/admin-ui` compatibility landing is role-aware: admin/superadmin ->
  `/admin/workdesk/ui`, lawyer -> `/lawyer/workspace/ui`.
- Lawyer M1 ownership follows the actual assignment; M2 ownership follows the
  consultation slot.
- Document review UI is server-side protected before staff HTML is returned.
- A staff document decision is snapshot checked and row locked.
- Lawyer acceptance/rejection, POA receipt, claim/court actions and enforcement
  use domain transitions rather than generic status editing.
- Court payment cannot be opened without recorded court-decision evidence.
- Recovered money and the success-fee obligation belong to the lawyer-owned
  enforcement transaction; the client only pays an already-created obligation.
- M2 outcomes exposed in the live lawyer UI are `close`, `to_m1` or `follow_up`;
  historical `other` requires an explicit admin legacy resolution.

## UI/UX rules used during hardening

- One primary next action per screen; secondary actions are recovery/navigation.
- Errors say what was not changed and provide an exact safe next route.
- Old Telegram messages never fail silently and never mutate a newer case.
- Draft input is preserved where possible; destructive navigation is guarded.
- Staff screens use canonical deep links so an operator lands on the exact case,
  consultation, payment or document requiring action.
- Client wording distinguishes `sent to the legal team` from `lawyer review has
  started` and distinguishes `payment created` from `payment confirmed`.

## Verification status

Static regression contracts are being added next to each hardened boundary.
A persona-level source walkthrough is recorded in
`docs/v37-persona-e2e-walkthrough.md`.

A real GitHub Actions attempt now exists for the current v37 head via the open
PR, but the jobs did **not** start: GitHub Actions returned an account-level
billing/spending-limit failure before allocating a runner. `CI`, `Deployment
Readiness` and `Reproducible Dependencies` therefore cannot be treated as either
application-test failures or successful checks. The local environment also
cannot resolve `github.com`, so a fresh executable CI/local run is still required
before release sign-off.
