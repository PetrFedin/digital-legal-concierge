# Post-LIVE release evidence sequence

Status: **SOURCE_OK / NOT EXECUTED**.

This runbook fixes the release order for the current M1/M2 candidate. It supplements `docs/ACCEPTANCE_CURRENT.md` and `docs/LIVE_REQUIRED_RUNBOOK.md`. It does not itself convert any gate to PASS.

The order is deliberately fail-closed. A later gate must not be used to compensate for a missing earlier gate, and a source/workflow change after evidence is collected invalidates the evidence chain for the old commit SHA.

## Release sequence

For one immutable release-candidate commit SHA, execute in this order:

1. **Runner allocation restored** — issue #116 is no longer blocking execution; jobs have a real runner allocation and real executed steps.
2. **Full CI** — every required PR check for the exact candidate SHA executes and passes. This includes the main CI workflow and any required security/dependency/deployment checks attached to the PR.
3. **Dedicated runtime workflows** — run and pass the PostgreSQL concurrency, Redis/Telegram runtime and browser staff E2E workflows for that same SHA. A configured workflow that never receives a runner is not evidence.
4. **One complete LIVE_REQUIRED run** — dispatch `.github/workflows/live-required.yml` for the same SHA and retain the SHA/run/attempt-bound `LIVE_REQUIRED_MANIFEST.json`. Do not combine component evidence from different workflow attempts.
5. **Real Telegram M1/M2 persona walkthroughs** — execute the complete client/staff paths with dedicated non-production Telegram identities/chats against the production-like PostgreSQL/Redis application image. Record Case ids, timestamps and sanitized evidence references; do not record bot tokens, document plaintext or payment credentials.
6. **Encrypted backup -> separate restore drill** — take the post-persona data state, create and verify an encrypted backup, restore it only into a separate empty staging/restore/drill PostgreSQL database and restored storage directory, then prove application-level facts and runtime usability.
7. **Provider paid/refund sandbox expansion** — only after all previous gates pass, add and execute only YooKassa test-shop paid/refund scenarios that can be completed safely without any production credential, production shop, production payment or production callback target.
8. **Release decision** — PR #114 remains unmerged until the required evidence above is complete and no unresolved blocker/regression remains.

If any source, migration, workflow or release-evidence script changes after step 2 begins, freeze a new candidate SHA and restart from full CI. Evidence from the superseded SHA may be retained for diagnosis but must not be used to approve the new SHA.

## Gate 1 — runner allocation

Issue #116 is cleared only when relevant Actions jobs show a real runner allocation and executed steps. `runner_id=0`, an empty/null step list, or a run that terminates before runner execution remains `BLOCKED_INFRA`.

Do not rerun the whole release matrix repeatedly while the infrastructure state is unchanged. The dedicated runner watch may be used to detect the meaningful transition.

## Gate 2 — full CI

The release candidate must first pass the ordinary PR verification pyramid. At minimum, verify the actual required checks attached to the candidate, including the main `CI` workflow.

The main CI currently includes:

- source compilation and architecture check;
- Alembic migration/idempotency/schema checks;
- the complete fast `pytest -q` suite, including `tests/test_post_live_restore_evidence.py`, `tests/test_document_storage_portability.py` and the normal-download storage-scope contract;
- PostgreSQL migration plus technical encrypted backup/empty-staging-restore integration;
- container build/start smoke.

The CI backup test is intentionally a **technical infrastructure restore probe**. It proves dump/encryption/extraction/restore mechanics with synthetic data. It does not replace the later post-persona application restore drill.

## Gate 3 — dedicated PostgreSQL / Redis / browser proof

For the same frozen SHA, require successful executions of the dedicated workflows that cover the runtime-specific contracts:

- `.github/workflows/postgres-concurrency.yml`;
- `.github/workflows/telegram-runtime.yml` for Redis-backed Telegram FSM/restart behavior;
- `.github/workflows/browser-e2e.yml` for staff browser behavior.

The PostgreSQL release proof must include the v37 auto-assignment capacity race. The browser proof must include the two-tab Payment Review stale-decision recovery where the losing tab receives authoritative 409/server truth and cannot create a second resolution.

## Gate 4 — one complete LIVE_REQUIRED run

Only after Gates 1-3 pass, dispatch `.github/workflows/live-required.yml` once for the frozen candidate. A rerun is a new `run_attempt` and must generate its own component artifacts.

Release evidence requires the final manifest from one exact workflow run and attempt:

`live-required-release-evidence-<SHA>-attempt-<run_attempt>`

The manifest must name the frozen candidate SHA and contain successful evidence for PostgreSQL, Redis, real Telegram delivery, browser and safe YooKassa test-shop create/retry/retrieve. See `docs/LIVE_REQUIRED_RUNBOOK.md` for the component contract.

The provider component at this stage intentionally stops at a test-shop unpaid/pending payment. It is not provider-side paid/refund proof.

## Gate 5 — real Telegram M1/M2 personas

The automated Telegram component proves Bot API reachability and delivery, not the complete business journey. After LIVE_REQUIRED passes, execute real persona walkthroughs with separate acceptance identities.

### M1

Execute the current approved M1 path end to end through Telegram and staff surfaces, including calculation, M1 selection, consent, encrypted document upload/review, service-contract evidence, both required payment stages, power-of-attorney/claim/court/enforcement evidence, actual recovered amount, success fee, structured close and read-only/archive behavior.

At every material mutation verify the Telegram result against PostgreSQL Case/Document/Payment state, Case/Audit history and PaymentEvent evidence. Include stale/duplicate/retry paths required by `docs/ACCEPTANCE_CURRENT.md`.

### M2

Execute description, optional encrypted document, slot selection/reservation, payment/booking, lawyer preparation/result and close/follow-up/to-M1 outcomes. Exercise the required races/recovery cases, including stale reservation payment, hold expiry, duplicate success, no-show/rebook/refund and Payment Review recovery.

A persona is not PASS when only screenshots look correct. The database and immutable evidence must agree with the UI.

## Gate 6 — encrypted backup -> restore drill

Run this gate **after** the Telegram persona walkthroughs so the backup contains the evidence state that was actually exercised.

### 6.1 Select the restore witness

Choose one persona Case that has, at minimum:

- an encrypted V2 historical Document with an intact data key envelope;
- a Payment with at least one immutable `PaymentEvent`;
- Case `AuditLog` history;
- an active staff account that can be used for the post-restore runtime check.

Prefer a completed persona Case with meaningful history. Do not create a special empty Case merely to make the drill pass.

### 6.2 Capture pre-backup application facts

Run the evidence script from the exact frozen release SHA while `DATABASE_URL` and `STORAGE_DIR` point to the source acceptance environment:

```bash
python scripts/post_live_restore_evidence.py snapshot \
  --release-sha <40-char-release-SHA> \
  --case-id <CASE_ID> \
  --document-id <DOCUMENT_ID> \
  --payment-id <PAYMENT_ID> \
  --staff-username <RESTORE_TEST_STAFF_USERNAME> \
  --output /secure-evidence/POST_LIVE_RESTORE_SOURCE.json
```

The current evidence format is **schema v2**. The script:

- requires PostgreSQL at the current Alembic head;
- validates the complete AuditLog hash chain;
- proves the selected encrypted document can be decrypted with the configured historical keyring and matches its persisted SHA-256;
- captures Case, Document, Payment, PaymentEvent, case-audit and staff facts in privacy-minimized form;
- hashes direct identifiers such as case number, original filename, DB file path, payment code, provider payment id, reservation key and staff username;
- never writes document plaintext into evidence;
- refuses to overwrite an existing evidence file;
- applies the same document-storage contract as runtime: a current relative key must be exactly `cases/<case_id>/<32hex>.dlcenc`; a prefixed relative key, traversal, invalid ciphertext name or wrong Case fails closed;
- accepts a historical absolute `Document.file_path` only as legacy metadata: only its terminal canonical `cases/<case_id>/<32hex>.dlcenc` key is retained and the ciphertext is read from the **current configured `STORAGE_DIR`**, never from the historical source prefix;
- rejects symbolic-link components in the selected storage path.

Record the `snapshot_file_sha256` printed by the command in a separate protected release record. This external value is required by the post-restore verifier; changing both a snapshot and its internal checksum is therefore insufficient to pass unnoticed.

A snapshot from another evidence schema is not silently upgraded. It is invalid for the current candidate and must be regenerated from the exact current SHA before the backup is created.

### 6.3 Create and verify the encrypted backup

Use the existing provider-aware backup CLI against the source environment:

```bash
python -m app.security.backup_cli create \
  --database-url "$DATABASE_URL" \
  --storage-dir "$STORAGE_DIR" \
  --backup-dir "$BACKUP_DIR"

python -m app.security.backup_cli verify <ARCHIVE_PATH>
```

Record the archive identity/metadata returned by the CLI. Never copy secrets into the archive; backup encryption keys, document keyrings, audit-integrity keyrings and runtime credentials remain external secret-manager material.

### 6.4 Restore only into an empty safe target

Create a completely separate empty PostgreSQL database whose name ends in `staging`, `restore`, `drill` or `test`, point `STAGING_DATABASE_URL` at it, and run:

```bash
python -m app.security.backup_cli restore-postgresql-staging \
  <ARCHIVE_PATH> /secure-restore/<RUN_ID> \
  --confirm-database <EXACT_TARGET_DATABASE_NAME>
```

The restore code rejects the configured production endpoint, system databases, non-empty targets and unsafe target names. The extracted storage for the next step is `/secure-restore/<RUN_ID>/storage`.

### 6.5 Verify exact restored application facts

Start a fresh process with `DATABASE_URL` pointing to the restored safe database. Supply the same required historical document/audit keyrings from the external secret manager. Do not point any provider or Telegram credential at production.

Run:

```bash
python scripts/post_live_restore_evidence.py verify \
  --release-sha <40-char-release-SHA> \
  --snapshot /secure-evidence/POST_LIVE_RESTORE_SOURCE.json \
  --expected-snapshot-sha256 <RECORDED_SOURCE_SNAPSHOT_SHA256> \
  --staff-username <RESTORE_TEST_STAFF_USERNAME> \
  --restored-storage-dir /secure-restore/<RUN_ID>/storage \
  --output /secure-evidence/POST_LIVE_RESTORE_RESULT.json
```

The schema-v2 verifier fails closed unless:

- the source snapshot has the recorded external SHA-256, the supported schema and a valid internal facts checksum;
- the release SHA is identical;
- the target DB endpoint differs from the source and has a safe restore suffix;
- the database is at the current Alembic head;
- the complete restored AuditLog hash chain verifies;
- the exact selected Case/Document/Payment/PaymentEvent/case-audit/staff facts match the pre-backup snapshot;
- the witness storage key is the same canonical Case-bound key captured before backup;
- the ciphertext is read only from the supplied restored `storage/` root, decrypts with restored DB envelope metadata and matches the original plaintext SHA-256;
- a prefixed relative key, wrong-Case key, traversal, invalid ciphertext name, absolute snapshot key or symbolic-link component is rejected.

`status: RESTORE_PASS` in this script means the **application data/storage evidence sub-gate** passed. It does not by itself complete the whole backup acceptance gate below.

### 6.6 Runtime usability on the restored environment

Before marking backup/restore as complete, start the application against the restored database/storage and an isolated Redis instance, then verify:

- the selected staff account can authenticate through the normal supported staff login path;
- the selected Case opens in the staff UI;
- Case/Audit history and Payment/PaymentEvent state are readable and agree with `POST_LIVE_RESTORE_RESULT.json`;
- the selected historical Document opens/decrypts through the normal authorized document-grant/download path, not by a direct filesystem shortcut;
- normal document download binds storage resolution to the authorized `Case.id`; a corrupted/wrong-Case storage key must fail closed;
- the bot process/dispatcher can start against the restored PostgreSQL/Redis state without mutating the source environment;
- no provider or Telegram production-side effect is triggered during the restore drill.

Only the combination of successful encrypted backup verification, safe staging restore, `RESTORE_PASS` application evidence and restored-runtime usability is the **backup -> restore drill PASS**.

## Gate 7 — safe YooKassa paid/refund expansion

Do not use the paid/refund sandbox extension as a prerequisite for proving the earlier infrastructure gates. It starts only after the restore drill passes.

Hard safety rules:

- credentials must first be proven to belong to a YooKassa test shop;
- every provider object used as evidence must report `test=true`;
- production shop ids, production secrets, production payment ids and production callback destinations are forbidden;
- do not infer a paid/refund PASS from application-only PostgreSQL tests;
- implement only scenarios that the current YooKassa test environment can complete deterministically and reversibly;
- if user/card confirmation is required by the provider, record that as an explicit sandbox step rather than bypassing it;
- any ambiguity about the test/production boundary is a hard stop.

The existing LIVE_REQUIRED provider smoke remains the lower-risk create/retry/retrieve proof until this later gate is deliberately expanded.

## Merge rule for PR #114

PR #114 must remain unmerged while any mandatory gate for the frozen candidate is `BLOCKED_INFRA`, `LIVE_REQUIRED`, failed, stale because the SHA changed, or lacks retained evidence.

A merge decision is allowed only after the exact candidate SHA has a coherent evidence chain from full CI through the required post-LIVE gates and there is no unresolved release blocker. No automatic merge is part of this runbook.
