# v37 E2E persona test matrix

This matrix converts the approved M1/M2 product flows into an executable acceptance pass for three real personas: client, administrator and lawyer. It complements the source walkthrough in `docs/v37-persona-e2e-walkthrough.md`.

Status legend:

- `SOURCE_OK` — the current branch contains a source-level path/guard for the scenario.
- `LIVE_REQUIRED` — source looks complete, but Telegram/Redis/provider/browser/database interaction must still be executed in staging.
- `BLOCKED_INFRA` — execution is currently blocked by external infrastructure.
- `FIXED_V37` — a concrete dead end found during this audit was fixed in v37.

Evidence rule: an E2E row is accepted only when the resulting database state, audit/history event and visible next action all agree. A pretty screen alone is not acceptance.

## A. Client — entry, recovery and navigation

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| C-001 | First `/start` | Home shows calculator and legal-help entry; no case is fabricated | SOURCE_OK / LIVE_REQUIRED |
| C-002 | `/menu` after partial calculator input | Current calculator draft is preserved | SOURCE_OK / LIVE_REQUIRED |
| C-003 | Home during partial calculator input | Draft pauses; arbitrary text on Home is not consumed by stale FSM | SOURCE_OK / LIVE_REQUIRED |
| C-004 | Resume calculator draft | First incomplete calculator step opens with prior values retained | SOURCE_OK / LIVE_REQUIRED |
| C-005 | Restart calculator draft | Destructive reset requires explicit confirmation | SOURCE_OK / LIVE_REQUIRED |
| C-006 | Unknown callback from old Telegram message | No mutation; current safe state is rendered and callback spinner is closed | SOURCE_OK / LIVE_REQUIRED |
| C-007 | Unknown free text outside FSM | Bot explains available next action instead of silently ignoring message | SOURCE_OK / LIVE_REQUIRED |
| C-008 | Unknown callback during active FSM | Current input is retained; user can continue/cancel | SOURCE_OK / LIVE_REQUIRED |
| C-009 | Rapid repeated taps | Flood control throttles without duplicate case/payment/consultation | SOURCE_OK / LIVE_REQUIRED |
| C-010 | Start new calculator while an active case exists | Second active case is not created; user is returned to current case | SOURCE_OK / LIVE_REQUIRED |
| C-011 | Generic Back on a non-explicit flow | Safe recovery occurs; true previous-screen history is not fully implemented yet | P2 GAP |
| C-012 | Client returns after process restart | Redis-backed FSM/session must restore the expected live step | LIVE_REQUIRED |

## B. Client — calculator and route choice

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| C-020 | Valid price/date, object not transferred | Preliminary amount and delay are stored; wording is not a legal guarantee | SOURCE_OK / LIVE_REQUIRED |
| C-021 | Valid price/date, object transferred | Actual transfer date becomes calculation end date | SOURCE_OK / LIVE_REQUIRED |
| C-022 | Invalid money input | Error explains format; prior values remain | SOURCE_OK / LIVE_REQUIRED |
| C-023 | Invalid date format | Error explains DD.MM.YYYY; prior values remain | SOURCE_OK / LIVE_REQUIRED |
| C-024 | Planned transfer date in future | Standard calculation does not fake a claim; client can correct or move to M2 | SOURCE_OK / LIVE_REQUIRED |
| C-025 | Client does not know price | Standard calculator stops and safely offers M2 | SOURCE_OK / LIVE_REQUIRED |
| C-026 | Client does not know transfer date | Standard calculator stops and safely offers M2 | SOURCE_OK / LIVE_REQUIRED |
| C-027 | Old calculation result button pressed after case changed | Snapshot/provenance guard prevents mutation of newer case | SOURCE_OK / LIVE_REQUIRED |
| C-028 | Client postpones after calculation | Calculation/case remains accessible without forcing M1 or M2 mutation | SOURCE_OK / LIVE_REQUIRED |

## C. Client — M1 standard recovery

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| C-M1-001 | Choose M1 after calculation | Case enters client-decision/consent boundary; contract/payment do not open | SOURCE_OK / LIVE_REQUIRED |
| C-M1-002 | Accept personal-data consent | Consent fact/date are stored for exact owned case | SOURCE_OK / LIVE_REQUIRED |
| C-M1-003 | Decline personal-data consent | Documents are not opened; existing calculation is not deleted | SOURCE_OK / LIVE_REQUIRED |
| C-M1-004 | Press stale consent button from old message | No newer case is changed | SOURCE_OK / LIVE_REQUIRED |
| C-M1-005 | Upload valid DDU | File is attached to exact case, inspected/encrypted, visible to staff | SOURCE_OK / LIVE_REQUIRED |
| C-M1-006 | Upload wrong/unsafe/oversize document | File is rejected/quarantined according to security policy; case remains recoverable | SOURCE_OK / LIVE_REQUIRED |
| C-M1-007 | Upload replacement version | New version becomes current; history remains | SOURCE_OK / LIVE_REQUIRED |
| C-M1-008 | Finish upload with no usable documents | System must not pretend lawyer review is ready | SOURCE_OK / LIVE_REQUIRED |
| C-M1-009 | Finish valid document set | Client sees “documents sent/received”, not a false claim that lawyer already reviewed them | SOURCE_OK / LIVE_REQUIRED |
| C-M1-010 | Lawyer requests missing/replacement documents | Client receives precise blocker and upload action | SOURCE_OK / LIVE_REQUIRED |
| C-M1-011 | Lawyer accepts case | Contract stage opens only after legal review decision | SOURCE_OK / LIVE_REQUIRED |
| C-M1-012 | Lawyer rejects M1 | Client receives reason/follow-up and can choose M2 or closure without dead end | SOURCE_OK / LIVE_REQUIRED |
| C-M1-013 | Contract current version opened | Exact document/version is shown to client | SOURCE_OK / LIVE_REQUIRED |
| C-M1-014 | Client confirms stale contract version | No 30k obligation is opened from stale version | SOURCE_OK / LIVE_REQUIRED |
| C-M1-015 | Client confirms current contract version | First payment becomes eligible | SOURCE_OK / LIVE_REQUIRED |
| C-M1-016 | 30k payment succeeds | Provider/webhook confirmation opens POA stage exactly once | SOURCE_OK / LIVE_REQUIRED |
| C-M1-017 | 30k payment fails | Client gets retry/current-case recovery; POA does not open | SOURCE_OK / LIVE_REQUIRED |
| C-M1-018 | Duplicate 30k webhook | No duplicate stage/payment side effect | SOURCE_OK / LIVE_REQUIRED |
| C-M1-019 | Late payment on stale 30k link | Case is not rolled backward; payment goes to controlled review/refund path | SOURCE_OK / LIVE_REQUIRED |
| C-M1-020 | Client says POA is ready | Only client readiness is recorded; receipt is not fabricated | SOURCE_OK / LIVE_REQUIRED |
| C-M1-021 | Lawyer confirms POA received | Claim preparation becomes eligible | SOURCE_OK / LIVE_REQUIRED |
| C-M1-022 | Claim prepared but not sent | 30-day timer does not start | SOURCE_OK / LIVE_REQUIRED |
| C-M1-023 | Lawyer records claim sent | Audited sent date starts 30-calendar-day wait | SOURCE_OK / LIVE_REQUIRED |
| C-M1-024 | Try court before 30 days | Court transition is refused with safe wait state | SOURCE_OK / LIVE_REQUIRED |
| C-M1-025 | 30 days elapsed | Lawyer can open court stage | SOURCE_OK / LIVE_REQUIRED |
| C-M1-026 | Try 70k payment without court evidence | Obligation is not opened | SOURCE_OK / LIVE_REQUIRED |
| C-M1-027 | Court evidence recorded, 70k due | Second payment becomes eligible | SOURCE_OK / LIVE_REQUIRED |
| C-M1-028 | 70k payment succeeds/fails/duplicates | Enforcement opens only after valid confirmation; failures are recoverable | SOURCE_OK / LIVE_REQUIRED |
| C-M1-029 | Lawyer records recovered amount | Actual recovered sum is audited; success-fee obligation is server-created | SOURCE_OK / LIVE_REQUIRED |
| C-M1-030 | Client presses stale success-fee button before obligation exists | Client cannot manufacture financial stage | SOURCE_OK / LIVE_REQUIRED |
| C-M1-031 | Success fee succeeds | Case closes only after confirmed final financial event | SOURCE_OK / LIVE_REQUIRED |
| C-M1-032 | Closed M1 case | My Case/Documents/Payments/History are read-only archive; old mutating buttons do nothing | SOURCE_OK / LIVE_REQUIRED |

## C2. Client — M1 self-filing package

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| C-SF-001 | Eligible client chooses package after saved calculation | Same Case remains route M1 with service mode `SELF_FILING_PACKAGE`; no M3 is created | SOURCE_OK / LIVE_REQUIRED |
| C-SF-002 | Client accepts exact self-filing consent | Service-specific consent evidence is stored; full-representation consent is not reinterpreted | SOURCE_OK / LIVE_REQUIRED |
| C-SF-003 | Region/address/email submitted | Profile is saved; document upload/payment remain blocked until mailbox code is verified | SOURCE_OK / LIVE_REQUIRED |
| C-SF-004 | Wrong/expired email verification code | No document/payment stage opens; retry/resend remains bounded and auditable | SOURCE_OK / LIVE_REQUIRED |
| C-SF-005 | Verified email + DDU/passport uploaded and submitted | Source files stay on exact Case; lawyer review becomes available without claiming legal completeness | SOURCE_OK / LIVE_REQUIRED |
| C-SF-006 | Admin finds unreadable file | Admin may request a replacement only; no legal approve/reject or lawyer-review fact is created | SOURCE_OK / LIVE_REQUIRED |
| C-SF-007 | Responsible Lawyer reviews source files | Legal approve/reject decisions are snapshot checked; foreign lawyer cannot act | SOURCE_OK / LIVE_REQUIRED |
| C-SF-008 | Lawyer confirms completeness, court, jurisdiction and transfer-act fact | 15 000 ₽ bank-transfer obligation opens only after the legal gate; exact requisites/purpose are frozen on Payment | SOURCE_OK / LIVE_REQUIRED |
| C-SF-009 | Client sees payment instructions | Telegram shows 15 000 ₽, frozen bar-association requisites and mandatory purpose; no provider checkout is fabricated | SOURCE_OK / LIVE_REQUIRED |
| C-SF-010 | Staff confirms actual bank receipt | Preparation starts from factual receipt time; ambiguous money enters controlled review instead of silently advancing | SOURCE_OK / LIVE_REQUIRED |
| C-SF-011 | Transfer act signed / unsigned branches | Claim-calculation cutoff uses act date when signed, otherwise actual service-payment date with later court update warning | SOURCE_OK / LIVE_REQUIRED |
| C-SF-012 | Responsible Lawyer prepares package | Exactly four approved encrypted/versioned deliverables are required: pretension, claim, claim calculation, roadmap | SOURCE_OK / LIVE_REQUIRED |
| C-SF-013 | Package incomplete or stale | READY/email delivery is blocked; old package version cannot be published | SOURCE_OK / LIVE_REQUIRED |
| C-SF-014 | Four-document email delivery succeeds | Exact four attachments go to verified email; Case closes only after delivery evidence | SOURCE_OK / LIVE_REQUIRED |
| C-SF-015 | Email delivery fails | Case remains operationally recoverable; no false delivered/closed state is shown | SOURCE_OK / LIVE_REQUIRED |
| C-SF-016 | Closed self-filing Case | Telegram archive, messages, payments, documents and staff case cards are read-only and retain the self-filing service label | SOURCE_OK / LIVE_REQUIRED |

## D. Client — M2 consultation

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| C-M2-001 | Direct “contact lawyer” from first entry | One M2 context is created; client moves to description | SOURCE_OK / LIVE_REQUIRED |
| C-M2-002 | Calculator missing data routes to M2 | Existing context is reused/committed; no parallel M1+M2 | SOURCE_OK / LIVE_REQUIRED |
| C-M2-003 | Active M1 + generic consultation shortcut | Parallel M2 case is not created | SOURCE_OK / LIVE_REQUIRED |
| C-M2-004 | Repeated old `contact_lawyer` on active M2 | Exact current M2 stage resumes; second consultation is not created | SOURCE_OK / LIVE_REQUIRED |
| C-M2-005 | Description saved | Exact consultation/case receives text; stale description cannot overwrite a newer request | SOURCE_OK / LIVE_REQUIRED |
| C-M2-006 | Optional documents skipped | Client can continue to slot selection | SOURCE_OK / LIVE_REQUIRED |
| C-M2-007 | Optional documents uploaded | Files stay attached to exact M2 case and available to responsible lawyer | SOURCE_OK / LIVE_REQUIRED |
| C-M2-008 | Two clients choose same slot concurrently | One reservation wins; loser receives refreshed availability | SOURCE_OK / LIVE_REQUIRED |
| C-M2-009 | Reserved slot expires before payment | Exact reservation is released and case returns to slot selection | SOURCE_OK / LIVE_REQUIRED |
| C-M2-010 | M2 payment succeeds | Exact held slot becomes booked once | SOURCE_OK / LIVE_REQUIRED |
| C-M2-011 | M2 payment fails | Client returns to valid payment/slot recovery | SOURCE_OK / LIVE_REQUIRED |
| C-M2-012 | Duplicate/late M2 webhook | No second booking; stale money enters review/reconciliation | SOURCE_OK / LIVE_REQUIRED |
| C-M2-013 | Generic help button after booking | Opens current booking instead of creating new consultation | SOURCE_OK / LIVE_REQUIRED |
| C-M2-014 | Client reschedules | Old/new slot snapshot is checked; booking moves once | SOURCE_OK / LIVE_REQUIRED |
| C-M2-015 | Client cancels | Cancellation/refund policy is explicit and case stays consistent | SOURCE_OK / LIVE_REQUIRED |
| C-M2-016 | Client no-show | Admin chooses new paid booking or closure; no indefinite limbo | SOURCE_OK / LIVE_REQUIRED |
| C-M2-017 | Lawyer no-show | Admin chooses free rebooking or controlled refund | SOURCE_OK / LIVE_REQUIRED |
| C-M2-018 | Lawyer outcome = close | Case closes and result remains in archive | SOURCE_OK / LIVE_REQUIRED |
| C-M2-019 | Lawyer outcome = to M1 | Existing client/docs/context transfer to one M1 path | SOURCE_OK / LIVE_REQUIRED |
| C-M2-020 | Lawyer outcome = follow-up | Explicit follow-up remains visible and actionable | SOURCE_OK / LIVE_REQUIRED |
| C-M2-021 | Closed M2 case | Consultation result, documents and history remain read-only | SOURCE_OK / LIVE_REQUIRED |

## E. Administrator — operations and control

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| A-001 | Valid admin login | `/admin-ui` routes to canonical Workdesk | SOURCE_OK / LIVE_REQUIRED |
| A-002 | Anonymous Workdesk access | Redirect to Login; no staff data is rendered | SOURCE_OK / LIVE_REQUIRED |
| A-003 | Revoked/deactivated/role-changed session | Global session guard rejects it and clears cookie | SOURCE_OK / LIVE_REQUIRED |
| A-004 | Standalone unsupported staff role | Human-readable access-setup screen + logout; no fabricated permission | FIXED_V37 |
| A-005 | Incomplete lawyer profile reaches staff landing | Human-readable prerequisite instead of raw 403/409 | FIXED_V37 |
| A-006 | Unassigned M1 appears in queue | Primary action is assignment, not an action requiring a lawyer | SOURCE_OK / LIVE_REQUIRED |
| A-007 | Assign inactive/unreachable lawyer | Assignment is refused | SOURCE_OK / LIVE_REQUIRED |
| A-008 | Historical unreachable assignee | Integrity queue exposes guarded repair | SOURCE_OK / LIVE_REQUIRED |
| A-009 | Documents await review | Exact case/document deep link opens canonical review surface | SOURCE_OK / LIVE_REQUIRED |
| A-010 | Payment received but state mismatch | Payment review queue stops automated mutation | SOURCE_OK / LIVE_REQUIRED |
| A-011 | Stale M2 reservation payment | Dedicated reconciliation path; no automatic booking of a different slot | SOURCE_OK / LIVE_REQUIRED |
| A-012 | Refund pending | Admin records factual refund lifecycle; UI does not pretend to send money itself | SOURCE_OK / LIVE_REQUIRED |
| A-013 | Refund declined | Reason remains visible and item can be explicitly returned to processing | SOURCE_OK / LIVE_REQUIRED |
| A-014 | SLA overdue + no lawyer | Assignment prerequisite is primary action | SOURCE_OK / LIVE_REQUIRED |
| A-015 | Client no-show | Admin has terminal choice: rebook paid / close | SOURCE_OK / LIVE_REQUIRED |
| A-016 | Lawyer no-show | Admin has terminal choice: free rebook / refund | SOURCE_OK / LIVE_REQUIRED |
| A-017 | Legacy/ambiguous consultation outcome | Dedicated outcome-control screen provides resolution | SOURCE_OK / LIVE_REQUIRED |
| A-018 | Generic manual status tries to create proof-bearing stage | Rejected; domain action/evidence required | SOURCE_OK / LIVE_REQUIRED |
| A-019 | Process-integrity contradiction | `/admin/workdesk/integrity` shows severity, explanation and safe deep link | SOURCE_OK / LIVE_REQUIRED |
| A-020 | Full scheduler manual run | Personal MFA superadmin only; audited | SOURCE_OK / LIVE_REQUIRED |
| A-021 | Superadmin resets MFA/roles/password | Session version changes and existing sessions are revoked | SOURCE_OK / LIVE_REQUIRED |
| A-022 | Admin opens self-filing case card | Client contacts, calculation, documents, payment state, history and communications are visible in one case context | SOURCE_OK / LIVE_REQUIRED |
| A-023 | Admin tries to approve/reject a source document legally | Rejected; admin can only request a readable replacement | SOURCE_OK / LIVE_REQUIRED |
| A-024 | Admin confirms self-filing bank receipt | Exact frozen payment is reconciled with reference/evidence; package SLA starts only through the self-filing payment lifecycle | SOURCE_OK / LIVE_REQUIRED |
| A-025 | Closed self-filing case appears in active queues/integrity/message writes | It is treated as terminal/read-only everywhere; no active work item or legal write is fabricated | SOURCE_OK / LIVE_REQUIRED |

## F. Lawyer — legal work and responsibility

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| L-001 | Valid lawyer login | Role-aware landing opens Lawyer Workspace | FIXED_V37 / LIVE_REQUIRED |
| L-002 | Lawyer account lacks active linked business profile | Access fails closed with clear setup recovery, not raw dead end | FIXED_V37 / LIVE_REQUIRED |
| L-003 | Lawyer opens M1 assigned to self | Case is visible and actions are available according to state | SOURCE_OK / LIVE_REQUIRED |
| L-004 | Lawyer tries M1 assigned to another lawyer | Protected action/document access is denied | SOURCE_OK / LIVE_REQUIRED |
| L-005 | Lawyer opens M2 owned via consultation slot | Case is visible even without fake M1 assignment | SOURCE_OK / LIVE_REQUIRED |
| L-006 | Different lawyer tries M2 consultation/document | Effective-responsibility check denies access | SOURCE_OK / LIVE_REQUIRED |
| L-007 | Review document | Decision is snapshot checked; stale decision does not overwrite newer version | SOURCE_OK / LIVE_REQUIRED |
| L-008 | Accept M1 with missing/unapproved documents | Acceptance is rejected with readiness reason | SOURCE_OK / LIVE_REQUIRED |
| L-009 | Accept ready M1 | Contract flow becomes next action | SOURCE_OK / LIVE_REQUIRED |
| L-010 | Reject M1 | Mandatory reason is stored; client gets controlled next choice | SOURCE_OK / LIVE_REQUIRED |
| L-011 | Upload/replace service contract | Protected document store and exact versioning are used | SOURCE_OK / LIVE_REQUIRED |
| L-012 | Confirm POA receipt | Only responsible lawyer can establish receipt fact | SOURCE_OK / LIVE_REQUIRED |
| L-013 | Prepare/send claim | Legal actions use dedicated audited transitions | SOURCE_OK / LIVE_REQUIRED |
| L-014 | Open court before due date | Refused by claim-period eligibility | SOURCE_OK / LIVE_REQUIRED |
| L-015 | Open 70k without court evidence | Refused | SOURCE_OK / LIVE_REQUIRED |
| L-016 | Record court evidence | Evidence is persisted/audited before financial obligation | SOURCE_OK / LIVE_REQUIRED |
| L-017 | Record recovered amount | Only actual factual amount opens server success-fee calculation | SOURCE_OK / LIVE_REQUIRED |
| L-018 | Save M2 consultation result draft then close/reopen UI | Draft survives locally until committed | SOURCE_OK / LIVE_REQUIRED |
| L-019 | Complete M2 with close/to-M1/follow-up | Domain service commits exactly one outcome | SOURCE_OK / LIVE_REQUIRED |
| L-020 | Mark client no-show | Admin resolution becomes next operational action | SOURCE_OK / LIVE_REQUIRED |
| L-021 | Open assigned self-filing Case | Self-contained lawyer card shows client, calculation, documents, payments, process history, communications and one current next step | SOURCE_OK / LIVE_REQUIRED |
| L-022 | Start self-filing legal review | Only responsible Lawyer can establish the legal-review stage; admin technical check cannot start it | SOURCE_OK / LIVE_REQUIRED |
| L-023 | Confirm self-filing completeness/jurisdiction | Exact package version + source-document gate is required; 15 000 ₽ obligation opens only after confirmation | SOURCE_OK / LIVE_REQUIRED |
| L-024 | Upload/approve four final deliverables | Each required type is encrypted/versioned and SHA-bound; READY requires all four current approved documents | SOURCE_OK / LIVE_REQUIRED |
| L-025 | Standard M1 financial final on self-filing | No 30k/70k/success-fee panel is treated as applicable to the self-filing service | SOURCE_OK / LIVE_REQUIRED |

## G. Cross-role concurrency / stale-action safety

| ID | Scenario | Expected result | Current status |
| --- | --- | --- | --- |
| X-001 | Client opens old message while admin/lawyer advances case | Old callback becomes recovery/navigation only | SOURCE_OK / LIVE_REQUIRED |
| X-002 | Two staff users act on same document snapshot | One valid decision wins; stale mutation is rejected | SOURCE_OK / LIVE_REQUIRED |
| X-003 | Admin changes assignment while old lawyer tab is open | Subsequent protected lawyer action re-checks responsibility | SOURCE_OK / LIVE_REQUIRED |
| X-004 | Payment webhook races with admin review | Row/status checks prevent duplicate legal transition | SOURCE_OK / LIVE_REQUIRED |
| X-005 | Slot expiry races with payment callback | Exact consultation/reservation binding determines review vs booking | SOURCE_OK / LIVE_REQUIRED |
| X-006 | Case closes while client has old payment/document buttons | Archive remains read-only and new mutation is refused | SOURCE_OK / LIVE_REQUIRED |
| X-007 | Staff role/session is revoked while browser tab remains open | Next request is rejected by global session guard | SOURCE_OK / LIVE_REQUIRED |

## H. Live integration gates

| ID | Gate | Required evidence | Current status |
| --- | --- | --- | --- |
| LIVE-001 | GitHub CI | Real runner executes tests/build/migrations on current head | BLOCKED_INFRA — GitHub account billing/spending-limit error prevents runner start |
| LIVE-002 | Telegram API | Real client completes C-001..C-M2-021 on mobile Telegram; screenshots + update IDs | LIVE_REQUIRED |
| LIVE-003 | Redis FSM | Restart bot during calculator/upload/description flows; state resumes or recovers safely | LIVE_REQUIRED |
| LIVE-004 | PostgreSQL | Seeded E2E pass validates locks, transactions, migrations and history consistency | LIVE_REQUIRED |
| LIVE-005 | YooKassa/provider | Success/failure/duplicate/late webhook and refund/reconciliation captured | LIVE_REQUIRED |
| LIVE-006 | Browser staff UI | Admin/lawyer mobile+desktop pass, expired sessions, deep links and multi-tab stale actions | LIVE_REQUIRED |
| LIVE-007 | Notification delivery | Client/staff notifications are delivered once, retry safely and expose failures to operations | LIVE_REQUIRED |

## Release acceptance

Release can be called ready only when:

1. all P0/P1 source dead ends are closed;
2. `LIVE-001` executes rather than failing before runner allocation;
3. every normal M1 and M2 row passes with DB/audit/visible-next-action evidence;
4. every stale/duplicate/concurrency row proves no unintended mutation;
5. client, admin and lawyer each have a clear terminal/recovery action on every tested screen;
6. no production route relies on fake/demo payment confirmation or automatic legal decisions.
