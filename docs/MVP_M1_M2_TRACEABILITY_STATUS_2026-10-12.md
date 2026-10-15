# M1/M2 acceptance ledger — 2026-10-12 (release NOT READY)

This receipt maps UAT-01..UAT-16 from `docs/MVP_OPERATIONAL_ACCEPTANCE_EVIDENCE_2026-10-12.md` to **actual observable proof**. It is not a release declaration. Recording automated pass for one SHA never implies live Telegram, payments, or production acceptance.

## Verified automated baseline

- PR #188: https://github.com/PetrFedin/digital-legal-concierge/pull/188
- Exact commit `a8072246b90f5ec2996df252bc9c02ac9415fced`.
- CI: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38176933276 — success.
- Browser Staff E2E: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38176933330 — success.
- Deployment Readiness: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38176933422 — success.
- Reproducible Dependencies: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38176933259 — success.
- PR #189: repeat object preview code and isolated tests proposed **after** that accepted automated baseline. No PR #189 pass may be inferred from PR #188 checks.

## Test acceptance matrix

| ID | Verified from available evidence | Still required for real UAT / admission | Final status |
|---|---|---|---|
| UAT-01 | Existing bot handlers + prior source-level CI; not an external transcript | /start and identity with real Telegram test user; database and FSM evidence | OPEN |
| UAT-02 | PR #188 calculation+Telegram consistency; exact-head CI success | Verify real Telegram dates; legal-owner approved rate/moratoria rule table | OPEN |
| UAT-03 | Redis FSM CI + recovery unit tests in #188; preview repeated-object tests added in #189 | #189 exact-head CI; Redis restart + repeated callback against test bot | OPEN |
| UAT-04 | Guarded consent handler+history; code-level tests | Approved SHA/URL/operator; real clickthrough + refusal audit | BLOCKED: LEGAL SIGNOFF |
| UAT-05 | Document flows present; staff Chromium suite green on #188 | Scan/quarantine runtime, encrypted storage, real client upload and denied-access | OPEN |
| UAT-06 | M1 stages and YooKassa integration code present | Real provider sandbox, webhook replay, nonduplicated payment ledger and POA | BLOCKED: PROVIDER |
| UAT-07 | Stage transitions and 30-day guard in code | Lawyer live-stage progression screenshots, date evidence, lawsuit/decision | OPEN |
| UAT-08 | Closed case protection and success fee workflows have tests in repo | Partial receipts, exactly-once fee, billing and manual closure operator UAT | OPEN |
| UAT-09 | M2 booking/consultation code & database migration suite | Two concurrent test users, slot expiration, sandbox payment | BLOCKED: PROVIDER |
| UAT-10 | M2 to M1 transition and results workflow in source | Lawyer+client real UAT of decision, consent and subsequent documents | OPEN |
| UAT-11 | Notification/chat code in CI baseline | Telegram retry delivery, outbox failure injection, cross-client ACL | OPEN |
| UAT-12 | PR #188 Browser Staff E2E Chromium/PostgreSQL success | Human acceptance on laptop + mobile, RBAC/MFA/denied access on staged server | OPEN |
| UAT-13 | Redis persistence and container startup suite green for #188 | Simulated network loss, recovery, retries with published test instance | OPEN |
| UAT-14 | PostgreSQL backup/restore job green for #188 | Complete hosted encrypted storage + secrets + DB point-in-time restoration | OPEN |
| UAT-15 | Retention code present | Signed retention and erasure policy, legal hold and request tests | BLOCKED: LEGAL SIGNOFF |
| UAT-16 | CI and deployment readiness #188 successful | Exact SHA publication, monitoring, owners, rollback, external integration proof, UAT signatures | BLOCKED: LIVE EVIDENCE |

## Calculation repeat-object acceptance (PR #189)

Expected sequence:

1. Existing active M1/M2 → home → **🧮 Другой объект**.
2. Enter separate price / planned date / transfer fact through four existing bot steps.
3. Calculator returns preliminary penalty **without** status transition, new `Case`, modification of active `Calculation`, document association, payments, or audit trail for the active matter.
4. Presented actions are **new preview**, **current case**, **message**, **home**. No M1/M2 transition buttons are offered on this *other object's* result.
5. Switching or closing active case in another session during input causes preview to fail closed. Missing price/date never converts existing matter into M2.
6. UI explicitly warns that this other-object result is **not saved** in current case. This resolves the current active-case *accessibility* blocker, not full persistent separate-object workflow.

Test receipt must attach current PR #189 exact-head workflow URLs and the above callbacks' real Telegram transcripts. Multi-case persistence is a separate design task; do not represent nonpersistent preview as fully meeting the functional-spec sentence 'calculation is saved into case card'.

## Release gate

NO-LAUNCH. No unattended merge of stacked drafts. No external services/paid resources provisioned. Final verdict remains **NOT READY** until all OPEN/BLOCKED rows have evidence, customer/legal approval, and the exact final release SHA. Code readiness and operational readiness must be reported separately.
