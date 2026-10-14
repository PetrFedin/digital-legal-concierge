# Final integration & customer UAT gate — Digital Legal Concierge

**Status:** RELEASE HOLD / acceptance not signed. **Date:** 2026-10-13.
**Repository:** `PetrFedin/digital-legal-concierge`.
**Current merged main:** `0777c150be8768e468cea85bc5ff5b682c5bdab5` (#186).
**Source-integrated candidate:** `4659cea2be48ad0e9bd9d4e5733dc98b50739469` (#202, stacked after #201), **not merged**.
**Migration expected on stacked candidate:** `20261013_0020`; read actual Alembic head again on the exact final SHA.
**Cost policy:** no new paid infrastructure or provider activation without explicit owner approval; "free tier" must be demonstrated, not presumed.

## 1. Requirement authority and feature scope

Source-of-truth order is the same as `docs/CANONICAL_MVP_SPEC_2026-10-03.md`:

1. Functional specification `source_documents/1 Функциональная_спецификация_MVP_финальная_версия.docx`: legal M1/M2 scope, sequence, §3.4 calculation of another object, §12.5 one client with many cases, §18.14 historical calculation snapshots.
2. UX/UI specification `source_documents/1 UX_UI_спецификация_Telegram_бот_финальная_версия.docx`: B-001—B-028, actions, callbacks and Back/Cancel/Timeout/error recovery.
3. Client-approved later decisions, only where independently evidenced; never silently redefine the two-route baseline.
4. Current runtime authorities: `CaseService`, `case_transition_policy`, PostgreSQL/Alembic, encrypted Document, PaymentEvent and audit.

**Never create a third legal route.** An M1 commercial service mode is not M3. Telegram remains the MVP client's home. No automated binding legal conclusion, guaranteed recovery or autonomous decision to litigate.

## 2. Honest scope register: required vs additions

| Capability | Classification | Evidence / code stage | Release disposition |
|---|---|---|---|
| M1 standard recovery: calculation, consent, documents, lawyer review, contract, 30,000 ₽, POA, claim, waiting, lawsuit, decision, 70,000 ₽, execution, received money, 10%, controlled close | **Functional MVP / REQUIRED** | Merged #186; external proof in LIVE-001 #185 incomplete | UAT M1 / provider/legal signoff |
| M2: situation, optional files, reservation, consultation payment, lawyer result, handover into M1 or closure | **Functional MVP / REQUIRED** | Merged baseline and CI; external UAT incomplete | UAT M2 / provider test / lawyer |
| Correct early/on-time handover (0 overdue / 0 RUB) | **Correctness defect / REQUIRED** | Draft #188 | Independent review + live dates |
| Repeat another-object preview, separate owned Case, immutable calculation history and idempotent save | **Functional §§3.4, 12.5, 18.14 / REQUIRED** | Draft #189 → #191 → #192 → #193 | Review DB migration 0019, callback receipts and 2-client ownership |
| Owned read-only closed-case history, many-case pagination, no client mutation | **Existing archival promise / REQUIRED** | Draft #195 | Terminal owner/retention negative tests, UI evidence |
| Stale/erased archive callback quarantine | **Privacy/security defect / REQUIRED** | Draft #197 | No post-erasure content or financial/audit leak via old buttons |
| Minimize new message payload in immutable audit | **Privacy/security defect / REQUIRED** | Draft #199 | Historical sealed audit with old excerpts remains open in #198 |
| Client archive file retrieval preflight / one-time grant | **Security hardening for existing files** | Draft #200/#201 and PG proof #202 | **NOT DELIVERY**: no decryption, file endpoint, Telegram upload or receipt; privacy/security-channel signoff required (#196) |
| M1 self-filing document package: 15,000 ₽, bank transfer and four delivered documents within 3 calendar days after confirmed receipt | **Later customer-requested extension; M1 service mode** | Draft #137 on separate older stack | Preserve specification; independently reconcile with final code/DB; require bank reconciliation + real verified email; **do not auto-enable or advertise** |
| Governed legal rate/source editor (moratoria, caps, rate history and legal signoffs) | **Later control improvement / legal-risk work** | Draft #133 on divergent base | No unreviewed rate revision becomes PRODUCTION; legal authority and formula/moratorium cutoff signoff required |
| Fresh /start and non-persistent six-step intake | **Later customer UX feedback** | Draft #136 on separate stack; newer M1/M2 candidate has its own preview flow | Compare actual callbacks/flow; do not merge two competing preview engines |
| Malware admission/quarantine, PDF repair/OCR derivatives | **Integration master-plan candidates** | Drafts #171/#173, unmerged divergent dependencies | Evaluate separately, preserve original encrypted evidence, operational zero-cost proof and performance |
| Search, PII detection, policy externalization, signature, institutional partner modules | **Proposed growth / post-MVP** | Master plan and Draft #187 proposals, not proof of running modules | Separate approved change requests AFTER customer baseline acceptance |

## 3. Exact-head automated evidence (NOT live evidence)

On `4659cea2be48ad0e9bd9d4e5733dc98b50739469`:

- CI: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38446662467 — SUCCESS.
- Browser Staff E2E: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38446662152 — SUCCESS.
- Deployment Readiness: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38446662553 — SUCCESS.
- Reproducible Dependencies: https://github.com/PetrFedin/digital-legal-concierge/actions/runs/38446662243 — SUCCESS.
- In CI, PostgreSQL **migration/backup/restore** and **client archive grant race/revocation** steps executed successfully. New tests are in `tests/test_postgresql_client_grant_gate.py`.

These runs prove code/CI behavior on one unmerged commit. A new integration commit/SHA invalidates this exact-SHA receipt for final admission and needs new workflows.

## 4. Client and staff journeys that must be recorded

All UAT-01..UAT-16 from `docs/MVP_OPERATIONAL_ACCEPTANCE_EVIDENCE_2026-10-12.md` remain **OPEN for customer release** until actual dated external evidence exists. Use the same IDs rather than a second competing checklist.

**M1 golden path:** actual Telegram client -> preliminary calculation (four date combinations) -> explicit verified personal-data consent -> real PDF/image upload -> lawyer review & correction -> signed agreement -> sandbox 30k webhook -> POA evidence -> claim dispatch and waiting gate -> lawsuit & court decision -> sandbox 70k webhook -> recorded partial/full actual receipts -> 10% fee obligation -> controlled closure reason -> read-only archive. Negative paths: forged/replayed provider payment, changed amount, invalid document, cross-case or cross-client IDs, payment before stage, timed-out upload, deleted case.

**M2 golden path:** client free-text -> optional or skipped files -> 2 clients competing for one slot -> hold expiry -> provider sandbox confirmation -> cancellation/rebooking -> reminders -> lawyer decision -> M1 with separate valid consent or closed with audit. Negative paths: late webhook to replaced slot, old callback, third-party booking, unpaid slot viewed as booked, simultaneous updates.

**Additional approved scenarios:** at least 3 distinct real client Telegram accounts (do not substitute 3 chats from one account); owner selecting multiple matters while a draft message/upload is pending; exact frozen preview receipt after a midnight boundary; attempts to reopen erased or archived case with old Telegram callback; bank-accounting and email evidence for self-filing only if its separate M1 service mode is accepted and integrated; no fictitious bank transfer or email acknowledgment.

**Staff:** real administrator and lawyer identities with isolated cases; MFA, forbidden role actions, queues, assignments, SLAs, consultation schedule, redacted audit and reproducible restoration. Screenshots at mobile, tablet and desktop widths; no reliance on green browser automation alone.

## 5. Required operational evidence per step

For every completed test persist in a redacted release bundle:

`timestamp + environment + exact git SHA + image digest + actor role + synthetic/test case_id + requested operation + pre/post status + result + provider event/ledger/audit id where applicable + screenshot or test log + reviewer + defect number`.

Never store Telegram bot tokens, HMAC secrets, provider credentials, access bearer tokens, unredacted client papers or personal identifiers in GitHub issues, release bundles or CI artifacts.

Minimum evidence folders: `release/actions`, `release/db`, `release/telegram`, `release/payments`, `release/documents`, `release/legal`, `release/uat`, `release/operator`.

## 6. Hard release blockers and owners

| Blocker | Accountable owner | Admission evidence |
|---|---|---|
| Dependent Drafts #188 → #189 → #191 → #192 → #193 → #195 → #197 → #199 → #200 → #201 → #202 | Technical lead + **independent reviewer** | Each dependency reviewed, safely integrated; final exact-head required workflows GREEN |
| Historical sensitive text in sealed audit (#198); retained content, revocation and backups | Privacy/legal owner + security engineer | Specific lawful remediation, no hash-chain falsification, permission and restore proof |
| Encrypted archived file delivery (#196) | Security + legal/privacy + product owner | Approved channel/consent and real one-use transport; no delivery claimed for preflight/grant |
| Future/current legal rule periods, official rates, moratoria, exclusions, contract template and consent B-007 | Responsible lawyer + legal/product owner | Approved exact text/source/rule revision hashes and dates; negative example review |
| Live environment and availability | Infrastructure owner | Authorized isolated Timeweb staging, separate PostgreSQL/Redis/encrypted storage, latest migrations, /health, /ready, exact release SHA; real backup/restore |
| Live Telegram and provider | Integration owner | 3 separate real client test accounts, YooKassa approved sandbox, 30k/70k/10% and consultation ledger/webhook proof; explicit SMTP/bank proof for approved self-filing mode |
| End-user UAT, rollback and operator responsibility | Customer + support owner | Signed M1 and M2 UAT, 0 P0/P1 defects, monitored restore and rollback rehearsal |

**Connectivity observed 2026-10-13:** `MacBook-Air-Petr.local` OFFLINE via authorized Remote Desktop Commander; no new Timeweb staging run was executed in this work period. Do not declare the environment reachable or the public bot accepted.

## 7. Release decision

A release candidate may be **SOURCE GREEN** on its exact SHA but still **NOT CUSTOMER UAT READY** or **NOT PRODUCTION ACCEPTED**.

**Current conclusion:** source/security CI evidence exists, but Draft integration, privacy/legal approval, secure file transport, actual external Telegram/provider/Timeweb proof and signed customer handover remain open. **Production HOLD; no live users, billable provider activity or paid resources authorized by this document.**

Before merging or staging, re-read the dependency chain and the exact current SHA. After integration, rerun full source + PostgreSQL/restore + browser UI checks, then run external tests with permissions and signed acceptance. All extra agreed functions must be explicitly retained, deferred with reason or rejected by owner; never quietly lost during stabilization.
