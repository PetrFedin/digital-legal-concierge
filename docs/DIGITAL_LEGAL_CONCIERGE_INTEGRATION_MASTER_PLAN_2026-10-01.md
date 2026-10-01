# Digital Legal Concierge — Integration Master Plan

**Document:** `docs/DIGITAL_LEGAL_CONCIERGE_INTEGRATION_MASTER_PLAN_2026-10-01.md`  
**Status:** PLANNED  
**Date:** 2026-10-01

## Purpose

Canonical plan for strengthening the 214-FZ legal concierge without replacing the current CaseService, encryption, retention, payment or audit authorities.

## Existing authority to preserve

The repository already has controlled case statuses, Redis FSM durability, encrypted document storage, key rotation, legal hold/retention behavior, deterministic penalty calculation, audit/security controls and consultation/payment flows.

## Integration disposition

| Capability | Source | Decision |
|---|---|---|
| Malware scanning | ClamAV | ADOPT/SIDECAR |
| PDF repair/sanitise | pikepdf | ADOPT |
| OCR | OCRmyPDF | ADOPT when scan needs OCR |
| Document structure extraction | Docling | ADOPT |
| Generic parsing | Unstructured | ADAPT/fallback |
| PII detection/redaction | Presidio | ADAPT |
| Secrets/key infrastructure | OpenBao | ADOPT/SIDECAR |
| Legal Timeline | native | ADOPT |
| Search | OpenSearch | SIDECAR/ADAPT |
| Policy as code | OPA | ADAPT |
| Relationship permissions | OpenFGA | ADAPT |
| Document assembly | docassemble patterns | ADAPT |
| E-sign | Documenso | SIDECAR/ADAPT |
| Workflow engine | Flowable | REFERENCE |
| EDMS | Mayan EDMS | REFERENCE |
| Tamper evidence | immudb | DEFER/ADAPT |
| API fuzz/property checks | Schemathesis | ADOPT/CI |
| Container/security scan | Trivy | ADOPT/CI |

## Phase 0 — Preserve evidence and encryption boundaries

No parser/OCR/search component may receive a document before admission policy allows it.

Canonical ingest order:

`upload -> malware scan -> file type/PDF sanitise -> encrypted source storage -> OCR/structure derivative -> extracted text/index -> case evidence link`

Original file is immutable.

Derived files/text have their own checksum and provenance.

## Phase 1 — Secure document admission

### ClamAV

Run outside the application process. Quarantine upload until scan is clean.

Timeout/unavailable scanner is fail-closed for production admission.

### pikepdf

Use for:

- PDF structural validation;
- safe repair where possible;
- metadata cleanup;
- page count/encryption checks.

Never silently modify the only copy of an uploaded legal document.

## Phase 2 — OCR and structure

### OCRmyPDF

Use only when the document lacks usable text. Store OCR PDF as derivative.

### Docling

Primary structured extraction for:

- headings;
- tables;
- pages;
- reading order;
- document sections.

### Unstructured

Fallback/reference for file types or extraction patterns Docling does not cover well.

Extraction must carry page/source anchors.

## Phase 3 — PII/redaction

Use Presidio as a detection assistant.

Flow:

`extracted text -> detected entities -> review/redaction policy -> redacted derivative`

Do not auto-delete or irreversibly redact source evidence.

Use case-specific allow/deny rules for names, passport-like identifiers, addresses, payment information.

## Phase 4 — Legal Timeline Authority

Create native timeline events:

- contract/signing;
- payment;
- promised handover;
- actual handover;
- claim;
- response;
- inspection/defect;
- court/procedural event;
- consultation;
- payment/refund.

Every timeline event links to supporting evidence and case status.

This timeline should power "My Case Projection & Next Action", but projection remains labelled as calculation/guidance, not a judicial outcome prediction.

## Phase 5 — Search

Use OpenSearch as a rebuildable index over authorised extracted text.

Search result must return:

- case/document ID;
- page/section anchor;
- matching excerpt;
- user access check performed by application.

OpenSearch never decides access.

## Phase 6 — Policy and relationship authorization

### OPA

Use for stable policy rules such as:

- retention;
- deletion prerequisites;
- document export;
- privileged action prerequisites.

### OpenFGA

Use only if case/user/operator/lawyer relationships outgrow current role checks.

Application remains source for user/case relationships; OpenFGA evaluates projected tuples.

Do not deploy both simply for fashion. Adopt each only where its policy model materially simplifies a real control.

## Phase 7 — Document assembly and signature

### docassemble patterns

Use templates/question logic to assemble controlled claim/letter drafts from case facts.

Generated document must expose source fields and version.

### Documenso

Optional signing provider/sidecar.

Signed document comes back as provider evidence; case/document lifecycle remains in Legal Concierge.

## Phase 8 — Secrets

OpenBao should hold production secrets/key material where operationally viable.

Application receives least-privilege references/credentials.

Do not put encryption master keys in repository, logs or user sessions.

## Phase 9 — Tamper-evident external anchor

immudb is deferred.

If adopted, anchor digests of selected audit/document events. Do not duplicate full case content or sensitive documents into it.

Purpose is independent tamper evidence, not a second case database.

## Phase 10 — References only

- **Flowable:** study BPMN/escalation patterns; do not replace current CaseService state graph.
- **Mayan EDMS:** study document lifecycle/indexing UX; do not create a second document repository.

## Phase 11 — CI/security gates

### Schemathesis

Generate API property/fuzz cases from OpenAPI and add auth/state mutation invariants.

### Trivy

Scan images/dependencies/IaC in release pipeline.

Security scan failure policy must be documented, not silently ignored.

## Prohibited

Do not:

- parse before malware/admission checks;
- overwrite original evidence;
- let OpenSearch authorize users;
- let OPA/OpenFGA own case data;
- let docassemble/Documenso mutate case state directly;
- create a second workflow authority in Flowable;
- index plaintext outside approved security boundary;
- claim case outcome certainty from timeline/projected next action.

## Suggested issue order

1. DLC-INT-00 Secure ingest quarantine.
2. DLC-INT-01 pikepdf/OCR derivatives.
3. DLC-INT-02 Docling structured extraction.
4. DLC-INT-03 Presidio review/redaction.
5. DLC-INT-04 Legal Timeline.
6. DLC-INT-05 Search with page anchors.
7. DLC-INT-06 Policy/relationship authorization.
8. DLC-INT-07 Document assembly/signing.
9. DLC-INT-08 OpenBao secrets.
10. DLC-INT-09 Trivy/Schemathesis gates.
11. DLC-INT-10 Optional tamper anchor.

**Implementation instruction:** every external component must remain subordinate to the encrypted case/evidence authority.
