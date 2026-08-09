from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_claim_api_uses_four_named_routes_not_generic_status_mutation():
    source = read("app/api/lawyer_m1_claim.py")

    assert '@router.post("/cases/{case_id}/claim/start")' in source
    assert '@router.post("/cases/{case_id}/claim/sent")' in source
    assert '@router.post("/cases/{case_id}/court/open")' in source
    assert '@router.post("/cases/{case_id}/court/payment/open")' in source
    assert 'router.post("/cases/{case_id}/status")' not in source


def test_snapshot_helper_locks_assigned_case_and_all_writes_use_it():
    source = read("app/api/lawyer_m1_claim.py")

    helper = source[
        source.index("async def _assigned_snapshot_case") :
        source.index('@router.post("/cases/{case_id}/claim/start")')
    ]
    assert "await assigned_case(" in helper
    assert "for_update=True" in helper
    assert "assert_case_snapshot(" in helper
    assert "expected_status=expected_status" in helper
    assert "expected_updated_at=expected_updated_at" in helper
    assert source.count("await require_lawyer_actor") == 4
    assert source.count("await _assigned_snapshot_case(") == 4


def test_claim_api_emits_durable_client_lifecycle_events_before_commit():
    source = read("app/api/lawyer_m1_claim.py")

    for event in {
        "M1_CLAIM_PREPARATION_STARTED",
        "M1_CLAIM_SENT",
        "COURT_STAGE_STARTED",
        "COURT_PAYMENT_OPENED",
    }:
        marker = f'event_code="{event}"'
        assert marker in source
        assert source.index(marker) < source.index("await db.commit()", source.index(marker))


def test_claim_sent_uses_domain_due_date_and_court_open_rechecks_eligibility():
    api_source = read("app/api/lawyer_m1_claim.py")
    service_source = read("app/domain/cases/m1_claim_service.py")

    assert "eligibility = await service.court_eligibility(case=case)" in api_source
    assert 'due_at.strftime("%d.%m.%Y %H:%M UTC")' in api_source
    assert "eligibility = await self.court_eligibility(case=case, now=now)" in service_source
    assert "if not eligibility.eligible" in service_source


def test_court_payment_endpoint_uses_named_domain_handoff_only_after_court_stage():
    api_source = read("app/api/lawyer_m1_claim.py")
    service_source = read("app/domain/cases/m1_claim_service.py")

    assert "await M1ClaimService(db).open_court_payment(" in api_source
    assert "if self._status(case) != CaseStatus.M1_COURT_STAGE" in service_source
    assert "next_status=CaseStatus.M1_WAITING_PAYMENT_70000" in service_source
