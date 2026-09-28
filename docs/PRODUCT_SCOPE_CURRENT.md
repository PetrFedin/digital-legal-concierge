# PRODUCT SCOPE — CURRENT

Status: **authoritative current repository contract** for product scope.

The approved functional and Telegram UX/UI specifications remain the business source documents. This file records their current implementation contract after the production-hardening work. Historical `FINAL`, `GO_LIVE`, versioned audit and acceptance files are context only when they conflict with this document.

## Scope boundary

Digital Legal Concierge supports exactly two legal routes:

- **M1 — standard recovery / legal case handling**;
- **M2 — paid consultation**.

There is no M3/M4 or other legal route in the current product. Telegram is the client cabinet. A separate client web cabinet is out of scope. New legal routes, a new CRM, a second payment product, a separate calendar product and AI legal decision-making are out of scope unless explicitly approved here. The customer-approved self-filing court-document product is **not** a third route: it is an M1 service mode recorded below.

`MVP` in historical specification filenames defines the functional boundary only. It does **not** define the accepted quality level. M1/M2 are expected to meet production-grade correctness, security, recoverability, idempotency, runtime verification and operations standards.

## Client / Case / Calculation contract

A Client can have multiple Cases. Each individual Case can have only one active M1/M2 route at a time.

A Case can have multiple Calculations. The current/latest calculation is selected by business logic; historical calculations remain part of the Case history.

The global **Calculate** action is always available. Starting a new calculation is a new legal inquiry and can create a new Case without closing, mutating or replacing the client's other active Cases.

Delivery retries of the **same source operation** are idempotent through `CaseCreationRequest (client_id, operation_key)`. A different source operation may create a different Case.

No database or application invariant may enforce “one active Case per client”.

### Calculator source-spec reconciliation

The Functional Specification is authoritative for calculator business outcomes when its rule conflicts with a Telegram UX/UI validation sentence. The source UX/UI callback rule that describes `calc_actual_date_submit` as “Дата не раньше договорной” is therefore not an acceptance rule for the current product.

For an already transferred object, `actual_transfer_date < planned_transfer_date` and `actual_transfer_date == planned_transfer_date` are valid factual inputs. They are not rejected as date-validation errors. The completed Calculation is persisted with zero delay and zero penalty, M1 is not offered from that result, and the client may finish the calculator flow or continue to M2 as defined by the Functional Specification.

A contractual transfer date later than the calculation date is likewise a valid informational outcome rather than a fabricated positive claim: the entered facts/result remain Case-bound and the client is not offered M1 from a non-positive current Calculation.

This reconciliation defines input/outcome semantics only. It does **not** approve or freeze any legal formula, rate, coefficient, moratorium or exclusion period. Those calculation rules must come from the parameterized, versioned and effective-dated lawyer-approved rule source required by the Functional Specification; a hard-coded/configuration fallback is not release authority.

## Selected Case context

Telegram maintains an explicit selected Case context through `ClientCaseContext.selected_case_id`.

If the client has one active Case, `My Case` opens it directly. If more than one active Case exists, the client can choose the exact inquiry. The displayed Case number is the context for Documents, Payments, Message history and Case history.

Read-only historical screens may display an exact owned Case without silently changing the selected Case. Any mutation must be bound to the exact Case and must fail closed when a stale Telegram message refers to a different Case.

A stale/crafted Case-selection callback cannot select a terminal or foreign Case.

## M1 product route

After a positive saved calculation the client has two M1 commercial choices plus the separate M2 consultation choice. These are product choices, not new legal routes.

**M1 / full representation** keeps the approved chain:

calculation → client decision → exact-version consent → documents → lawyer review → accept/request/reject → service contract → initial payment → power of attorney → claim → statutory wait → court evidence/status → second payment → enforcement → actual recovered amount → success fee → business closure.

**M1 / self-filing court package** is the customer-approved service mode `SELF_FILING_PACKAGE`:

calculation → self-filing choice → service-specific consent → verified profile email → source documents → lawyer completeness/jurisdiction/transfer-act confirmation → bank-transfer obligation 15 000 ₽ → bank receipt reconciliation → immutable calculation-cutoff snapshot → lawyer preparation/approval of exactly four court documents → email delivery → closure.

The four deliverables are exactly:

1. pretension / pre-trial claim (`Претензия`);
2. statement of claim (`Исковое заявление`);
3. claim-amount calculation (`Расчёт суммы иска`);
4. client roadmap (`Дорожная карта клиента`).

The claim-calculation cutoff is a customer-approved business rule. If the responsible lawyer confirms that the transfer act is signed, the cutoff is the act date. If the act is not signed, the cutoff is the actual service-payment date; the roadmap must explain that the client will request clarification of claims in the hearing and provide a new calculation. The calculator snapshot is source material for the lawyer-authored calculation document and is not an automated legal conclusion.

For self-filing, the customer-approved payment method is **bank transfer only** to the Adygeya Republican Bar Association requisites. The bot must show the exact snapshotted recipient/bank details, exact 15 000 ₽ amount and the mandatory payment-purpose marker `для адвоката Гамза Д.Г.`. No YooKassa, Robokassa, personal card or invented SBP flow is part of this service contract unless the customer explicitly changes it later.

The completed self-filing result must be sent to the client's confirmed profile/delivery email within **3 calendar days after confirmed payment**. Representation in court is not included in this service mode.

Client statements do not establish lawyer, transfer-act, court or financial facts. Those facts are recorded by the responsible role/service through the state machine and supporting evidence.

## M2 product route

The M2 route covers the existing approved chain only:

question description → optional documents → slot selection/reservation → payment/confirmation → booked consultation → result / client no-show / lawyer no-show → reschedule/refund/closure or explicit transition to M1.

Slot reservation and payment are concurrency-sensitive business facts. A stale payment or stale slot must never silently reserve another appointment, reuse money for a different reservation or mutate a different Case.

## Navigation contract

The persistent Telegram menu is:

**Home / Calculate / My Case / Documents / Contact Lawyer**.

Back is a logical navigation history over replay-safe/read-only screens. It must never replay a payment creation, slot reservation, legal decision or other business mutation.

Home and Cancel must not destroy already persisted Case data. Draft-protection rules apply to unsent client messages and in-progress calculator state.

## Consent and contract evidence

Consent acceptance is stored as an immutable evidence record with Case/user identity, type/status/version, exact text snapshot, SHA-256, acceptance time and Telegram provenance.

A Telegram click is not labelled as a qualified electronic signature. The legal effect of click-accept for each document type remains an explicit legal/operational decision; where a signed file or external e-sign is legally required, that requirement is not replaced by the Telegram evidence record.

## Payments

Payment is a financial lifecycle, not a UI button.

Each Payment stores current projection/status plus business timestamps (`paid_at`, `failed_at`, `cancelled_at`, `refunded_at`, `expired_at`). Payment status transitions are also appended to the normalized `payment_events` ledger. Provider webhook payload/evidence remains in `payment_webhook_events`; actor/business context remains in Case/Audit history.

The current release supports two deliberate production payment modes:

- `offline` — a real financial obligation is persisted without manufacturing an external payment link. The client pays outside the bot using team-approved requisites. An authenticated administrator may confirm receipt only after independent bank/accounting reconciliation; the same canonical payment lifecycle then advances M1 or confirms the exact M2 reservation.
- `yookassa` — an online provider mode enabled only when its release-specific provider evidence and credentials are deliberately approved.

`disabled` is a fail-closed maintenance/development switch, not a production-ready payment mode and not permission to auto-mark money as received. `fake` remains local/test only. Full-representation/consultation may use the generic `offline` contract. Self-filing uses its stricter dedicated bank-transfer contract with snapshotted bar-association requisites and mandatory purpose text.

M2 remains a paid consultation in `offline` mode: slot selection may create a payment obligation, but the slot becomes finally booked only after the administrator confirms actual receipt against the exact consultation/reservation context.

No late provider failure may overwrite money already received. Stale M1/M2 money must go to the defined review/refund path instead of resurrecting an obsolete legal stage.

## Closure, archive and retention

These are separate lifecycle concepts:

- **business closure** — legal/service work is completed or explicitly ended; stored with `closed_at` and `close_reason`;
- **archive/read-only** — stored with `archived_at` where applicable;
- **retention deletion** — content removal stored separately with `content_deleted_at` and protected retention controls.

A terminal status alone is not sufficient operational explanation; known M1/M2 closure paths must persist a structured reason.

## Activity and re-engagement

`User.last_activity_at` records Telegram client activity. `Case.last_client_action_at` records activity against the selected/current active Case. These are independent from generic `updated_at`, because staff/scheduler/system work must not postpone a client inactivity reminder.

Approved abandoned-flow reminders are stage-aware, deduplicated and throttled. They never change legal state and only point the client back to the current saved step.

## Time contract

Persistent datetimes remain UTC. Client/staff presentation uses one configured business timezone (`BUSINESS_TIMEZONE`, default `Europe/Moscow`) through the shared presentation formatter. User-facing times must not be assembled by appending `UTC` in individual handlers.

## Explicit non-goals for this release

Do not add new legal routes, new dispute types, client web portal, AI legal judgement, unrelated dashboards, a second payment provider as a product expansion, a new calendar product, or additional guard layers that duplicate public route ownership.

The release goal is narrower: make every existing M1/M2 client, lawyer, financial and administrative fact unambiguous, case-bound, idempotent, recoverable, runtime-tested and operable.