from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_poa_screen_emits_exact_case_readiness_callback():
    source = read("app/bot/screens/poa_handoff.py")

    assert 'return f"poa_done:v2:{int(case_id)}"' in source
    assert '_bound_done(int(case.id))' in source
    assert "CaseStatus.M1_WAITING_POWER_OF_ATTORNEY" in source


def test_legacy_unbound_poa_done_is_navigation_only():
    source = read("app/bot/screens/poa_handoff.py")

    legacy = source.split("async def legacy_poa_done_is_navigation_only", 1)[1].split(
        "async def poa_done_for_exact_case", 1
    )[0]
    assert "await db.rollback()" in legacy
    assert "await _show_current_instruction(callback, db)" in legacy
    assert "change_status(" not in legacy
    assert "db.commit" not in legacy


def test_bound_poa_readiness_locks_exact_owned_active_case():
    source = read("app/bot/screens/poa_handoff.py")

    handler = source.split("async def poa_done_for_exact_case", 1)[1]
    assert "Case.id == int(expected_case_id)" in handler
    assert "Case.client_id == int(user.id)" in handler
    assert ".with_for_update()" in handler
    assert "int(active.id) != int(case.id)" in handler
    assert "status != CaseStatus.M1_WAITING_POWER_OF_ATTORNEY" in handler


def test_client_poa_readiness_stops_before_lawyer_receipt():
    source = read("app/bot/screens/poa_handoff.py")

    handler = source.split("async def poa_done_for_exact_case", 1)[1]
    assert "next_status=CaseStatus.M1_POA_CLIENT_READY_REPORTED" in handler
    assert "M1_CLAIM_PREPARATION" not in handler
    assert "фактическое получение должен отдельно подтвердить юрист" in handler
