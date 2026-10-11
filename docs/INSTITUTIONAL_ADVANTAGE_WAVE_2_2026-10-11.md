# digital-legal-concierge — Legal Outcome Evidence Network

Status: **PROPOSED, documentation only**. Date: 2026-10-11. This roadmap complements existing canonical plans and does not represent shipped functionality.

## Strategic enhancements
1. P0: source-cited legal fact extraction with document/page anchors, confidence, contested-fact handling and human sign-off.
2. P0: verified action receipts distinguish draft, sent, delivered, accepted and legally effective states; provider evidence required.
3. P1: policy/versioned legal calculation engine with reproducible effective-date scenarios and jurisdiction gates.
4. P1: professional credential verification and conflict checks before a lawyer may accept a case.
5. P2: institutional case referral with client consent, confidentiality partitions and transparent commercial relationships.
6. P2: privacy-preserving aggregate dispute intelligence with small-cohort suppression and retention enforcement.

## Common implementation contract
- Inspect and preserve existing domain authorities, schemas, tasks and existing roadmap before implementation. Do not build a parallel source of truth.
- Every proposal becomes a bounded PR with owner, dependency graph, acceptance criteria, CI and rollback procedure.
- For recommendations, record source, freshness, confidence, effect estimate, human approval and outcome; distinguish measured impact from hypothetical scenarios.
- Introduce issuer/verifier credentials only where independently useful; support issuance, expiry, suspension/revocation, key rotation and privacy-preserving verification.
- Apply least privilege, per-tenant access, audit retention and independent negative-path tests.
- Demand functional device/browser proof, golden-path replay, exact-SHA CI and recoverability before production admission.
- Pilot with baseline and outcome metrics; no fabricated ROI, partner agreements, customer evidence or production readiness.

## Reference standards (evaluate applicability, do not assume compliance)
- W3C Verifiable Credentials 2.0: https://www.w3.org/TR/vc-data-model-2.0/
- OpenID4VC specifications: https://openid.net/wg/digital-credentials-protocols/specifications/
- NIST AI Risk Management Framework: https://www.nist.gov/itl/ai-risk-management-framework
- OWASP GenAI Security Project: https://genai.owasp.org/

## Execution gate
Discovery -> architecture boundary review -> single vertical slice -> adversarial tests -> human acceptance -> measured pilot -> commercial rollout. Missing external credentials, rights, legal applicability, real-world evidence, or CI is an explicit BLOCKED state.
