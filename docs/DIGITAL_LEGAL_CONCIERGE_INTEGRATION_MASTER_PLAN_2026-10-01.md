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

## Additional wave — web/email evidence capture and authenticity metadata

This wave strengthens digital evidence intake for disputes where important facts exist in web pages, developer portals or email correspondence.

### Browsertrix evidence capture — CONDITIONAL SIDECAR

Reference:

https://github.com/webrecorder/browsertrix-crawler

Use Browsertrix Crawler as an external evidence-capture tool when a case requires a reproducible rendered-web snapshot that simple PDF/screenshot export cannot preserve adequately.

Candidate uses:

- developer/customer portal state;
- public terms/offer pages;
- project status pages;
- published notices;
- public company/developer statements relevant to the case.

Capture package should retain, where available:

- source URL;
- captured_at;
- crawl/capture profile;
- WARC/WACZ or equivalent archive reference;
- rendered screenshot;
- page title/canonical URL;
- asset/request evidence;
- crawler/version;
- package checksum.

Browsertrix is evidence acquisition only. It does not decide that a web page is legally authoritative or that a captured statement is true.

### Browser evidence admission — ADOPT

Flow:

capture package -> checksum/virus/file admission -> metadata extraction -> evidence record -> reviewer classification -> timeline/legal-source link

A user or operator must identify why the page matters and which fact/event it supports.

Do not silently recrawl and replace old evidence. Every new capture is a new version/evidence item.

### Email evidence intake — ADOPT

Support raw email evidence as the preferred source rather than only screenshots.

Accepted source forms may include:

- RFC822/EML;
- provider-exported raw message;
- attachment set;
- human-readable derivative.

Store:

- source file checksum;
- Message-ID;
- From/To/Cc;
- Date header;
- subject;
- received-chain summary;
- attachment IDs/checksums;
- raw header preservation;
- parser/version;
- review status.

The original raw message remains immutable.

### DKIM verification — ADAPT

Candidate library/reference:

https://github.com/forwardemail/dkimpy

Use DKIM verification when the original raw message contains the required signature and headers.

Persist:

- signature selector/domain;
- verification status;
- verification time;
- DNS/key lookup evidence where practical;
- verifier/version.

A successful DKIM result shows that the message validates against the signing domain/key under the verification conditions. It does not by itself prove who physically authored the text, legal authority of the sender, or that every forwarded/screenshot copy is authentic.

### Email-to-Timeline / Evidence linking — ADOPT

Allow an admitted email to support:

- notice/request sent;
- response received;
- promised action/date;
- refusal;
- evidence request;
- settlement/negotiation step.

Timeline extraction may propose dates/entities from headers/text, but human/CaseService confirmation creates the authoritative event.

### Licensing boundary

Browsertrix Crawler is AGPL-licensed upstream. Treat it as a separately deployed evidence-capture service/CLI with explicit legal review rather than embedding its code into the application.

The verified forwardemail/dkimpy repository is MIT-licensed but has lower recent activity; pin and test the exact approved version before use.

### Additional acceptance

- raw evidence is retained unchanged;
- web capture identifies exact URL/time/tool/version/checksum;
- email derivatives trace to the original raw message;
- DKIM result is shown as cryptographic metadata, not a legal conclusion;
- no recrawl or reparsing silently overwrites prior evidence;
- captured content remains subject to normal case ACL, retention and legal hold.

**Sequencing:** secure file admission first -> raw email/web capture -> authenticity metadata -> timeline/source linkage -> evidence bundle export.

## Additional wave — argument/evidence graph and long-term signature trust

This wave makes the legal product materially more explainable and premium: not a folder of documents, but a navigable map of facts, claims, counterparty assertions and supporting evidence.

### Case Argument / Evidence Graph — ADOPT

Native node types may include:

- factual assertion;
- counterparty assertion;
- legal issue/claim;
- timeline event;
- evidence item/document/page;
- contract clause;
- legal source/version;
- deadline;
- calculation;
- requested remedy.

Relations may include:

- supports;
- contradicts;
- qualifies;
- derived-from;
- governed-by;
- disputed-by;
- supersedes;
- missing-evidence-for.

Every relation stores source, creator, review status and timestamps.

This graph represents reviewed reasoning. It does not predict a court result.

### Cytoscape.js Visual Graph — ADOPT

Reference: https://github.com/cytoscape/cytoscape.js

Use only as the interactive UI for:

- why this next action?;
- evidence supporting a claim;
- disputed facts;
- missing evidence;
- legal-source dependencies;
- timeline-to-document view.

The graph UI is rebuildable from canonical case records.

### AI-assisted Graph Suggestions — ADAPT

AI/extraction may propose:

- fact candidates;
- evidence links;
- contradiction candidates;
- duplicates;
- missing-evidence questions.

Nothing becomes reviewed case reasoning until a human/operator confirms it.

### PAdES / Long-Term Validation Policy — ADOPT

Extend the existing pyHanko verification pipeline.

Reference: https://github.com/MatthiasValvekens/pyHanko

Persist where available:

- signature/profile;
- certificate-chain validation;
- timestamp validation;
- revocation evidence;
- validation time;
- trust-store/policy version;
- long-term validation material status.

Distinguish:

- cryptographically valid now;
- long-term validation evidence available;
- incomplete trust/revocation evidence;
- invalid/unverifiable.

### Trust Policy Authority — ADOPT

Version:

- trust anchors;
- validation-time rules;
- revocation behavior;
- timestamp policy;
- verifier version.

Policy changes create new validation records rather than rewriting old history.

### Additional acceptance

- every graph edge resolves to canonical case/evidence IDs;
- AI cannot publish reviewed legal reasoning;
- visual graph is not authority;
- signature status identifies exact trust policy + verifier version;
- original signed PDF is immutable;
- cryptographic validity is not described as automatic legal effect.

**Sequencing:** Timeline/Evidence/Search -> argument graph -> graph UI -> AI suggestion queue; pyHanko verification -> trust policy -> LTV/revalidation history.

## Premium commercial wave — secure settlement and negotiation room

This wave extends a legal case from preparation into a controlled negotiation workflow while preserving legal review and evidence integrity.

### Settlement Room Authority — ADOPT

Create a case-scoped secure room with:

- authorised parties/representatives;
- negotiation status;
- offer/counteroffer;
- non-monetary terms;
- payment schedule;
- deadline/expiry;
- attached evidence/draft;
- response;
- accepted/rejected/withdrawn state;
- audit trail.

The room is not public chat and does not replace normal case evidence.

### Versioned Offer / Counteroffer — ADOPT

Each offer version stores:

- amount/terms;
- currency;
- payment timing;
- conditions;
- release/waiver wording reference;
- cost/penalty calculation snapshot where relevant;
- evidence/legal-source links;
- validity/expiry;
- proposer;
- status.

Offers are immutable once issued; a changed proposal is a new version.

### Scenario Calculator — ADOPT

Provide deterministic comparison of settlement scenarios, e.g.:

- offered amount;
- timing/installments;
- calculated claim components;
- documented costs;
- time value/fees if explicitly configured;
- net cash schedule.

This is a scenario tool, not a court-outcome predictor or recommendation to accept/reject.

### Negotiation Evidence Boundary — ADOPT

Support explicit classifications:

- internal strategy note;
- shareable offer;
- counterparty response;
- signed/final settlement document.

Internal notes never enter counterparty export by accident.

### Draft Settlement Document — ADAPT

Use existing document-assembly and legal-source controls to generate a versioned draft from accepted structured terms.

The draft requires legal/user review.

### E-sign / Finalisation — ADAPT

Reuse the existing Documenso/provider boundary or accepted signature workflow:

accepted structured terms -> final document -> signatures -> verification -> case timeline -> payment/obligation schedule

Settlement state becomes final only according to configured confirmation/signature rules.

### Obligation Tracking — ADOPT

After settlement:

- payment installment;
- document/action obligation;
- due date;
- completion evidence;
- missed/default status;
- next action.

This feeds the existing Deadline and Case Projection authorities.

### Additional acceptance

- offer versions cannot be silently edited after issue;
- internal notes cannot leak to the other party;
- calculator is reproducible from a case snapshot;
- system does not predict judicial outcome or decide whether to settle;
- final document resolves to exact accepted terms;
- post-settlement obligations appear in canonical timeline/deadline state.

**Sequencing:** Timeline + Calculation + Evidence + Secure Documents -> Settlement Room -> offers -> draft -> signature -> obligation tracking.

**Commercial framing:** this extends the product from "prepare my claim" to a controlled end-to-end resolution workspace.

## Premium enterprise wave — external counsel workspace and filing-readiness gate

This wave makes a case portable between self-service, in-house review and external counsel without losing evidence lineage.

### External Counsel Workspace — ADOPT

Create a case-scoped collaboration role/workspace for:

- client;
- authorised lawyer/counsel;
- expert/consultant;
- internal operator.

Counsel accesses only explicitly shared case scope.

### Portfolio pattern source — REUSE/ADAPT

Reuse controlled collaboration patterns from:

https://github.com/PetrFedin/chat

Useful patterns:

- scoped membership;
- task/evidence relationships;
- review/return/resubmit;
- source-linked comments;
- structured request/approval;
- durable audit/outbox.

No shared database.

### Counsel Review Request — ADOPT

Store:

- case;
- question/scope;
- reviewer;
- due date;
- argument/evidence nodes;
- document versions;
- requested output;
- status;
- response/advice;
- private/client-visible classification.

### Filing Readiness Gate — ADOPT

Versioned checklist may include:

- party/identity complete;
- legal basis/source versions identified;
- claim/calculation snapshot frozen;
- required evidence admitted;
- signature/verification state known;
- procedural deadline confirmed;
- exhibits/bundle present;
- unresolved contradiction flagged;
- counsel/user approval recorded.

READY means package readiness, not successfully filed.

### Counsel Comment / Redline Linkage — ADOPT

Comments/redlines reference exact:

- document version;
- page/section;
- argument/evidence node;
- checklist item.

Document revision preserves old review history.

### Case Handoff Package — ADOPT

Generate:

- case summary;
- timeline;
- argument/evidence graph;
- calculations;
- current deadline;
- evidence manifest;
- document versions;
- unresolved issues;
- checksum/version.

Reuse existing evidence-bundle machinery.

### Additional acceptance

- counsel access is case-scoped/revocable;
- private counsel notes never leak into settlement room;
- comments link to immutable source versions;
- readiness is reproducible from checklist/rule version;
- READY does not imply filed;
- handoff package reconciles to evidence checksums.

**Sequencing:** Evidence/Timeline/Argument Graph -> counsel membership -> structured review -> filing gate -> handoff package -> future filing adapter.

**Commercial framing:** seamless escalation from consumer self-service to professional legal workflow.

## Moat wave — multi-case legal operations and portfolio claims workspace

This wave opens a B2B market for law firms, associations, property-owner groups and corporate legal teams managing many similar cases.

### Case Portfolio Authority — ADOPT

Create:

- portfolio;
- organisation/team;
- case membership;
- common legal issue/template;
- shared evidence/source;
- jurisdiction/process;
- owner;
- status.

Every individual case remains legally and evidentially independent.

### Common Issue / Template Authority — ADOPT

Version reusable:

- legal issue definition;
- document template;
- evidence request;
- calculation rule;
- filing-readiness checklist;
- settlement clause template.

A template update never rewrites already-issued case documents.

### Shared Evidence Library — ADOPT

Allow portfolio-level sources such as:

- common contract form;
- developer/public statement;
- regulatory/legal source;
- expert report;
- standard correspondence.

Individual case links explicitly state how the shared source applies.

### Bulk Intake / Normalisation — ADOPT

Controlled workflow:

source files/table -> case candidates -> field validation -> duplicate detection -> preview -> explicit case creation

No bulk import bypasses identity/evidence admission.

### Portfolio Operations Cockpit — ADOPT

Show:

- cases by stage;
- deadlines;
- missing evidence;
- filing readiness;
- settlement state;
- counsel review;
- common blockers;
- aggregate claimed/settled amounts where methodology permits.

Always distinguish aggregate portfolio reporting from individual legal outcome.

### Batch Drafting with Individual Commit — ADOPT

Generate draft notices/requests from common templates, but each case has:

- individual source data;
- individual calculation;
- exact document version;
- individual approval;
- individual send/filing state.

No "mass send" without case-level validation.

### Pattern Intelligence — ADOPT

Detect operational patterns:

- recurring missing evidence;
- common counterparty response;
- repeated clause;
- stage delay;
- settlement term frequency.

This is process intelligence, not prediction of court outcome.

### Additional acceptance

- one case cannot inherit another person's private evidence;
- shared sources are separately permissioned;
- template changes do not alter old case history;
- every batch-generated artifact has individual case validation;
- portfolio aggregates identify methodology and denominator;
- no class/collective-action legal status is implied automatically.

**Sequencing:** single-case authority + Counsel/Settlement -> portfolio -> shared sources/templates -> bulk intake -> portfolio cockpit -> controlled batch operations.

**Commercial framing:** expands from consumer legal concierge to LegalOps / law-firm portfolio software without sacrificing case-level evidence integrity.

## Platform economics wave — Embedded Legal API and white-label partner distribution

This wave opens partner distribution through real-estate platforms, banks, insurers, property services, associations and legal-service channels.

### Partner Organisation Authority — ADOPT

Create:

- partner;
- allowed products/jurisdictions;
- branding;
- auth/client credentials;
- intake scopes;
- case visibility;
- pricing/commercial plan reference;
- support/escalation;
- privacy/retention policy;
- status.

Partner never receives case data outside explicit user/contract scope.

### Embedded Intake API — ADOPT

Partner can create a case candidate with only approved minimum fields:

- issue/product type;
- relevant dates;
- property/contract references;
- user identity/contact with consent;
- source partner;
- initial documents/evidence references.

Flow:

partner intake -> user verification/consent -> validation -> case created -> canonical CaseService

No partner can silently open a binding legal action for the user.

### Calculation / Eligibility API — ADOPT

Expose bounded deterministic services where legally/product-appropriate:

- scenario calculation;
- missing information;
- process/readiness state;
- next required evidence.

Response must identify rule/version and must not be presented as a guaranteed legal outcome.

### Evidence Upload / Status API — ADOPT

Partner may, within scope:

- upload/document handoff;
- fetch case stage;
- fetch requested evidence checklist;
- receive status webhooks.

Raw evidence still goes through the normal secure admission/encryption pipeline.

### White-label Experience — ADOPT

Partner may embed:

- co-branded intake;
- calculator;
- evidence checklist;
- status surface.

Core legal logic, evidence and authority remain common; no per-partner legal-code forks.

### Partner Webhooks — ADOPT

Events:

- consent completed;
- case admitted;
- evidence missing;
- stage changed;
- counsel review requested;
- settlement/final state changed where shareable.

Signed, idempotent and scoped.

### Embedded Legal Boundary — REQUIRED

The API/product must clearly state:

- what is automated information/workflow;
- when counsel/legal professional review is involved;
- jurisdiction/scope;
- unsupported/out-of-scope issues.

Do not let a distribution partner re-label the product as a legal guarantee.

### Additional acceptance

- user consent/identity gates case creation;
- partner sees only scoped case projection;
- calculator outputs exact rule/version;
- evidence uses normal secure admission;
- white-label branding cannot change legal rules;
- revoking partner access preserves user case/history.

**Sequencing:** Portfolio LegalOps + Counsel/Settlement + stable APIs -> partner org -> embedded intake -> status/evidence API -> white-label -> commercial distribution.

**Commercial framing:** converts the concierge from a direct channel into embeddable LegalTech infrastructure with partner-led acquisition.

## Defensibility wave — Case Readiness Standard and professional service trust network

This wave creates a portable, explainable readiness protocol for case handoff and a professional-network layer without ranking lawyers by outcomes.

### Legal Case Readiness Standard — ADOPT

Define a versioned readiness profile with components such as:

- verified party/identity fields;
- contract/property facts;
- deadline/procedure state;
- calculation snapshot;
- evidence completeness;
- evidence integrity/signature state;
- legal-source versions;
- argument/evidence graph completeness;
- unresolved contradictions;
- filing/settlement document state;
- counsel review status.

The standard describes package readiness, not probability of legal success.

### Readiness Manifest — ADOPT

Generate a machine-readable manifest:

- case ID;
- standard version;
- component states;
- missing items;
- evidence bundle checksum;
- calculation version;
- deadline snapshot;
- generated_at;
- reviewer/approval state.

This can travel with the Counsel Handoff Package.

### Scoped Readiness Credential — ADAPT

Where useful, issue a verifiable attestation:

"Case package met Readiness Standard X at time T"

It must identify scope and expiry/revalidation conditions.

It does not certify legal merit or court admissibility.

### Professional Service Network — ADOPT

Counsel/experts/providers may have factual service profiles:

- identity/organisation verified;
- jurisdiction/practice scope;
- available service type;
- structured-review integration;
- response SLA offered;
- completed platform review count;
- current credential/integration status.

Do not publish win rates or opaque lawyer rankings.

### Service Reliability Dimensions — ADOPT

Internal/partner matching may use factual process metrics:

- accepted review requests;
- median response time;
- overdue rate;
- structured-review completion;
- availability status.

New providers remain no-history, not low-ranked.

### Partner Credential — ADOPT

Possible scoped statuses:

- Counsel Workspace Integrated;
- Structured Review Verified;
- Filing Package Workflow Verified;
- Settlement Workflow Integrated.

These are platform workflow attestations, not professional licensure.

### Additional acceptance

- readiness profile never predicts outcome;
- manifest binds to exact case/evidence/calculation versions;
- professional licence/qualification data remains external/source-attributed;
- no hidden provider ranking by settlement/court outcome;
- new providers are not penalised for lack of history;
- credential verification exposes minimum necessary data.

**Sequencing:** Filing Readiness + Counsel Workspace + Portfolio LegalOps -> readiness standard/manifest -> provider service profiles -> workflow credentials -> matching/verification.

**Moat:** standardised case-readiness manifests reduce switching/handoff friction while the provider network compounds structured service reliability evidence.

