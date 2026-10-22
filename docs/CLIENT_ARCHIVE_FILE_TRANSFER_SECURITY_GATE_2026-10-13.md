# Client archive file transfer — security implementation gate

Status: **DESIGN / BLOCKED**. This document does not enable Telegram file delivery.

## Boundary already implemented
`ClientArchiveDocumentAccessService.preflight` returns **only** non-sensitive document metadata after a joined owner/Case/document, terminal-status, retention, blocked-user and encrypted-envelope check. The result **must never be interpreted as a grant**.

## Required next implementation
1. **Channel authorization:** legal/privacy officer approves whether a client's specific document may be delivered to Telegram. If not approved, use a private short-lived authenticated portal. In either case verify the user through the actual Telegram update/approved portal authentication, never query parameters alone.
2. **Issue:** DB-backed `client_document_grants` table (separate from staff grants). Bind to authenticated `user_id`, `case_id`, `document_id`, immutable `version`, `sha256`, channel, expiry, state, random opaque secret's keyed digest. Do not place bearer secret in an ordinary, reusable Telegram callback. Maximum 1 active grant for a document and strict per-user throttling.
3. **Consume:** atomically transition `ISSUED -> CONSUMING` in a PostgreSQL transaction under lock and re-run the **current** preflight under the same transaction. Reject expired/revoked/used grants, changed status, pending erasure or different document SHA/version. Side effect after DB lock release must use an expiring lease to prevent double-delivery; subsequent redemption never creates a second transfer.
4. **Inspect/decrypt:** only after authorization, check V2 envelope and authenticated decryption via `LocalStorageService.read_document_bytes` with expected SHA-256 and metadata. Bound byte size again; discard plaintext on exceptions. Never return file path, raw DEK or ciphertext metadata in client responses.
5. **Deliver:** approved channel only; Telegram-specific opt-in must be explicit and logged. Receipt state `DELIVERED` requires positive provider acknowledgement; timeout `UNKNOWN` is **not** safe for automatic replay because the recipient might already have received the file. Manual reconciliation or separate idempotent provider mechanism must handle uncertain outcomes.
6. **Audit:** store recipient pseudonym, case/document/version, consent version, request/decision/transfer state and failure class. No plaintext, document title/path, URL, token or private content in signed audit. Retention purge/revocation must invalidate every outstanding grant.
7. **Race gate:** PostgreSQL negative tests for double-click/parallel consumption, Case status or owner change, retention APPROVED/EXECUTING between issue and consume, removed key, tampered bytes and provider timeout. Staff access grants must be unchanged.

## Admission evidence
No deployment until exact-head CI, independent security review, ephemeral PostgreSQL backup/restore, real Telegram test account and legal channel/retention sign-off. Keep the UI download action hidden until a separately reviewed transport PR is accepted. Never weaken the existing staff-session grant model to make this work.

Related: Issues #194, #196, #198; Draft PRs #195, #197, #199, #200.
