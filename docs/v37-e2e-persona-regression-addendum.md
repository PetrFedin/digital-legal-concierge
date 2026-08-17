# v37 E2E persona regression addendum

This addendum extends `docs/v37-e2e-persona-test-matrix.md` with concrete regressions found while continuing the client/admin/lawyer source walkthrough. The same acceptance rule applies: a live E2E row is accepted only when database state, audit/history and the visible next action agree.

Status legend:

- `SOURCE_OK` — source-level path/guard exists on the current branch.
- `LIVE_REQUIRED` — source looks complete but still needs a real Telegram/browser/provider/database pass.
- `FIXED_V37` — the audit found a concrete dead end or contradiction and changed the branch.
- `BLOCKED_INFRA` — execution is blocked by external infrastructure rather than an application assertion.

## Client navigation regressions

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| C-013 | Generic `Назад` after opening My Case -> Documents -> Payments/read-only section | Returns to the previous allowlisted logical screen, not always Home | FIXED_V37 / LIVE_REQUIRED |
| C-014 | Generic `Назад` has no usable history | Falls back to `Моё дело`; from the case cabinet falls back Home | SOURCE_OK / LIVE_REQUIRED |
| C-015 | Generic `Назад` while unsent message draft exists | Draft guard wins; no draft is silently lost | SOURCE_OK / LIVE_REQUIRED |
| C-016 | Generic `Назад` after partial calculator input | Calculator values remain paused/restorable and breadcrumb metadata does not roll back | FIXED_V37 / LIVE_REQUIRED |
| C-017 | Back history contains a business mutation callback | Impossible by allowlist: payment creation, slot reservation, document mutation, contract confirmation and legal-stage actions are not replayable | SOURCE_OK / LIVE_REQUIRED |
| C-018 | Contract screen requires `Назад` | Visible Back action returns through safe logical navigation without reconfirming the contract/version | FIXED_V37 / LIVE_REQUIRED |

## Administrator regressions

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| A-022 | Open `/admin/workdesk/ui?case_id=<id>` from search/outcome/integrity link | Requested case card opens automatically and the URL remains synchronized with selection | FIXED_V37 / LIVE_REQUIRED |
| A-023 | Close a deep-linked Workdesk case card | `case_id` is removed from URL so refresh does not reopen a closed context | SOURCE_OK / LIVE_REQUIRED |
| A-024 | Search by client name/Telegram/phone | Related cases are included and client row links directly to latest Workdesk case instead of becoming a dead end | FIXED_V37 / LIVE_REQUIRED |
| A-025 | Open `Без юриста` queue with pre-document M1 and M2 cases present | Only M1 statuses where assignment policy actually requires a lawyer appear | FIXED_V37 / LIVE_REQUIRED |
| A-026 | Open M2 consultation queue | Responsible lawyer is derived from consultation-slot ownership, not `Case.assigned_lawyer_id` | FIXED_V37 / LIVE_REQUIRED |
| A-027 | Open M2 Workdesk card before/after slot selection | UI says responsibility is determined by slot; it never recommends generic M1 assignment merely because `assigned_lawyer_id` is empty | FIXED_V37 / LIVE_REQUIRED |
| A-028 | Open M2 Workdesk card actions | M1 SLA assignment shortcut is suppressed for M2; consultation/message/document actions remain available | FIXED_V37 / LIVE_REQUIRED |
| A-029 | Anonymous bookmark to Workdesk/lawyer workspace/consultation desk/shared schedule/payment review/SLA/document review | Staff HTML is not served as an open shell; user is routed to login | SOURCE_OK / LIVE_REQUIRED |
| A-030 | Wrong role or incomplete staff linkage opens guarded staff UI | Fail-closed recovery goes to canonical `/admin-ui` staff landing rather than raw JSON 403/409 | FIXED_V37 / LIVE_REQUIRED |
| A-031 | Wrong role opens Settings or Diagnostic UI | Canonical staff recovery is shown; expired session goes to Login | FIXED_V37 / LIVE_REQUIRED |
| A-032 | Wrong role opens consultation-outcome control | Canonical staff recovery is shown while legacy outcome-repair injection remains intact for a valid admin | FIXED_V37 / LIVE_REQUIRED |
| A-033 | Click `активных дел` metric on Workdesk | Opens a real all-active queue; it no longer incorrectly routes to `Без юриста`. M2 rows remain slot-owned and client-owned pre-assignment M1 rows do not recommend assignment | FIXED_V37 / LIVE_REQUIRED |

## Lawyer regressions

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| L-021 | Anonymous direct lawyer workspace bookmark | Redirect to Login before workspace HTML is returned | FIXED_V37 / LIVE_REQUIRED |
| L-022 | Valid staff session with missing/inactive/unlinked Lawyer profile opens workspace | Fail closed into human-readable staff setup recovery; no permissions are fabricated | FIXED_V37 / LIVE_REQUIRED |
| L-023 | Lawyer opens shared schedule | Personal staff identity is resolved before schedule shell is returned | FIXED_V37 / LIVE_REQUIRED |
| L-024 | M2 appears in admin and lawyer views simultaneously | Both views identify the same consultation-slot lawyer; neither invents an M1 case assignment | FIXED_V37 / LIVE_REQUIRED |

## Cross-role / stale-action regressions

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| X-008 | Client presses Back after a payment/booking/legal mutation | Back replays only a read-only screen and never repeats the mutation | SOURCE_OK / LIVE_REQUIRED |
| X-009 | Admin sees M2 without `Case.assigned_lawyer_id` | Absence of M1 assignment is not interpreted as an assignment defect; slot responsibility remains source of truth | FIXED_V37 / LIVE_REQUIRED |
| X-010 | Search/outcome/integrity sends admin to a case deep link | Workdesk opens that exact case instead of unrelated overview | FIXED_V37 / LIVE_REQUIRED |
| X-011 | Session expires after a staff UI was opened | Subsequent protected request returns to login; role mismatch returns to canonical staff landing | SOURCE_OK / LIVE_REQUIRED |

## Execution blockers

- GitHub Actions runner allocation is still blocked by the account billing/spending condition tracked in issue #116. This is `BLOCKED_INFRA`; it is not an application test failure and not a successful CI result.
- Live acceptance still needs three separate identities (client/admin/lawyer), real Redis FSM, PostgreSQL, Telegram network behavior and payment-provider callbacks.
- Browser/mobile visual density and the new navigation history need real-device or staging browser verification; source inspection alone cannot validate Telegram rendering or responsive interaction timing.
