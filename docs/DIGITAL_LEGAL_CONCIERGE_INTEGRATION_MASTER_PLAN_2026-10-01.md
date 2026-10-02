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

## Additional wave — signature evidence, release provenance and tracing

### pyHanko digital-signature verification — ADOPT

Reference: https://github.com/MatthiasValvekens/pyHanko

Add a bounded verification pipeline for PDFs that already contain digital signatures.

Flow:

`admitted source PDF -> signature discovery -> cryptographic verification -> certificate/path/timestamp/revocation evidence -> verification record -> case/document UI`

Persist:

- signature field/index;
- signer certificate subject/issuer identifiers;
- digest/signature algorithm;
- signing/timestamp time where available;
- validation status;
- trust/revocation evidence status;
- validator version;
- source document checksum.

Important distinction: cryptographic verification can prove that a signature validates against a certificate/trust policy; it does **not** by itself determine the legal effect, authority of the signer or admissibility of the document. Those remain legal/business interpretation.

Never rewrite the original signed PDF before verification.

### Sigstore Cosign release signing — ADOPT/CI

Reference: https://github.com/sigstore/cosign

Sign container/release artefacts after the existing security/test gates.

Target chain:

`source SHA -> CI tests/security scans -> image/build artefact -> SBOM/attestation -> Cosign signature -> deployment`

Production deployment should be able to identify the exact signed artefact digest.

Cosign proves artefact provenance/integrity; it does not replace application-level document signatures or case audit.

### OpenTelemetry Python — ADOPT

Reference: https://github.com/open-telemetry/opentelemetry-python

Trace privacy-safe operational paths:

`request -> auth -> CaseService command -> DB/Redis -> document processor -> provider -> response`

Useful spans:

- document admission;
- OCR/Docling;
- timeline rebuild;
- payment/consultation provider;
- scheduled retention/deletion job;
- search indexing.

Do not put extracted legal text, document bodies, authentication tokens or PII into span attributes/events.

### Acceptance extension

- signed PDF verification is reproducible from original checksum + trust policy + validator version;
- invalid/unknown trust is represented explicitly rather than coerced into valid/invalid legal meaning;
- deployed release digest has a verifiable signature;
- traces correlate processing failures without leaking document contents.

**Sequencing:** PDF signature verification follows secure document admission; Cosign follows Trivy/SBOM/release integrity; OpenTelemetry can be added incrementally around the current CaseService and processors.

## Additional wave — procedural deadlines, evidence bundles and legal-source versioning

This wave adds operational legal controls around the existing CaseService and evidence authority. It must not turn the product into an autonomous legal-decision system.

### Procedural Deadline Authority — ADOPT

Calendar reference: https://github.com/workalendar/workalendar

Create native deadline records linked to a case/event/legal basis:

- deadline ID/type;
- triggering event/date;
- calculation rule/version;
- jurisdiction/calendar;
- business-day/holiday rule;
- calculated due date;
- manual override + reason;
- source/legal-basis reference;
- responsible person;
- reminder/escalation state;
- completed/missed status.

Flow:

timeline event -> applicable deadline rule -> calculated candidate -> human/CaseService confirmation -> reminders -> completion evidence

Workalendar can provide business-day/holiday mechanics where its jurisdiction coverage is applicable, but the application must version the exact calendar/rule used.

A calculated date is assistance. It must remain possible to override it with an explicit reason because contractual/judicial rules can differ from generic working-day calendars.

### Court / Counsel Evidence Bundle — ADOPT

Create a deterministic export package for an authorised case snapshot:

case snapshot -> evidence selection -> chronology -> document manifest -> checksums -> generated index -> export bundle

Bundle manifest should include:

- case ID + snapshot version;
- generated_at;
- included document IDs;
- original checksums;
- signature-verification status where present;
- timeline event references;
- extracted-page anchors;
- redaction status;
- bundle checksum.

The bundle is a derived export. It does not replace encrypted originals or alter retention/legal-hold state.

### Legal Source Registry — ADOPT

Create versioned references for laws, contract clauses, court/authority materials and internal templates used by calculations/explanations.

Store:

- source type;
- title/identifier;
- jurisdiction;
- effective-from/to;
- source URL/reference;
- retrieved/verified date;
- text excerpt/hash where legally permitted;
- supersedes/superseded_by;
- reviewer.

Case projections and generated drafts should reference the exact source version used.

Do not silently rewrite historical case reasoning when a law/template changes; new analysis gets a new source version.

### Document Redline / Version Comparison — ADOPT

Use a bounded text/structure comparison layer for:

- contract version vs amendment;
- claim draft revisions;
- developer/counterparty response vs prior version;
- generated document before/after counsel edits.

Persist comparison metadata:

- left/right document versions;
- extraction versions;
- diff engine/version;
- created_at;
- reviewer notes.

The diff is a navigation aid. Legal meaning of a changed clause remains a human/legal interpretation.

### Deadline + Timeline integration

Deadline state should appear on My Case Projection & Next Action as:

- next due item;
- source trigger;
- calculated/confirmed date;
- confidence/status;
- required action;
- supporting source.

Never state an unconfirmed procedural date as guaranteed where the rule depends on facts or court discretion.

### Additional acceptance

- deadline calculations are reproducible from trigger + rule/calendar version;
- overrides are explicit/audited;
- an evidence bundle can be regenerated from a case snapshot and reconciles to source checksums;
- historical legal-source versions remain available;
- redline never modifies original evidence;
- reminders use existing notification/scheduler authority.

**Sequencing:** Legal Timeline first -> Deadline Authority -> evidence bundles/source registry -> redline/UI. pyHanko/OCR/search remain subordinate evidence processors.

## Additional wave — archival PDF conformance and preservation export

This wave improves long-term evidentiary portability without changing the encrypted original-document authority.

### PDF/A conformance validation — CONDITIONAL SIDECAR

Reference: https://github.com/veraPDF/veraPDF-library

Use veraPDF as an external validation process for **derived archival PDFs**, not as an in-process library by default.

Flow:

original admitted PDF -> immutable evidence storage -> optional normalized/archival derivative -> veraPDF validation -> conformance report -> evidence metadata

Persist:

- source/derivative document ID;
- checksum;
- intended PDF/A profile;
- validator version;
- validation result;
- failed rule IDs;
- validation timestamp.

Do not convert every source PDF to PDF/A automatically. A digitally signed PDF must first preserve/verify the original signature evidence because conversion can invalidate signatures or change bytes.

### Archival Derivative Policy — ADOPT

Create an explicit policy deciding when a derivative is appropriate:

- scanned evidence after OCR;
- generated claim/letter;
- counsel/court evidence bundle index;
- long-term reference copy;
- signed original: retain original unchanged, derivative only as secondary copy.

Every derivative stores source link + processor/version + checksum.

### Preservation Export Manifest — ADOPT

Extend the Court/Counsel Evidence Bundle with a preservation-oriented manifest:

- original/derivative relationship;
- original checksum;
- derivative checksum;
- OCR/normalization processor;
- digital-signature verification state;
- PDF/A validation state;
- retention/legal-hold state;
- export/bundle version.

This makes it possible to distinguish legal original, readable derivative and archival-normalized copy.

### License/deployment boundary

veraPDF library is GPL-licensed in the verified upstream repository. Treat it as a replaceable CLI/service validation boundary unless a separate legal review approves another integration mode.

### Additional acceptance

- original signed/unsigned evidence is never overwritten;
- PDF/A validation is reproducible from exact derivative checksum + validator version;
- failure to meet PDF/A is visible and does not falsely mark the original evidence invalid;
- preservation export clearly identifies which file is the original legal evidence;
- licensing boundary is documented in deployment architecture.

**Sequencing:** secure admission + signature verification first -> archival derivative policy -> veraPDF validation -> preservation manifest.

