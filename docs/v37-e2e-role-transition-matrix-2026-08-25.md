# v37 E2E role-transition matrix — Case, documents, money, M2, messages and close

Date: 2026-08-25  
Scope: M1/M2 product only, including `SELF_FILING_PACKAGE` as an M1 service mode. Client cabinet remains Telegram. No M3/M4, no new CRM/payment/calendar product.

## Evidence rule

This document is a regression contract, not a release certificate.

- **SOURCE_OK** means the canonical code path and source regression encode the rule.
- **LIVE_REQUIRED** means PostgreSQL/Redis/browser/Telegram/provider execution is still required before production acceptance.
- GitHub Actions runner allocation remains blocked by issue #116. A run that never receives executable steps is **BLOCKED_INFRA**, not application PASS and not application FAIL.

## Role meaning

| Role | Product authority |
| --- | --- |
| Client | Own Telegram identity and own Case context only. May create/input client facts and initiate allowed actions for the exact selected Case. Never establishes lawyer/provider/admin facts. |
| Operator | **Auxiliary label only**, not a standalone product authority. It must be combined with a base Admin/Superadmin/Lawyer role. `operator` alone must fail closed. `lawyer+operator` remains lawyer-scoped; the label must not broaden responsibility. |
| Lawyer | Legal responsibility only. M1 actions require the assigned Case. M2 result/no-show is tied to the lawyer of the exact booked consultation/slot. Does not confirm provider/admin financial facts. |
| Admin | Operational responsibility: assignment, payment/refund review, lawyer-no-show resolution, operational queues, technical document presence/readability checks and replacement requests. Admin does **not** approve/reject documents on legal substance, establish lawyer-review facts, confirm completeness/jurisdiction or author final legal deliverables. Generic status editing is not a second state machine. |
| Superadmin | Administrative authority plus access-management/security duties. Admin checks treat Superadmin as admin; sensitive document/access surfaces require personal session/MFA where specified. |

The source account model deliberately rejects a standalone Operator/Tester workspace role and rejects one personal account that combines Lawyer with Admin/Superadmin responsibility.

## Canonical transition matrix

| Domain / transition | Read scope | Mutation owner | Required binding / guard | Stale or concurrent result | Source state |
| --- | --- | --- | --- | --- | --- |
| New calculation / new Case | Client | Client | Telegram user identity + idempotent creation operation | duplicate operation must not create a second Case | SOURCE_OK / LIVE_REQUIRED |
| Select active Case | Client | Client | selected Case must belong to current client | no implicit switch from a stale callback | SOURCE_OK / LIVE_REQUIRED |
| Case assignment | Admin/Superadmin; assigned Lawyer can then see work | Admin/Superadmin | Case row/current assignment snapshot | conflicting assignment must not silently overwrite | SOURCE_OK / LIVE_REQUIRED |
| Generic Case status selector | Admin/Superadmin | **No generic owner** except `M1_REJECTED -> M1_CLOSED` compatibility close | `admin_manual_status_policy`; every `CaseStatus` has explicit domain ownership | all other business/lifecycle transitions fail closed | SOURCE_OK |
| Client document upload | Client own Case | Client | exact selected Case + upload FSM provenance | old Case upload entry cannot attach to newly selected Case | SOURCE_OK / LIVE_REQUIRED |
| Document review | Admin/Superadmin broad; responsible Lawyer scoped | **Responsible Lawyer** owns `approve/reject` and legal-review facts. Admin/Superadmin may only perform the operational presence/readability check and `request_reupload` | personal staff account, Case responsibility for Lawyer, Document+Case locks, expected status/version/update snapshot | competing review returns conflict; admin legal decision is rejected; typed comment must remain locally | SOURCE_OK / LIVE_REQUIRED |
| Protected document download | Admin/Superadmin/responsible Lawyer | none (read grant) | personal session; Superadmin MFA; lawyer responsibility; verified+encrypted document; short-lived one-time grant | reused/foreign/expired grant fails closed | SOURCE_OK / LIVE_REQUIRED |
| Client M1/M2 payment entry | Client own selected Case | Client initiates only | `:v2:<case_id>` / exact payment-to-Case ownership | stale A button while B selected cannot reconcile/create B or A payment implicitly | SOURCE_OK / LIVE_REQUIRED |
| Provider payment fact | Client may view own payment | Provider lifecycle / explicitly allowed offline admin path | PaymentLifecycle boundary + provider/audit provenance | duplicate webhook/retry idempotent; conflicting status reconciled/reviewed | SOURCE_OK / LIVE_REQUIRED |
| Payment Review | Admin/Superadmin | Admin/Superadmin | exact Payment+Case+Consultation/slot evidence, row locks, exact retry provenance | same decision/actor/comment may retry; different admin/decision conflicts | SOURCE_OK / LIVE_REQUIRED |
| Refund Review | Admin/Superadmin | Admin/Superadmin | exact Payment + audit actor/decision/comment; no arbitrary audit age-out | exact retry succeeds without second transition; other admin/opposite stale decision conflicts; UI retains draft and reloads after 409 | SOURCE_OK / LIVE_REQUIRED |
| M1 POA received | relevant staff can view | Assigned Lawyer | exact Case assignment, row lock, expected status + updated_at | stale lawyer action returns 409 | SOURCE_OK / LIVE_REQUIRED |
| M1 claim prepare/sent | relevant staff can view | Assigned Lawyer | exact assigned Case + expected status/update snapshot | stale/foreign lawyer cannot advance | SOURCE_OK / LIVE_REQUIRED |
| M1 court open/payment open | relevant staff can view | Assigned Lawyer | exact assigned Case + legal evidence/comment + expected snapshot | stale/foreign lawyer cannot advance | SOURCE_OK / LIVE_REQUIRED |
| M1 money actually received | relevant staff can view | Assigned Lawyer | exact assigned Case + expected snapshot; financial service creates resulting success-fee obligation | generic admin cannot manufacture recovered-money fact | SOURCE_OK / LIVE_REQUIRED |
| M1 self-filing completeness / jurisdiction / transfer-act fact | Admin/Superadmin may view; responsible Lawyer acts | **Responsible Lawyer** | exact M1 `SELF_FILING_PACKAGE` Case, current package version, approved source documents, verified email | stale package version or foreign lawyer fails closed; admin cannot establish legal completeness | SOURCE_OK / LIVE_REQUIRED |
| M1 self-filing bank receipt / reconciliation | Client may view own obligation; Admin/Superadmin operates | **Admin/Superadmin** confirms factual receipt/review/refund path | exact Payment+Case, frozen 15 000 ₽ requisites/purpose, row lock and reconciliation provenance | duplicate/ambiguous money stays in review; Lawyer cannot manufacture receipt | SOURCE_OK / LIVE_REQUIRED |
| M1 self-filing final four deliverables | Client may read after delivery; Admin/Superadmin may inspect | **Responsible Lawyer** approves/uploads exactly four legal deliverables | exact package version, encrypted/versioned Documents, SHA-256, all four required types | incomplete/stale set cannot become READY or be emailed | SOURCE_OK / LIVE_REQUIRED |
| M1 self-filing email delivery / close | Client/staff according to case access | **System delivery lifecycle** after real send evidence | verified delivery email, exact four attachments, package state and delivery audit | send failure stays actionable; generic admin/lawyer status edit cannot fake delivery/close | SOURCE_OK / LIVE_REQUIRED |
| M2 description / optional docs | Client own Case | Client | exact Case+Consultation/FSM provenance | stale draft/callback cannot write to another Case | SOURCE_OK / LIVE_REQUIRED |
| M2 slot hold / booking / reschedule / cancel | Client own Case | Client within allowed lifecycle | exact Case+Consultation+message/slot provenance; slot row concurrency | lost slot race refreshes current options without changing another booking | SOURCE_OK / LIVE_REQUIRED |
| M2 consultation result | Client can read own result | Assigned consultation Lawyer | consultation row lock; assigned lawyer check before terminal retry | exact same stored result/decision may retry; changed result/decision conflicts | SOURCE_OK / LIVE_REQUIRED |
| M2 client no-show | relevant staff/client projection | Assigned consultation Lawyer | assigned lawyer + current booked consultation/slot | exact comment retry only; changed comment conflicts | SOURCE_OK / LIVE_REQUIRED |
| M2 lawyer no-show | Admin/Superadmin | Admin/Superadmin | exact visible `slot_id` + audit actor/comment + consultation row lock | same admin/comment/slot retry only; other admin or old replaced slot conflicts; browser refreshes on 409 without deleting draft | SOURCE_OK / LIVE_REQUIRED |
| M2 lawyer no-show -> free rebook | Admin/Superadmin | Admin/Superadmin | current `LAWYER_NO_SHOW` + target available slot under lock | competing rebook loses safely; no second client payment | SOURCE_OK / LIVE_REQUIRED |
| M2 lawyer no-show -> refund route | Admin/Superadmin | Admin/Superadmin | Consultation+Case+Payment locks; refund-request audit actor/comment; slot release in same transaction | existing `REFUND_PENDING` no longer skips consultation cancellation/slot release; exact terminal retry must match prior admin/comment | SOURCE_OK / LIVE_REQUIRED |
| Client message create | Client own Case | Client | exact Case provenance through draft/review/submit | draft from A is never silently re-labelled B; explicit retarget required | SOURCE_OK / LIVE_REQUIRED |
| Staff message read/reply | Admin/Superadmin broad; responsible Lawyer scoped | Admin/Superadmin or responsible Lawyer | product staff scope + Case responsibility + latest-message snapshot | new message since form load returns 409; typed reply retained | SOURCE_OK / LIVE_REQUIRED |
| Operator-only Message Center | none | none | auxiliary-role fail-closed boundary | 403; no queue/data leak | SOURCE_OK |
| Lawyer+Operator Message Center | assigned Lawyer Cases only | Lawyer within responsibility | product scope repairs legacy broad compatibility and filters projection | auxiliary label cannot broaden queue or reply rights | SOURCE_OK / LIVE_REQUIRED |
| M2 close after completed consultation | Client can read terminal result/archive | Assigned consultation Lawyer | recorded consultation outcome | generic admin status selector cannot create `M2_CLOSED` | SOURCE_OK / LIVE_REQUIRED |
| M1 close after financial success | Client can read terminal result/archive | dedicated M1/payment lifecycle | success-fee/business evidence | generic admin cannot jump from success-fee state to close | SOURCE_OK / LIVE_REQUIRED |
| Terminal archive/read-only | Client/staff according to history/access scope | dedicated lifecycle only | terminal Case identity; archive cannot become selected active Case | old archive actions remain read-only and exact-Case | SOURCE_OK / LIVE_REQUIRED |

## P0 stale/concurrency persona scenarios

| ID | Scenario | Expected invariant | Evidence |
| --- | --- | --- | --- |
| ROLE-01 | operator-only personal account opens Message Center/API | 403; no Case list/message data | SOURCE_OK / LIVE_REQUIRED |
| ROLE-02 | lawyer+operator opens Message Center | only lawyer-responsibility Cases; no unassigned/foreign Cases | SOURCE_OK / LIVE_REQUIRED |
| ROLE-03 | Admin tries generic `M1_DOCUMENTS_PENDING -> M1_DOCUMENTS_RECEIVED` | rejected; document workflow owns the fact | SOURCE_OK / LIVE_REQUIRED |
| ROLE-04 | Admin tries generic `M1_ENFORCEMENT -> M1_MONEY_RECEIVED` | rejected; assigned lawyer/enforcement service owns the fact | SOURCE_OK / LIVE_REQUIRED |
| ROLE-05 | Admin tries generic `M2_CONSULTATION_DONE -> M2_CLOSED` | rejected; consultation outcome owns close | SOURCE_OK / LIVE_REQUIRED |
| ROLE-06 | Lawyer B posts M1 POA/claim/enforcement mutation for Lawyer A Case | 403/404 responsibility failure; Case unchanged | SOURCE_OK / LIVE_REQUIRED |
| ROLE-07 | Lawyer submits M2 result twice with exactly same payload | idempotent terminal retry; one business outcome | SOURCE_OK / LIVE_REQUIRED |
| ROLE-08 | Same lawyer old tab submits a different M2 result after completion | 409; stored result remains authoritative | SOURCE_OK / LIVE_REQUIRED |
| ROLE-09 | Admin A marks lawyer no-show; Admin B submits identical old form | 409 because actor differs; no false success | SOURCE_OK / LIVE_REQUIRED |
| ROLE-10 | Old lawyer-no-show tab references slot A after another admin rebooks slot B | 409; B is not marked as A's no-show; current queue reloads | SOURCE_OK / LIVE_REQUIRED |
| ROLE-11 | Admin sends lawyer-no-show refund choice while Payment already `REFUND_PENDING` but Consultation still `LAWYER_NO_SHOW` | same transaction cancels consultation, frees old slot, advances Case projection and audits resolution; no second payment transition | SOURCE_OK / LIVE_REQUIRED |
| ROLE-12 | Exact retry of already-created lawyer-no-show refund route by same admin/comment | idempotent only when audit proves exact prior action | SOURCE_OK / LIVE_REQUIRED |
| ROLE-13 | Different admin retries already-created lawyer-no-show refund route with same text | 409; no false success | SOURCE_OK / LIVE_REQUIRED |
| ROLE-14 | Refund Review exact repeat after provider result | exact same actor/decision/comment returns current result without duplicate transition/audit/notification | SOURCE_OK / LIVE_REQUIRED |
| ROLE-15 | Refund Review opposite stale decision | 409; no state reversal | SOURCE_OK / LIVE_REQUIRED |
| ROLE-16 | Refund Review browser receives 409 | queue refreshes; per-payment comment draft retained; controls unlock | SOURCE_OK / LIVE_REQUIRED |
| ROLE-17 | Client Case A payment/message/document button after selecting B | no action is reinterpreted as B; explicit Case recovery | SOURCE_OK / LIVE_REQUIRED |
| ROLE-18 | Terminal Case A archive while active B exists | archive remains read-only; no active-context switch/mutation | SOURCE_OK / LIVE_REQUIRED |
| ROLE-19 | Superadmin sensitive document/access path without MFA | fail closed where MFA is required | SOURCE_OK / LIVE_REQUIRED |
| ROLE-20 | Two staff tabs update same document/dialog/payment decision | loser gets conflict/reload path, not false success | SOURCE_OK / LIVE_REQUIRED |
| ROLE-21 | Admin/Superadmin tries `approve` or `reject` in document review | rejected; only responsible Lawyer may make the legal decision | SOURCE_OK / LIVE_REQUIRED |
| ROLE-22 | Admin/Superadmin finds unreadable self-filing file | may request a new readable version without starting lawyer review or approving legal substance | SOURCE_OK / LIVE_REQUIRED |
| ROLE-23 | Lawyer opens self-filing Case assigned to another lawyer | legal review/completeness/deliverable actions denied; no Case/package mutation | SOURCE_OK / LIVE_REQUIRED |
| ROLE-24 | Standard M1 financial-final projection is requested for self-filing | projection is non-applicable; 30k/70k/success-fee chain is never shown as self-filing truth | SOURCE_OK / LIVE_REQUIRED |
| ROLE-25 | Closed self-filing Case remains in old staff/client tab | terminal/read-only semantics apply to queues, messages, document decisions, integrity and Telegram archive | SOURCE_OK / LIVE_REQUIRED |

## Source regression added in this pass

- `tests/test_v37_refund_exact_retry_runtime_contract.py`
- `tests/test_v37_message_center_product_role_scope.py`
- `tests/test_admin_m1_financial_status_guard_v36.py` (expanded from financial-only guard to complete domain-status ownership)
- `tests/test_v37_consultation_outcome_exact_retry.py`
- `tests/test_v37_lawyer_no_show_refund_resolution.py`
- `tests/test_v37_consultation_outcomes_stale_ui.py`
- `tests/test_v37_role_transition_matrix_contract.py`

Existing multi-Case, document, payment, staff concurrency, browser and Telegram suites remain part of acceptance. These source files are regression intent only until the runner and required live infrastructure execute them.

## Remaining release proof

The next executable gate is not another source-only feature pass. Once runners are available, run the matrix against real PostgreSQL/Redis and staff/browser/Telegram sessions, then the provider sandbox. Acceptance requires the visible UI state, authoritative DB state, audit/history and financial ledger to agree after each scenario, including restart and stale-tab recovery.
