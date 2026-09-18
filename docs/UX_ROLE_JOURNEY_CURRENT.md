# UX / ROLE JOURNEY — CURRENT

Status: **source-audited journey and visual acceptance inventory** for the existing M1/M2 product.

This document does not create a new route, a client web cabinet or a second state machine. Business authority remains in the approved Functional Specification, the approved Telegram UX/UI Specification and the repository CURRENT contracts. Where those sources and the current repository contract are not yet reconciled, the difference is recorded explicitly instead of silently choosing a convenient behavior.

## 1. Product UX boundary

- Client cabinet: Telegram only.
- Staff: authenticated browser surfaces.
- Legal routes: M1 standard recovery and M2 consultation only.
- Client receives client-safe status, current Case context and one main next action.
- Internal comments, raw technical statuses, provider errors and staff-only audit detail are not client UI.
- Mutating actions remain exact-Case / exact-domain bound; visual simplification must not weaken authority or stale-action protection.
- Home, Back, Cancel and recovery states must not silently destroy persisted business data or a protected draft.

## 2. Client journey — Telegram

### 2.1 Entry and persistent navigation

/start / Home resolves current database truth and presents:
1. selected active Case when one is current;
2. explicit Case selector when several active matters exist and context is ambiguous;
3. read-only latest completed matter when there is no active Case;
4. new-client start state when no matter exists.

The current persistent reply keyboard is intentionally stable:
Home / Calculate / My Case / Documents / Contact Lawyer.

**Open reconciliation item:** the approved source specification also describes dynamic menu availability by lifecycle stage, while the current repository contract and implementation use a stable five-item information architecture. PM-022 does not change this behavior. Any later change requires explicit business-contract reconciliation first.

### 2.2 New calculation

Calculate -> new exact Case -> calculator draft -> planned date -> transfer facts -> stored Calculation -> result.

Current UX requirements:
- a new Calculate action does not replace another active Case;
- the same source operation is idempotent;
- recovery resumes the exact Case;
- valid earlier/on-time actual transfer becomes a zero-delay result rather than an input error;
- a future contractual transfer date remains an informational boundary;
- the result remains explicitly preliminary;
- M1 is offered only when the latest stored Calculation is eligible.

### 2.3 M1 standard recovery

Client journey:
calculation -> M1 decision -> consent -> documents -> lawyer review -> accept / request more / reject -> contract -> first payment -> POA -> claim -> waiting period -> court -> second payment when applicable -> enforcement -> recovered amount -> success fee -> close -> read-only archive.

Presentation contract at every stage:
- СЕЙЧАС = client-safe current state;
- ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ = one most important current action;
- progress / readiness = supporting context, not an alternative state machine;
- documents, payments, history and messages are secondary Case-bound destinations;
- a rejected M1 matter offers the existing safe decision paths without inventing a new legal route;
- after closure, mutation controls disappear from the Case card and archive remains read-only.

### 2.4 M2 consultation

Client journey:
description -> optional documents -> date -> slot -> reservation -> payment/approved no-payment path -> booked consultation -> result / no-show -> rebook / refund / follow-up / M1 handoff / closure.

Presentation contract:
- free slots are grouped and shown in the configured business timezone;
- the selected appointment is not replaced until a new time is separately confirmed;
- stale dates/slots return the client to current server truth;
- payment remains tied to the exact consultation/reservation;
- completed consultation controls become read-only archive controls;
- M2 -> M1 is a lawyer outcome on the same Case, not a client button and not a third route.

### 2.5 Client recovery paths checked in source

- several active Cases -> explicit selector;
- stale Case selector -> no silent context switch;
- stale payment callback -> no mutation of another selected Case;
- draft message -> explicit return/discard choice;
- bot/FSM restart -> Redis resume when present, DB-backed Case recovery when transient state is gone;
- stale consultation reschedule/cancel -> current appointment remains unchanged;
- completed M2 stale controls -> result/payments/history/archive only;
- load/payment/file errors -> retry or safe return without claiming a successful mutation.

## 3. Administrator journey — browser

Canonical entry:
login -> /operator -> daily work -> /admin/workdesk/ui -> priority/queue -> exact Case -> one safe action.

Daily work surfaces:
- unified Workdesk;
- client/team messages;
- document review;
- consultation outcomes;
- consultation schedule;
- Payment Review;
- refunds.

Operational control surfaces:
- SLA / overdue work;
- Telegram delivery;
- monitoring;
- security events;
- product settings.

Administrator UX must not expose a generic status editor as a substitute for the state machine and must not perform substantive lawyer decisions.

### PM-022 visual correction

The /operator hub now:
- exposes consultation scheduling directly in the administrator daily-work group;
- removes duplicated SLA/notification links from the settings group;
- preserves a clear split between daily work and control/configuration;
- has visible keyboard focus and an aria-live access/status notice.

## 4. Lawyer journey — browser

Canonical entry:
login -> /operator -> Lawyer workspace.

Primary surfaces:
- My Cases — priorities, M1/M2 responsibility and next action;
- consultation desk — preparation, appointment, result and no-show handling;
- document review — protected one-time document access + legal review;
- messages — Case-bound client communication;
- schedule — own consultation availability.

M1 legal work includes accept/request/reject, M1->M2 legal transfer where allowed, POA/claim/court/enforcement facts and related legal milestones.

M2 responsibility is consultation/slot based. The generic M1 assigned-lawyer projection must not be displayed as the M2 source of responsibility.

Current staff role isolation denies a lawyer access to the administrator Workdesk.

## 5. Leadership / superadmin journey — browser

Superadmin remains a hardened administrative role with MFA; it does not bypass immutable history.

Canonical entry after MFA:
/operator -> Руководительский контроль.

PM-022 groups the existing dedicated surfaces coherently:
- /access/ui — users, roles, MFA, session revocation;
- /audit-center/ui — audit-chain integrity;
- /backup-center/ui — encrypted backup control;
- /retention/ui — legal hold, retention and two-person deletion workflow.

The role badge now renders Суперадминистратор rather than the redundant inherited Администратор · Суперадминистратор.

## 6. Staff visual acceptance

Core visual language already used by current staff surfaces:
- responsive white-card system on a light neutral background;
- dark header;
- one accent primary action;
- green/amber/red semantic status treatments;
- loading, empty, warning and error states;
- business-time rendering independent of browser timezone;
- role-aware navigation rather than hidden unauthorized actions.

PM-022 Browser Staff E2E adds a 390x844 viewport check for administrator and lawyer:
- correct role label;
- role-correct landing links;
- no horizontal page overflow on /operator;
- no horizontal page overflow on the main administrator/lawyer workspace.

Superadmin leadership navigation is source-contract locked in PM-022. A full browser superadmin/MFA persona remains part of later complete release-persona evidence rather than bypassing MFA in a test.

## 7. Evidence coverage vs remaining proof

| Journey | Current automated evidence | Remaining proof |
| --- | --- | --- |
| Client multi-Case / stale Case actions | aiogram runtime tests | complete real Telegram persona |
| Client calculator restart | Redis Telegram runtime | full M1/M2 real persona |
| Administrator auth / role / daily surfaces | Browser Staff E2E | complete operational persona |
| Administrator stale Payment Review | Browser Staff E2E two-tab 409 | provider-backed release evidence |
| Administrator mobile layout | PM-022 Browser Staff E2E | screenshot/manual final visual review |
| Lawyer role isolation / surfaces | Browser Staff E2E | complete legal-work persona |
| Lawyer mobile layout | PM-022 Browser Staff E2E | screenshot/manual final visual review |
| Superadmin Access/Audit/Backup/Retention IA | PM-022 source contract | real MFA browser persona |
| Full M1 end-to-end | source + component tests | ordered LIVE_REQUIRED + real persona |
| Full M2 end-to-end | source + component tests | ordered LIVE_REQUIRED + real persona |

## 8. Remaining UX debts

1. Persistent Telegram menu availability needs explicit specification-vs-current-contract reconciliation before changing visibility.
2. Several staff pages are composed by base HTML plus JavaScript patch layers. They work, but this remains visual-maintenance debt; later P1 simplification should collapse duplicate presentation ownership without changing authority.
3. Browser automation validates responsive geometry and role visibility, not pixel-perfect screenshot baselines. Pixel baselines should be introduced only after the core layout stops changing frequently.
4. A complete client/staff persona remains mandatory before release; source review and component E2E are not a substitute for one real M1 and one real M2 walkthrough with DB/audit reconciliation.
