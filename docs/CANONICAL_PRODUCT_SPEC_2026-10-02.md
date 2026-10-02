# DIGITAL LEGAL CONCIERGE — CANONICAL PRODUCT SPECIFICATION

**Frozen baseline:** 2026-10-02  
**Status:** AUTHORITATIVE FOR CUSTOMER DELIVERY  
**Business sources:** `docs/source_specs/Функциональная_спецификация_MVP.docx`, `docs/source_specs/UX_UI_спецификация_Telegram_бот.docx`  
**Implementation authorities:** `PRODUCT_SCOPE_CURRENT.md`, `SYSTEM_CONTRACT_CURRENT.md`, `ACCEPTANCE_CURRENT.md`, `RUNBOOK_CURRENT.md`, `PROCESS_MAP_CURRENT.md`

This document freezes the single product interpretation used for handover. Historical versioned handover/start/ready documents are evidence only and cannot change this contract.

## 1. Scope

The product contains exactly two legal routes:

- **M1 — standard recovery under DDU / 214-FZ**;
- **M2 — paid personal consultation**.

Telegram is the client cabinet. There is no separate client web cabinet in the delivery scope. CRM, lawyer workspace and leadership/operations surfaces are internal staff products.

The self-filing court-document package is an **M1 service mode**, not a third route.

## 2. Core UX contract

Persistent client navigation is:

`Home → Calculate → My Case → Documents → Contact Lawyer`.

Rules:

- one screen = one primary action;
- client sees client-safe status and nearest action, never internal CRM comments/status codes;
- Back/Cancel/timeout/retry must not destroy persisted business data;
- every mutating Telegram action is bound to the exact Case/domain object when ambiguity is possible;
- stale callbacks fail closed and never silently switch Case;
- closed matters are read-only; a new calculation/new matter is explicit;
- calculations are preliminary guidance until lawyer review.

## 3. Case / state authority

`Case.status` is the single process-state authority. Business transitions occur through `CaseService` or dedicated domain services using the same transition policy. Generic arbitrary status editing is not a supported business mechanism.

### M1 canonical chain

`calculator preview → explicit save → client decision → consent → documents → lawyer review → accept/request-more/reject-or-M2 → contract → initial payment → POA → claim → 30-day wait → court → second payment → enforcement → recovered money → success fee → M1_CLOSED`.

The source-spec statuses remain the business vocabulary. Current code may use additional controlled intermediate states for recovery/compatibility, but they must not create another route or bypass the legal order.

### M2 canonical chain

`description → optional documents → slot selection → payment obligation/confirmation → booked consultation → lawyer result → M2_CLOSED or M2_TO_M1`.

Historical `M2_CONSULTATION_ROUTE` is compatibility-only in the implementation. New writes use the current M2 entry/state authority and must advance into `M2_DESCRIPTION_PENDING`.

## 4. Roles

### Client
May create/continue their own inquiry, calculate, upload/replace their own document version, view client-safe status/history/payment state, choose a consultation slot, send messages and perform explicit client confirmations. Client actions never establish lawyer, court, bank/provider or recovered-money facts.

### Administrator
Owns operational queues, assignment, factual payment reconciliation, consultation operations, technical document reupload requests, delivery/retry operations and operational follow-up. Administrator does not make substantive legal approval/rejection decisions.

### Lawyer
Owns legal document review, legal Case decisions, M1/M2 legal transitions, claim/court/enforcement facts assigned to the lawyer, consultation outcome and legal source/deliverable approval.

### Superadmin / leadership
Owns users/access, security, audit, backup/restore, retention/legal hold, diagnostics and controlled recovery.

Every server endpoint enforces authorization independently of UI visibility.

## 5. Data model

The delivery data authority is PostgreSQL. Core aggregates are:

- User / AdminUser / Lawyer;
- Case + Case history/audit;
- Calculation + versioned legal rule evidence;
- Document + immutable versions/security/encryption metadata;
- DocumentDerivative for controlled sanitized/OCR derivatives;
- Payment + append-only PaymentEvent + PaymentWebhookEvent;
- Consultation + ConsultationSlot;
- Message;
- Notification;
- consent/service-contract evidence;
- retention/legal-hold records;
- self-filing aggregate as an M1 service mode.

Redis stores Telegram FSM/navigation/drafts and coordination state only. Loss of Redis must not fabricate or erase persistent legal/financial facts.

## 6. Documents

Canonical admission:

`incoming bytes → malware admission → file/content validation → SHA agreement → immutable encrypted source → optional sanitized/OCR derivative → later extraction/index`.

Rules:

- original accepted evidence is never overwritten by sanitizer/OCR;
- files are encrypted at rest with per-document envelope metadata;
- storage identity is Case-scoped and portable;
- rejected bytes are never retained as plaintext;
- derivatives have their own checksum, processor/version, recipe and provenance;
- authorization of a derivative re-authorizes the source Document/Case;
- key rotation, retention and restore rules apply to source and derivatives.

## 7. Payments

Money is a business lifecycle, not a button.

Supported production mechanisms:

- **offline/manual reconciliation** — persisted obligation, independent bank/accounting confirmation by authorized staff, no fabricated external payment URL;
- **YooKassa** — only when approved test/live credentials and provider evidence are deliberately configured.

`disabled` and `fake` are not production payment mechanisms.

M1 full representation retains the approved commercial stages including 30,000 RUB, 70,000 RUB and success-fee stage as defined by the source specification/current contract.

M2 remains paid: slot booking becomes final only after the exact payment/reservation context is confirmed.

Provider callbacks are idempotent evidence; stale money cannot mutate a different Case or reservation.

## 8. Consultation calendar

Consultation slots are persisted, reservable and concurrency-safe. Two clients cannot own the same slot. Hold expiry, reschedule, cancel, client no-show, lawyer no-show, rebooking and refund/review paths are explicit.

Client reminders include 24-hour and 2-hour consultation reminders. Unpaid slot reminders/expiry use the configured reservation policy.

## 9. Notifications and communication

Notifications are durable records with retry/delivery state. Scheduler/dispatcher handles:

- abandoned client actions;
- document requests;
- payment reminders;
- consultation reminders;
- claim/court/enforcement events;
- staff attention signals;
- failed delivery retry.

A notification never substitutes for a business-state transition.

## 10. CRM and lawyer workspace

The Case card is self-contained: client, route/status, calculation, documents/versions, legal review, payments, nearest action/deadline/responsibility, messages and event history.

Administrator dashboard prioritizes operational queues and financial/consultation attention. Lawyer workspace shows assigned legal work, documents, consultations, messages and permitted legal actions. Leadership controls are separated from daily work.

## 11. Security / audit / retention

Mandatory controls:

- personal staff accounts and RBAC;
- MFA for privileged operations where configured;
- protected sessions and revocation;
- immutable/tamper-evident audit chain;
- encrypted document storage and key rotation;
- one-time document access grants;
- legal hold and two-person retention deletion;
- encrypted authenticated backup and tested restore;
- no secrets in repository/logs/client sessions.

History is append-only by business design; corrections are represented by later events, not history rewrites.

## 12. NFR / operations

Required delivery properties:

- production PostgreSQL;
- production Redis for Telegram FSM;
- Docker/container runtime;
- idempotent writes;
- concurrency protection for Case/payment/slot/document mutations;
- UTC persistence + configured business timezone presentation;
- scheduler singleton/heartbeat;
- monitoring for Telegram, DB, Redis, payments/webhooks, storage, scheduler, notification backlog and backup freshness;
- reproducible migrations, backup and restore;
- no duplicate runtime owner for the same HTTP method/path.

## 13. Acceptance boundary

Customer release requires one frozen candidate SHA whose evidence agrees across:

`client/staff UI → domain result → PostgreSQL state → audit/history/payment/document evidence`.

Mandatory acceptance includes M1, M2, error/retry/stale scenarios, role separation, document security, payment consistency, calendar concurrency, notification retry, backup→restore, staff browser E2E and real Telegram personas.

Production claims are never made from source inspection alone.

## 14. Conflict resolution

Precedence:

1. approved Functional Specification for business rules;
2. approved UX/UI Specification for client/staff interaction, where it does not contradict business rules;
3. this frozen canonical specification;
4. `PRODUCT_SCOPE_CURRENT.md`;
5. `SYSTEM_CONTRACT_CURRENT.md`;
6. `ACCEPTANCE_CURRENT.md` / `RUNBOOK_CURRENT.md`;
7. implementation/process maps;
8. historical versioned documents.

Known reconciliations already frozen:

- only M1/M2 exist;
- `M2_CONSULTATION_ROUTE` is not a new writable modern state;
- Telegram is the client cabinet;
- source evidence is immutable;
- client cannot establish staff/provider facts;
- payment receipt is not inferred from a click/screenshot;
- calculation is preliminary and legally reviewable;
- self-filing is M1 service mode, not M3.
