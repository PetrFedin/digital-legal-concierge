from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_claim_deadline_scheduler_uses_domain_eligibility_not_mutable_case_timestamp():
    source = (ROOT / "app/scheduler/jobs.py").read_text(encoding="utf-8")
    block = source[source.index("async def check_claim_waiting_30_days") :]

    assert "M1ClaimService(self.db)" in block
    assert "court_eligibility(case=case, now=now)" in block
    assert "if not eligibility.eligible" in block
    assert 'event_code="CLAIM_30_DAYS_EXPIRED"' in block
    assert 'dedupe_key=f"case:{case.id}:claim-30-days-expired"' in block
    assert "Case.updated_at <=" not in block
    assert ".where(Case.updated_at" not in block
