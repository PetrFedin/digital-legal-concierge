# v37 continued E2E audit — 2026-08-19

This checkpoint continues the source-level client/admin/lawyer walkthrough on `feat/v37-guided-case-dashboard-telegram`. It is deliberately separate from a live/staging acceptance result: GitHub Actions runner allocation is still blocked externally, and Telegram/Redis/PostgreSQL/payment-provider/browser execution has not been completed in this environment.

The approved product boundary remains unchanged: only M1 standard recovery and M2 personal consultation. Telegram is the client cabinet. Administrative and lawyer responsibilities are distinct product roles.

## Acceptance lens used in this pass

For every path the check remains:

`entry -> visible current state -> one safe primary action -> mutation boundary -> persisted result -> audit/history -> visible next action -> stale/retry/recovery -> archive/exit`

A screen is not accepted merely because it renders. The persisted case/payment/document/consultation state and the next visible action must agree.

## Findings closed in this continuation

### P1 — sensitive admin shells were returned before server-side UI authorization

Historical payment-review, refund and SLA modules returned their HTML shell first and relied on JavaScript `/auth/session` / API calls to discover an invalid or wrong-role session. Their underlying mutation APIs were protected, but the UI boundary was inconsistent and could leave stale bookmarks on misleading screens.

Effective early routes now authenticate a personal admin/superadmin session before returning:

- `/admin/payment-reviews/ui`
- `/admin/refunds/ui`
- `/admin/sla/ui`

The early guards reuse the current hardened business templates (`PAYMENT_REVIEW_CENTER_HTML`, guided `REFUND_UI`, `SLA_CENTER_HTML`) rather than replacing them with simplified/demo surfaces. This is important because FastAPI route order means an early auth wrapper can otherwise hide later business patches.

Source-level regression coverage: `tests/test_v37_staff_ui_shell_guard.py`.

### P1/security — browser JavaScript could read the staff bearer token

Legacy staff screens expected `/auth/session` to return the real bearer token and copied it into `X-Admin-Token`. That undermined the HttpOnly session-cookie boundary.

The effective early `/auth/session` now returns identity metadata plus the non-secret compatibility sentinel `httponly-cookie-session`; it no longer exposes the actual bearer credential to browser JavaScript. `RequestOriginGuardMiddleware` bridges a validated same-origin HttpOnly cookie to legacy header-based FastAPI dependencies only inside the ASGI request scope.

Important compatibility rules:

- real API bearer clients retain their existing transport contract;
- safe cookie reads are internally bridged;
- unsafe cookie mutations pass cross-site/origin checks before the internal header is injected;
- the browser sentinel is never treated as a real bearer credential.

Source-level regression coverage: `tests/test_v37_cookie_session_transport.py`.

### P1/privacy — sensitive non-`/admin` staff surfaces were cacheable by default

The no-store security boundary historically covered obvious prefixes such as `/admin`, `/access`, `/lawyer`, `/security`, but several pages containing client/legal/payment data or privileged operational controls lived under different prefixes.

`SecurityHeadersMiddleware.SENSITIVE_PREFIXES` now also covers protected surfaces including:

- `/document-access`, `/contracts`, `/message-center`, `/consultation-slots`, `/search-center`;
- `/audit`, `/retention`, `/recovery`;
- `/settings-ui`, `/health-center`, `/diagnostic-center`;
- release/maintenance/QA/handover/operations compatibility centers.

Those responses receive `Cache-Control: no-store, max-age=0` and `Pragma: no-cache`.

Source-level regression coverage: `tests/test_v37_cookie_session_transport.py`.

### P1 — conflicting admin + lawyer responsibility in one personal account

The role model permits multiple role labels, and `normalize_roles()` also adds `admin` to `superadmin`. Generic staff actor resolution prioritizes superadmin/admin before lawyer, while lawyer-only legal actions use the lawyer actor/profile. Therefore a personal account containing both administrative and lawyer product roles is operationally ambiguous: the canonical staff landing can behave as admin while lawyer-only surfaces expect lawyer responsibility.

Canonical access management now rejects new or updated role sets that combine:

- `lawyer` + `admin`; or
- `lawyer` + `superadmin`.

Technical `operator` / `tester` roles may still be additive, but a real product workspace role remains mandatory.

The UI explains that administrative and legal responsibility require separate personal accounts. The system does not silently split or mutate a historical account.

Source-level regression coverage: `tests/test_v37_access_role_guard.py`.

### P1 — historical mixed-role records could remain invisible

Existing database records may predate the new role rule. `/access/ui` now scans existing staff users server-side and renders a prominent warning listing historical accounts that still combine administrative and lawyer responsibility.

The warning is deliberately non-destructive: it tells the superadmin to create/separate personal accounts but does not revoke sessions, disable a lawyer profile or change assignment automatically.

### P1 — wrong-role bookmark to access management ended in raw 403

`/access/ui` was protected, but a regular admin/lawyer opening the superadmin-only page could still reach a raw JSON 403. The effective access UI guard now follows the same recovery contract as other staff surfaces:

- expired/invalid personal session -> `/login`;
- wrong role, MFA/config/profile mismatch -> canonical `/admin-ui` staff landing;
- valid current superadmin/MFA -> access-management UI.

No permissions are elevated during recovery.

### P1/UX — M1 rejection looked visually complete before the client chose what to do

`M1_REJECTED` is not a terminal client state: the client still chooses consultation M2, asks the team for clarification or explicitly closes the request. Showing that state at 100% made the cabinet contradict its own primary action.

Client presentation now shows:

- status: `Ведение дела не принято — выберите следующий шаг`;
- progress: 35%, not 100%;
- the existing controlled rejection recovery remains the source of the actual M2/close decisions.

The state machine itself is unchanged; only the client-facing truthfulness of the status/progress display was corrected.

### P2/UX — internal `success fee` wording leaked into client status

The internal payment code/status remains unchanged, but the Telegram cabinet now says `финальный платёж` / `финальный платёж получен` rather than exposing English/internal process jargon.

Source-level regression coverage: `tests/test_v37_client_status_language.py`.

### P0/P1 concurrency — two simultaneous entries could create two active client cases

The product invariant says a client can have only one active route, but the historical implementation enforced this mainly in application flow:

1. read `get_active_case_for_user()`;
2. if nothing found, create a case.

The calculator/common bot context and M2 intake could execute that sequence in separate transactions. Two near-simultaneous Telegram updates, retries or independent entry points could both observe `no active case` before either transaction committed and create parallel M1/M2 cases.

This is now closed in three layers:

1. **Domain serialization.** `CaseService.get_or_create_active_case_for_user()` locks the stable `User` row with `SELECT ... FOR UPDATE`, then re-checks active cases and creates only if the re-check is still empty.
2. **All canonical Telegram creation paths use it.** `BotContextService` and `ConsultationIntakeService` delegate to the serialized helper. M2 still validates the resulting route/status and fails closed with `ActiveCaseRouteConflict` if another route already won the race.
3. **Database invariant.** Migration `20260819_0014_one_active_case_per_client.py` creates a partial unique index on `cases(client_id)` only for non-terminal statuses. Historical closed/archived cases remain unlimited; only simultaneous live matters are prohibited.

The migration is deliberately fail-closed for historical duplicates. Before creating the index it queries duplicate active `client_id` values. If conflicts exist, deployment stops with an explicit error; the migration does **not** auto-close, archive or choose between legal matters.

### P1/operations — historical duplicate active cases needed a visible staff control

The early Workdesk integrity route now preserves the full existing integrity audit and adds a critical `multiple_active_cases_for_client` issue to every conflicting live case. The detail lists the other active case(s), links to the exact Workdesk card and explicitly tells staff not to close anything automatically before reconciling history/documents/payments/consultations.

It also exposes `duplicate_active_client_count` and whether the active-case scan itself was truncated.

The route is mounted before the historical integrity route, so the additional invariant cannot be hidden by FastAPI route precedence.

Source-level regression coverage: `tests/test_v37_single_active_case_invariant.py`.

### First-user concurrency check

The new case-row serialization depends on a stable client row. `UserService` was rechecked: Telegram user creation already uses a nested transaction/savepoint and catches the unique-key `IntegrityError`, then reloads the concurrent winner. The first `/start` user-creation race therefore already has its own recovery and does not need a parallel duplicate-user workaround in this pass.

## Client Telegram UX re-check

The main client state presentation remains strong at source level:

- Home and `Моё дело` show case number, simple client status, progress, next action, blockers, documents, consultation/payment summaries and update time;
- payment calls to action are explicit at runtime (`Оплатить 30 000 ₽`, `Оплатить 70 000 ₽`, final percentage, route-aware M2 payment);
- generic Back uses bounded read-only history and cannot replay payment, booking, document mutation, contract confirmation or legal actions;
- calculator/message drafts remain protected from accidental navigation loss;
- closed M1/M2 cases remain read-only archives;
- M1 rejection no longer visually impersonates a completed case;
- final payment wording is client-readable.

### Remaining P2 Telegram usability debt found in this pass

The persistent reply keyboard still uses trampoline screens for several callback-only sections. Example: tapping `📁 Моё дело` sends an explanatory message with another inline `📁 Моё дело` button, so the client must tap twice to reach the detailed cabinet. Similar extra confirmation exists for documents and some communication entries.

This is not a data-integrity defect, but it conflicts with the approved low-friction navigation intent. It should be removed by sharing the canonical read-only screen renderers with both `Message` and `CallbackQuery` entry points, rather than duplicating business logic or simulating callbacks. Until that refactor is implemented and live-tested, keep it as P2 rather than introducing a risky parallel renderer.

## Administrator walkthrough status after this pass

Source-level path remains coherent for:

- personal login -> canonical Workdesk;
- all-active/unassigned/document/consultation/attention queues;
- M2 responsibility through consultation slot rather than fake M1 assignment;
- exact case deep links from search/outcome/integrity;
- search by client -> related cases -> Workdesk;
- payment review -> controlled confirm/reassign/refund decision;
- refund -> record result only after real provider action;
- SLA -> explicit recovery plan and audit;
- access management -> product-role validation, historical conflict visibility, MFA boundary;
- server-side staff shells and no-store response boundary;
- critical visibility for any historical client with more than one active case.

## Lawyer walkthrough status after this pass

Source-level path remains coherent for:

- personal login -> lawyer workspace;
- incomplete lawyer profile -> human-readable fail-closed recovery;
- full composite workspace preserved behind the early auth route;
- M1 assignment ownership and M2 consultation-slot ownership;
- document responsibility;
- M1 accept/reject, POA receipt, claim, court evidence/70k gate and enforcement actions;
- consultation outcome draft preservation and deterministic completion decisions.

## Cross-role invariants rechecked

- only one non-terminal client case can exist after migration; M1 and M2 cannot race into parallel active matters;
- M2 responsibility does not invent `Case.assigned_lawyer_id`;
- administrative and lawyer product responsibility is not combined in newly managed personal accounts;
- financial receipt/legal facts remain staff-owned facts;
- stale Telegram actions cannot mutate a newer object merely because an old button still exists;
- authentication wrappers must preserve the full effective UI composite;
- browser staff session secret is not returned to JavaScript by the effective route;
- sensitive staff data is marked non-cacheable on the effective response boundary;
- historical contradictory records are surfaced for deliberate staff resolution rather than silently rewritten.

## Verification still required before production acceptance

1. Restore GitHub Actions runner allocation and execute the current PR head. The billing/spending blocker is tracked in issue #116.
2. Apply migration `20260819_0014` first on a staging copy. If historical duplicate active cases exist, resolve them deliberately and rerun the migration; do not bypass the preflight.
3. Run the full client/admin/lawyer staging matrix with separate identities and evidence of resulting DB state + audit/history + visible next action.
4. Run Telegram + Redis FSM on a real bot: concurrent first actions, stale callbacks, Back/Cancel/Home, process restart, network retries and mobile visual density.
5. Run the real payment provider: duplicate/late webhook, stale reservation, refund and reconciliation.
6. Browser-walk Workdesk, Lawyer Workspace, access management and protected staff shells with session expiry/wrong-role cases.
7. Refactor persistent Telegram reply-menu trampolines only after a shared renderer is in place, then verify on mobile.
8. Retire/rewrite shadowed legacy route modules after live regression so future router-order changes cannot revive obsolete auth/UI contracts.

## Release rule

Do not call v37 production-ready from source inspection alone. Current source hardening materially reduces known dead ends, concurrency ambiguity and permission confusion, but production acceptance still requires an actually executed CI run plus live staging persona walkthroughs.
