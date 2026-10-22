# Buyer demo — provenance and acceptance

## Verified source and scope
- Capture automation: `scripts/buyer_demo_capture.py`
- Execution: `.github/workflows/browser-staff-e2e.yml` only in isolated GitHub Actions PostgreSQL test service.
- Chromium signs in as two distinct synthetic roles from `scripts/browser_staff_e2e.py`.
- Artifacts: `admin_overview.png`, `admin.png`, `lawyer.png`, `admin.webm`, `lawyer.webm`, `evidence_manifest.json`.
- Artifacts are retained for 14 days and reflect the exact workflow SHA.
- Synthetic case numbers `BROWSER-OWN-2026`, `BROWSER-FOREIGN-2026`.
- This does not qualify the actual Telegram app, live payment provider, legal calculations, production release or client acceptance.

## Required proof before adding screenshots to the buyer proposal
1. Exact-head **Browser Staff E2E** passes; the run uploads `dlc-buyer-demo-staff-real-ui`.
2. Reviewer opens the artifact and visually checks that the interface is authentic, no private credentials, tokens or client data appear, and the screenshot matches `evidence_manifest.json`.
3. Dedicated test Telegram account and test bot: manually capture the genuine client home/calculator, case/documents and next step. Never use production data.
4. Compile a 30–45-second MP4 using only authenticated test recordings and authentic Telegram captures. Clearly label **test environment**.
5. Append all three frames to the commercial proposal; include the SHA, test run URL, capture date and the disclaimer that production UAT is outstanding.

## Buyer walk-through (suggested 45 sec)
- 00–08s: Telegram client, calculator and route M1/M2.
- 08–18s: administrator, new inquiries and queues.
- 18–29s: lawyer, own cases and document review.
- 29–38s: handoffs, notifications and case status (only verified screens).
- 38–45s: two alternative transaction options and scope of acceptance.

## Release integrity
Keep the visual demo distinct from:
- legal rule verification for 214-FZ and current decree exceptions;
- consent B-007/operator identity;
- client archived-document byte delivery;
- privacy and historic audit retention;
- external Telegram, Timeweb and YooKassa UAT.

**NO PRODUCTION GO-LIVE or handover representation** is inferred from browser UI screenshots.
