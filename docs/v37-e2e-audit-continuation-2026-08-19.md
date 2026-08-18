# v37 continued E2E audit — 2026-08-19

This checkpoint continues the source-level client/admin/lawyer walkthrough on `feat/v37-guided-case-dashboard-telegram`. It is deliberately separate from live/staging acceptance: GitHub Actions runner allocation is still blocked externally, and Telegram/Redis/PostgreSQL/payment-provider/browser execution has not been completed in this environment.

The approved product boundary remains unchanged: only M1 standard recovery and M2 personal consultation. Telegram is the client cabinet. Administrative and lawyer responsibilities are distinct product roles.

## Acceptance lens

For every path the check remains:

`entry -> visible current state -> one safe primary action -> mutation boundary -> persisted result -> audit/history -> visible next action -> stale/retry/recovery -> archive/exit`

A screen is not accepted merely because it renders. The persisted case/payment/document/consultation state and the next visible action must agree.

## Findings closed in this continuation

### Sensitive admin shells are authenticated before HTML

Historical payment-review, refund and SLA modules returned their HTML shell first and relied on JavaScript/API calls to discover an invalid or wrong-role session. The effective early routes now authenticate a personal admin/superadmin session before returning:

- `/admin/payment-reviews/ui`
- `/admin/refunds/ui`
- `/admin/sla/ui`

The guards reuse the current hardened business templates instead of simplified/demo surfaces. Source-level coverage: `tests/test_v37_staff_ui_shell_guard.py`.

### Browser staff bearer token is no longer exposed to JavaScript

The effective early `/auth/session` returns identity metadata plus the non-secret compatibility sentinel `httponly-cookie-session`; it no longer returns the real bearer credential to browser JS. `RequestOriginGuardMiddleware` bridges a validated same-origin HttpOnly cookie to legacy header-based dependencies only inside the ASGI request scope.

Compatibility rules:

- real bearer API clients retain their existing transport contract;
- safe cookie reads are internally bridged;
- unsafe cookie mutations pass cross-site/origin validation before the internal header is injected;
- the browser sentinel is never treated as a real credential.

Coverage: `tests/test_v37_cookie_session_transport.py`.

### Sensitive staff surfaces are globally non-cacheable

The global no-store boundary now covers client/legal/payment/staff surfaces outside the historical `/admin` prefix, including documents, contracts, messages, consultation slots, search, audit, retention, recovery, settings, diagnostics and operational/release compatibility centers. Privileged readiness endpoints `/initial-setup-wizard/*` and `/launch-check` are included as well.

Effective responses receive:

- `Cache-Control: no-store, max-age=0`
- `Pragma: no-cache`

Coverage: `tests/test_v37_cookie_session_transport.py`.

### Conflicting admin + lawyer responsibility is blocked for managed accounts

A personal account containing both administrative and lawyer product roles is ambiguous because generic staff actor resolution prioritizes admin/superadmin while legal actions require lawyer responsibility. Canonical access management now rejects new or updated role sets that combine:

- `lawyer` + `admin`
- `lawyer` + `superadmin`

Technical `operator` / `tester` roles may remain additive, but a real product workspace role is mandatory. Administrative and legal responsibility must use separate personal accounts.

Historical mixed-role records are listed visibly in `/access/ui`; the system does not mutate, disable or split them automatically. Wrong-role access-management bookmarks recover through the canonical staff landing rather than raw JSON 403. Coverage: `tests/test_v37_access_role_guard.py`.

### M1 rejection no longer looks like a completed case

`M1_REJECTED` still requires a client decision: M2 consultation or explicit closure. The Telegram presentation now shows:

- `Ведение дела не принято — выберите следующий шаг`
- progress 35%, not 100%

The existing M2/close transition logic is unchanged.

### Internal success-fee wording no longer leaks into client status

Client status uses `финальный платёж` / `финальный платёж получен`; internal payment/status codes remain unchanged. Coverage: `tests/test_v37_client_status_language.py`.

### P0/P1 concurrency — one active case per client

The historical `read active -> create` sequence could race across two Telegram/API transactions and create parallel live M1/M2 matters. The invariant is now closed in three layers:

1. `CaseService.get_or_create_active_case_for_user()` locks the stable `User` row with `SELECT ... FOR UPDATE`, then re-checks active cases before creation.
2. Canonical Telegram case creation (`BotContextService`) and M2 intake (`ConsultationIntakeService`) use that serialized helper. If another route wins the race, M2 fails closed with `ActiveCaseRouteConflict`.
3. Migration `20260819_0014_one_active_case_per_client.py` creates a partial unique index on `cases(client_id)` for non-terminal statuses only.

Migration 0014 preflights historical duplicates. If conflicting live matters already exist, deployment stops and lists them; it never auto-closes, archives or chooses between legal cases.

Workdesk integrity also adds critical `multiple_active_cases_for_client` issues, lists sibling live cases and links directly to each Workdesk card. Staff are explicitly told not to auto-close anything before reconciling history, documents, payments and consultations.

Coverage: `tests/test_v37_single_active_case_invariant.py`.

### One live consultation context per case

A case can retain many historical consultations, but only one live intake/booking/payment context may own current M2 responsibility at a time. Migration `20260819_0015_one_active_consultation_per_case.py` adds a partial unique index on `consultations(case_id)` excluding terminal/history statuses:

- `DONE`
- `CLIENT_NO_SHOW`
- `LAWYER_NO_SHOW`
- `CANCELLED`
- `RESCHEDULED`
- `CLOSED`

Migration 0015 also fails closed on historical active duplicates and does not cancel or close appointments automatically. Existing Workdesk integrity already reports `multiple_active_consultations` as a critical contradiction. Coverage: `tests/test_v37_single_active_case_invariant.py`.

### First-user concurrency already has recovery

`UserService` already protects concurrent Telegram-user creation with a nested savepoint, the unique Telegram-ID constraint and `IntegrityError` recovery that reloads the concurrent winner. No duplicate-user workaround was added.

### Historical NEW / CALCULATOR_STARTED loop is closed

A persisted `NEW` or `CALCULATOR_STARTED` case could previously create a loop:

`Моё дело -> Завершить расчёт` but no CTA -> `calc_start` sees active case -> back to `Моё дело`.

`calculator_active_case_recovery.py` now gives both statuses a real `calc_start` action. When such a case exists:

- a saved Redis/FSM draft is offered for continuation;
- destructive restart still requires confirmation;
- if the draft expired after an old crash, only the questionnaire restarts and the same Case row is reused;
- no second case is created.

The early recovery router delegates all normal later states to the existing calculator handler. Coverage: `tests/test_v37_calculator_active_case_recovery.py`.

### Persistent Telegram menu now opens target sections directly

The old reply keyboard forced a second inline-button tap for several sections. An early exact Message router now opens the real target presentation in one step while reusing canonical decision helpers instead of simulating CallbackQuery objects.

#### `📁 Моё дело`

Uses the shared Home/Case view plus the same `load_client_case_view()` and `_case_buttons()` used by the canonical cabinet. Draft protection remains active.

#### `📄 Документы`

Uses the canonical document action-center helpers for:

- current/archived versions;
- required/review/approved/replacement counts;
- primary next action;
- exact replacement/version callbacks;
- M2 optional-document skip rules.

A stale lower-menu button with no active case never creates a case or uploads into another matter.

#### `💬 Переписка`

Opens the first real dialog page immediately, preserving pagination and read-only completed archives. Only lawyer/team messages actually shown on the page are marked read, and only after the page is successfully sent.

#### `✉️ Новый вопрос`

With an active case, the draft is immediately bound to exact `case_id`/`case_number` and enters the canonical category state. A completed case is never reopened for messages. With no active case, explicit `message_new_request` confirmation is still required before a new request can exist.

#### `💬 Связаться с юристом`

The direct reply-menu entry remains route-aware and presentation-only until the client explicitly starts M2:

- active M1 -> message/history/current case only; no parallel consultation is created;
- active M2 -> resumes the exact saved stage (description / slot selection / booked consultation);
- no active case -> shows consultation entry, but M2 case/consultation creation still happens only after explicit `consult_subject_start`, where serialized one-active-case protection and `ActiveCaseRouteConflict` apply.

The old trampoline handlers remain shadowed as compatibility fallback until live Telegram regression confirms the early exact handlers on real mobile clients. Coverage: `tests/test_v37_reply_menu_direct_my_case.py`.

## Client Telegram status after this pass

Source-level paths now cover:

- direct persistent-menu navigation without extra confirmation screens;
- Home / `Моё дело` status, progress, blocker, next action, document/consultation/payment summaries;
- explicit payment CTA wording;
- bounded read-only Back history that cannot replay mutations;
- calculator and message draft protection;
- stale callbacks bound to exact current objects;
- closed M1/M2 read-only archive;
- route-safe legal-help entry;
- recovery of persisted pre-calculation states;
- database protection against parallel active client matters.

Remaining Telegram acceptance is primarily **live/runtime verification**, not a known source trampoline defect: real Redis continuity, Telegram message-edit constraints, callback age, process restart, network retries and mobile density still need staging execution.

## Administrator status after this pass

Source-level path remains coherent for:

- personal login -> canonical Workdesk;
- all-active/unassigned/document/consultation/attention queues;
- M2 responsibility via consultation slot, not fake M1 assignment;
- direct case deep links from search/outcome/integrity;
- search by client -> related cases -> Workdesk;
- payment review -> controlled confirm/reassign/refund decision;
- refund -> record result only after real provider action;
- SLA -> explicit recovery plan and audit;
- access management -> role validation, historical conflict visibility, MFA boundary;
- server-side staff shells and no-store response boundary;
- critical visibility for duplicate active cases or multiple live consultation contexts.

## Lawyer status after this pass

Source-level path remains coherent for:

- personal login -> lawyer workspace;
- incomplete lawyer profile -> human-readable fail-closed recovery;
- full composite workspace preserved behind early auth route precedence;
- M1 assignment ownership and M2 consultation-slot ownership;
- document responsibility;
- M1 accept/reject, POA receipt, claim, court evidence/70k gate and enforcement actions;
- consultation-result draft preservation and deterministic completion decisions.

## Cross-role invariants rechecked

- only one non-terminal client case may exist after migration 0014;
- only one live consultation context per case may exist after migration 0015;
- M2 responsibility does not invent `Case.assigned_lawyer_id`;
- new managed staff accounts cannot combine admin and lawyer product responsibility;
- financial receipt and legal facts remain staff-owned facts;
- stale Telegram actions cannot mutate a newer object merely because an old button survives;
- auth wrappers preserve the full effective UI composite;
- browser staff session secret is not returned to JS by the effective route;
- sensitive staff responses are non-cacheable;
- historical contradictory records are surfaced for deliberate resolution rather than silently rewritten.

## Verification still required before production acceptance

1. Restore GitHub Actions runner allocation and execute the current PR head. Billing/spending blocker: issue #116.
2. Apply migrations 0014 and 0015 first on a staging copy. Resolve any preflight conflicts deliberately; do not bypass the guards.
3. Run the full client/admin/lawyer staging matrix with separate identities and evidence of resulting DB state + audit/history + visible next action.
4. Run Telegram + Redis FSM on a real bot: concurrent first actions, stale callbacks, direct reply-menu entries, Back/Cancel/Home, process restart, network retries and mobile visual density.
5. Run the real payment provider: duplicate/late webhook, stale reservation, refund and reconciliation.
6. Browser-walk Workdesk, Lawyer Workspace, Access Management and protected staff shells with session expiry/wrong-role cases.
7. After live Telegram regression, remove shadowed legacy reply-menu trampoline handlers and other obsolete route helpers so future router-order changes cannot revive old behavior.

## Release rule

Do not call v37 production-ready from source inspection alone. Current source hardening materially reduces known dead ends, concurrency ambiguity, navigation friction and permission confusion, but production acceptance still requires an actually executed CI run plus live staging persona walkthroughs.
