# MVP operational acceptance evidence — M1/M2

**Scope:** functional specification of the DDU Telegram bot, MVP with exactly two client routes (M1 enforcement and M2 personal consultation), and the approved Telegram UX/UI specification. This checklist is a proof contract, **not** a declaration that external tests already passed.

**Current source baseline:** `main@0777c150be8768e468cea85bc5ff5b682c5bdab5` (2026-10-11). Recompute the SHA before acceptance. CI on an older SHA does not satisfy exact-head acceptance.

## Status definitions

- **CODE PRESENT:** handler/service exists. Neither system integration nor UX correctness follows from its mere existence.
- **AUTOMATED GREEN:** CI on the exact acceptance SHA passes with recorded run links, database migration checks and browser staff tests.
- **STAGING GREEN:** the Docker/Timeweb deployment is healthy, Redis-backed FSM and PostgreSQL migrations are verified, and a real Telegram test account uses the live Telegram Bot API.
- **EXTERNAL GREEN:** a sandbox/authorized YooKassa interaction, webhook verification and ledger reconciliation have recorded evidence.
- **ACCEPTED:** legal owner has approved all consent texts and calculation rules; customer has signed UAT of M1, M2 and recovery drills.
- **READY / NOT LAUNCHED:** all mandatory evidence above is complete; public bot distribution and live billing are intentionally disabled.

## Traceable user journeys (one trace per scenario)

| ID | Persona / journey | Persisted outcome and negative path | Required evidence |
|---|---|---|---|
| UAT-01 | New Telegram user → /start → home → preliminary calculation | User binding stable, no duplicated cases; missing/invalid price and date preserve previous valid input | Real bot transcript + DB case/Calculation |
| UAT-02 | Calculator: transfer pending / transferred late / transferred on time / transferred early | Non-negative days and 0 ₽ for on-time and early transfer; never a positive amount for early handover | Deterministic domain tests + real bot |
| UAT-03 | Calculator interrupted / Telegram callback repeated / back / cancel / stale callback | No ghost case, no duplicate write, resilient FSM, honest confirmation after commit | Redis restart drill + replay test |
| UAT-04 | Calculation → M1 → explicit consent / refusal / return | Consent version, published URL and SHA, UTC timestamp, immutable audit trail; refusal prevents document transfer | Audit export + consent approval |
| UAT-05 | M1 → upload DDU/identity files → legal review → request corrections | Encrypted storage; access isolation; permitted formats, size limits, quarantine policy; historical versions retained per approved policy | Client/lawyer/admin walkthrough + forbidden-access tests |
| UAT-06 | M1 accepted → contract → first ₽30k payment → POA | Verified payment webhook and exactly-once ledger, no stage advancement on forged/replayed callback | YooKassa sandbox traces + ledger |
| UAT-07 | M1 claim dispatch → statutory waiting → lawsuit → decision → ₽70k | Date guard and legal milestone proofs, blocked premature progression, no false client-success messages | Lawyer/admin UAT + audit |
| UAT-08 | M1 enforcement → recorded actual receipts → 10% fee → controlled closure | Success fee computed from receipts, webhook never auto-closes case, closure reason recorded | Payment and state-transition evidence |
| UAT-09 | New inquiry → M2 description → file attachment → slot selection/hold → payment → booking | Double booking prevented; expired hold releases slot; failed payment does not book | Two-client concurrency + provider sandbox |
| UAT-10 | M2 consultation → lawyer outcome → M1 handoff or closure | Handoff retains evidence, does not bypass legal consents; terminal case read-only | Lawyer/client UAT |
| UAT-11 | Client ↔ lawyer messages, notifications, history | Correct ordering, pagination, retry/outbox, role-scoped case visibility; no cross-client leaks | Evidence of denied access + delivery traces |
| UAT-12 | Staff admin and lawyer desktops | Distinct ACL/MFA requirements, assignment & SLA, work queues, documents, financial ledger, legal milestones | Browser screenshots + permission matrix |
| UAT-13 | Recover from Redis outage / Telegram unavailable / webhook replay | No silently lost writes; fail-closed when data cannot be persisted; outbox replay idempotent | Failure-injection logs |
| UAT-14 | PostgreSQL backup → simulated loss → restore | Encrypted document store, keys, DB, migrations, ledger and audit all recover consistently | Time-stamped restore evidence |
| UAT-15 | PII retention/deletion/consent withdrawal | Retention policy, legal holds, audit and owner-approved deletion procedure | Security/legal review |
| UAT-16 | No-launch readiness | Production secrets/HTTPS, health and ready, observability, alerting, rollback and restricted sandbox availability | Signed operator handover |

## Non-negotiable defects and dependencies

1. **Legal rules are a gate.** Formula inputs, official key-rate intervals, applicable moratoria and exclusions are not accepted without legal-owner approval. The simplified rate × days formula is *only* a preliminary calculation, not a verified production legal assessment.
2. **New object calculation:** the functional specification section 3.4 permits repeat calculation on a new object even while a client has an active case. Current `calc_start` blocks that. Implement a non-persistent preview or properly scoped new-case workflow without merging two unrelated objects into one case; this feature is **not accepted** until verified.
3. **Documents:** drafts #171/#173 (malware scanning and document derivatives) are not in `main`. Quarantine and secure-ingest tests must pass in the chosen runtime and must not be represented as shipped before merge.
4. **External receipts:** green CI on a GitHub runner does not prove Telegram, YooKassa, Timeweb, file-virus scanner, SMTP or actual lawyer acceptance.
5. **Old release evidence:** issue #185 points to an older code/migration head. Rebaseline all runbooks and proof on the final approved SHA; do not close this checklist based on dated CI screenshots.
6. **Scope:** third paid route or self-filing (#137), advanced policy editor (#133), preview (#136) and additional institutional products require product approval and independent integration review. They must not silently change the approved two-route MVP.

## Evidence bundle to archive before handover

```text
release/
  manifest.json                     # exact Git SHA, migration head, images digest, date, reviewer
  actions/                          # URLs and final statuses for every required workflow
  db/                               # migration and restore drill logs
  telegram/                         # consented redacted M1/M2 transcripts and screenshots
  payments/                         # redacted sandbox webhook, signed payload check, ledger proof
  documents/                        # encrypted upload, access-denied, malware/recovery proof
  legal/                            # signed rules/consent version, public URL, SHA-256
  observability/                    # health, alerts, failed/replayed deliveries
  uat/                               # client, lawyer, admin signatures and issue list
  operator/                          # runbook, rollback, ownership, no-launch switch
```

**Final admission:** Mark READY only when every mandatory UAT row and the legal, operational, and external proof above have traceable evidence with the same release SHA. Otherwise mark **NOT READY**, with a named blocker and owner. The code may be held ready for a future release without enabling public access.
