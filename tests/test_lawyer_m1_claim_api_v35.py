from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_claim_api_uses_three_named_routes_not_generic_status_mutation():
    source = read("app/api/lawyer_m1_claim.py")

    assert '@router.post("/cases/{case_id}/claim/start")' in source
    assert '@router.post("/cases/{case_id}/claim/sent")' in source
    assert '@router.post("/cases/{case_id}/court/open")' in source
    assert 'router.post("/cases/{case_id}/status")' not in source


def test_every_claim_write_is_assigned_lawyer_and_snapshot_protected():
    source = read("app/api/lawyer_m1_claim.py")

    assert source.count("await require_lawyer_actor") == 3
    assert source.count("await assigned_case(") == 3
    assert source.count("for_update=True") == 3
    assert source.count("assert_case_snapshot(") == 3
    assert source.count("expected_status=expected_status") == 3
    assert source.count("expected_updated_at=expected_updated_at") == 3


def test_claim_api_emits_durable_client_lifecycle_events_before_commit():
    source = read("app/api/lawyer_m1_claim.py")

    for event in {
        "M1_CLAIM_PREPARATION_STARTED",
        "M1_CLAIM_SENT",
        "COURT_STAGE_STARTED",
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
