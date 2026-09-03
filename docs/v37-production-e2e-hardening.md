# Digital Legal Concierge — v37 production E2E hardening

This document records runtime invariants implemented on `feat/v37-guided-case-dashboard-telegram` and must be kept aligned with code. It is not a release certificate and does not replace executable tests, live health checks, Workdesk integrity, audit review, or a real staging walkthrough.

## Release truth

- Workdesk is the source of truth for administrator priorities and process-integrity exceptions.
- Lawyer Workspace is the source of truth for lawyer-owned M1/M2 work.
- Telegram is the client cabinet and must show one primary next action plus compact secondary navigation.
- Static `production`, `go-live`, `acceptance`, `handover`, `scenario-map`, `task-center`, builder and other historical readiness pages are not evidence of production readiness.
- Public liveness/readiness/release endpoints expose only minimal non-sensitive information.
- A failed readiness probe must return HTTP 503.
- CI/test status must never be reported as green unless a run actually exists and succeeded.

## Authentication and staff boundaries

- Staff UI/data is served only after a personal account session. Shared legacy admin tokens are not a production staff identity.
- Superadmin surfaces require the personal superadmin session and MFA enforced by the session guard.
- Admin/lawyer audit actor IDs are derived on the server from the session, never trusted from request payloads.
- Bulk CSV exports, staff search, runtime snapshots, settings, diagnostics, recovery, audit/security, backups and retention are protected staff operations.
- Client PII, message text, provider IDs, raw audit payloads, secret-presence maps, DB URLs, filesystem paths and environment details are not public readiness/debug data.

## M1 end-to-end

1. Client chooses M1 explicitly.
2. Client uploads documents only during valid collection/re-upload stages.
3. Client handoff ends at `M1_DOCUMENTS_RECEIVED`; it does not claim lawyer review has started.
4. The first actual staff document decision starts `M1_LAWYER_REVIEW`.
5. Responsible lawyer accepts or rejects M1. Rejection leaves a client-owned choice: M2 or close.
6. Acceptance opens the service-contract stage.
7. Service contract is a protected encrypted document with an exact version and SHA-256. Client confirmation is bound to the current document ID/version/SHA.
8. Only exact contract confirmation opens the 30,000 RUB obligation.
9. A successful first payment advances to power of attorney. Client `poa_done` is only a signal; factual receipt is confirmed by staff.
10. Claim preparation/sending and the audited 30-day wait are lawyer-owned legal facts.
11. Court stage cannot open before the audited waiting period expires.
12. The 70,000 RUB obligation cannot open without structured court-decision evidence.
13. Enforcement records the factual recovered amount. The server calculates success fee and creates the final obligation.
14. Client cannot create/open the success-fee business stage from a stale Telegram callback.
15. Confirmed final payment closes M1.
16. Stale successful payments never replay an old case transition; they enter a controlled refund/review path.
17. Persisted transient paid statuses (`M1_PAYMENT_30000_RECEIVED`, `M1_PAYMENT_70000_RECEIVED`, `M1_SUCCESS_FEE_RECEIVED`) may only be recovered through the proof-bound recovery service after the exact required payment is already `PAID`. The browser cannot choose the target status.
18. `M1_MONEY_RECEIVED` is not eligible for generic/transient recovery because it requires the factual recovered amount and success-fee calculation.

## M2 end-to-end

1. Client provides a sufficiently detailed question before booking.
2. Optional documents do not block booking.
3. Client chooses a live available slot; slot ownership determines the consultation lawyer without fake M1 assignment.
4. Hold duration comes from validated live setting `consultations.slot_hold_minutes`.
5. Expired hold releases the slot, returns consultation/case to slot choice, expires the payment row for that exact reservation and records history.
6. A late real payment for an expired/stale reservation does not auto-book a different consultation; it enters payment review.
7. Paid/confirmed booking binds consultation, lawyer and slot.
8. Old Telegram slot/payment callbacks must re-check current M2 route, case status and slot/payable state.
9. Completion accepts only deterministic outcomes: `close`, `to_m1`, `follow_up`.
10. Historical `decision=other` records require explicit audited admin resolution to one of those three outcomes.
11. Follow-up creates a fresh consultation on the same case, preserves the prior question as editable context, and requires a new slot/payment lifecycle.
12. Client no-show requires an explicit admin decision: new paid booking or close. The old payment is not reused.
13. Lawyer no-show requires an explicit admin decision: free rebook or refund.
14. Refund request removes the old booking state. Confirmed real refund closes the M2 case; declined refund remains visible and can be returned to the refund queue after the cause is fixed.
15. Closed M2 remains available to the client as read-only archive including documents, payments, history and consultation result.

## Telegram resilience and UX

- Unknown callbacks are handled by the final fallback and always acknowledge the Telegram callback.
- Active FSM input is not silently cleared by an unknown callback; the client is asked to finish or cancel it.
- Known stale callbacks are intercepted before legacy handlers where they could show a wrong stage or mutate state.
- Legacy `contract_sign` cannot confirm without the exact contract version.
- Legacy `poa_done` cannot mark factual POA receipt.
- Stale `poa_instruction` and `court_status` recover to the actual current case/archive.
- DEV/fake payment controls are available only in local/test when the fake provider is explicitly configured; `demo_mode` is never financial authorization.
- Main inline panel de-duplicates the primary callback and does not offer a misleading parallel M2 consultation over an active M1 case.
- `M1_MONEY_RECEIVED` never shows a client success-fee payment action before the actual obligation exists.
- Document wording distinguishes “handed to legal team” from “lawyer actually started review”.

## Admin and lawyer UX

- `/operator` is a role-aware navigation hub only; it does not duplicate case state or priorities.
- Workdesk owns operational priority. Historical task/admin dashboards must redirect to canonical working surfaces.
- Workdesk shows process-integrity contradictions with an actionable destination whenever a safe resolution exists.
- Consultation schedule is role-scoped: lawyer manages only own free slots; admin manages all active lawyers. Reserved/booked slots cannot be deleted from the schedule UI.
- Test slot creation is local/test only.
- Staff settings are whitelisted, typed/range-validated, optimistic-locked and audited.
- Bulk exports are audited and formula-injection-safe.
- Search is staff-only, bounded and HTML-escaped.
- Case assignment audit identity is server-derived; workload override is superadmin-only.
- Lawyer consultation UI does not expose `other` as a completion decision.
- Legacy `/lawyer/ui` routes to the canonical lawyer workspace.

## Verification status

Static regression/contract tests have been added for the above areas, including bot screen import inventory. They have **not been executed in this work session**. GitHub Actions/status must be checked on the current PR head before any claim of passing CI or release readiness. The PR must not be merged without an explicit merge request.
