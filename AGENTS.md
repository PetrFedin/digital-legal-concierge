# Digital Legal Concierge — Repository Change Contract

This repository is developed as one existing M1/M2 product. Do not treat it as a blank-slate application and do not expand scope while fixing or hardening the current release candidate.

## Read before changing anything

The authoritative current contract is:

1. `docs/PRODUCT_SCOPE_CURRENT.md`
2. `docs/SYSTEM_CONTRACT_CURRENT.md`
3. `docs/ACCEPTANCE_CURRENT.md`
4. `docs/RUNBOOK_CURRENT.md`
5. `docs/LIVE_REQUIRED_RUNBOOK.md`
6. `docs/POST_LIVE_RELEASE_EVIDENCE.md`
7. `docs/PROCESS_MAP_CURRENT.md` — living implementation/process/debt/change inventory; subordinate to the contracts above

Historical FINAL/GO_LIVE/audit documents are evidence/history only when they conflict with CURRENT documents.

## Mandatory living-map rule

Every repository change that alters product behavior, data, migrations, API/UI, Telegram, scheduler, security, storage, deployment, tests, workflows, evidence tooling, documentation contract, or release procedure **must update `docs/PROCESS_MAP_CURRENT.md` in the same change batch/PR**.

Before finishing a change:

1. identify all affected `P-*` end-to-end processes;
2. update source-of-truth ownership/status/evidence state when behavior changed;
3. create or update a stable `PM-*` item for every discovered inconsistency, duplication, legacy residue, blocker or design risk;
4. never remove a `PM-*` item merely because code moved — close it only with an explicit corrective action and the evidence required by the contract;
5. append a dated change-log entry explaining the change and release/evidence impact;
6. keep unexecuted work as `SOURCE_AUDITED`, `RUNTIME_PENDING`, `FIXED_PENDING_RUNTIME` or `BLOCKED_INFRA`; never call source inspection `LIVE_PASS`.

CI enforces this on pull requests through the `Process map maintenance contract` job, but agents/developers must update the map before CI rather than relying on CI to detect omission.

## Current product boundary

Current routes are exactly:

- M1 — standard recovery / case handling;
- M2 — paid consultation.

Telegram is the client cabinet. Staff use authenticated browser/admin/lawyer surfaces.

Do not add M3/M4, a separate client web cabinet, AI legal decision-making, a second CRM/payment/calendar product or unrelated product scope unless the authoritative product contract is explicitly changed first.

## Business-source-of-truth rules

- `Case.status` is the legal/process state source of truth; mutations go through `CaseService` or an approved dedicated domain service.
- M1 lawyer responsibility uses hardened `CaseAssignmentService` semantics; do not import the historical `app.domain.assignment` layer.
- M2 lawyer responsibility is consultation/slot-driven, not generic M1 Case assignment.
- `Payment` is current projection; `PaymentEvent` is append-only normalized lifecycle history; provider evidence and Case/Audit context remain separate complementary ledgers.
- Redis FSM is transient UX/runtime state, never legal/business source of truth.
- Document storage uses canonical portable `cases/<case_id>/<32hex>.dlcenc` keys through `LocalStorageService`; do not trust arbitrary historical absolute DB prefixes.
- Workdesk final HTML/JS composition belongs to `app.api.workdesk_renderer`; route/data modules must not reintroduce cross-module template patching.

## Release truth

GitHub issue #116 is the current Actions runner/billing/spending release blocker. A workflow with `runner_id=0` and empty/null steps is `BLOCKED_INFRA`, not application PASS and not a substantive application-test failure.

The ordered release chain for one candidate SHA is:

1. restore real runner allocation;
2. full CI/general gates;
3. dedicated PostgreSQL Concurrency → Telegram/Redis Runtime → Browser Staff E2E;
4. one complete manual LIVE_REQUIRED attempt with exact SHA/run/attempt manifest;
5. real Telegram M1/M2 personas and database/audit reconciliation;
6. encrypted backup→separate restore environment + normal restored-runtime proof;
7. safe YooKassa test-shop provider-side paid/refund expansion;
8. release/merge decision.

Do not use LIVE_REQUIRED as a runner probe. Any source/migration/workflow/evidence change after evidence collection begins creates a new candidate and restarts the chain from full CI.

## Change safety

- Prefer a narrow correction of a concrete inconsistency over speculative refactoring while runtime gates are unavailable.
- Preserve one `(HTTP method, path)` runtime owner.
- Do not weaken architecture/CI checks to make a failing design pass.
- Do not rewrite financial, consent or audit history; corrections are new evidence/events.
- Do not perform destructive migration/removal of compatibility state without proving historical data safety.
- Do not merge PR #114 automatically. Merge only after the complete ordered evidence chain exists and release blockers are closed.
