from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_all_three_primary_post_calculation_callbacks_have_live_handlers():
    source = read("app/bot/screens/post_calculation.py")

    for callback in ("calc_continue_m1", "calc_to_m2", "calc_postpone"):
        assert f'c.data == "{callback}"' in source
    assert "continue_m1_after_calculation" in source
    assert "continue_m2_after_calculation" in source
    assert "postpone_after_calculation" in source


def test_m1_choice_stops_at_client_decision_and_never_skips_consent():
    service = read("app/domain/cases/post_calculation_decision_service.py")
    screen = read("app/bot/screens/post_calculation.py")

    m1 = service.split("if normalized_choice == CHOICE_M1:", 1)[1].split(
        "if normalized_choice == CHOICE_M2:", 1
    )[0]
    assert "next_status=CaseStatus.CLIENT_DECISION" in m1
    assert "transfer_to_m1" not in m1
    assert 'outcome="m1_consent_required"' in m1

    handler = screen.split("async def continue_m1_after_calculation", 1)[1].split(
        "async def continue_m2_after_calculation", 1
    )[0]
    assert '"consent_open"' in handler
    assert "transfer_to_m1" not in handler
    assert "consent_accept" not in handler


def test_m2_choice_transitions_and_creates_consultation_in_same_transaction():
    service = read("app/domain/cases/post_calculation_decision_service.py")
    screen = read("app/bot/screens/post_calculation.py")

    assert "await self.cases.transfer_to_m2(" in service
    handler = screen.split("async def continue_m2_after_calculation", 1)[1].split(
        "async def postpone_after_calculation", 1
    )[0]
    assert "ConsultationIntakeService(db).get_or_create_context(user)" in handler
    assert handler.index("get_or_create_context(user)") < handler.index("await db.commit()")
    assert "await db.rollback()" in handler
    assert '"consult_subject_start"' in handler


def test_postpone_keeps_calculation_and_does_not_create_service():
    service = read("app/domain/cases/post_calculation_decision_service.py")
    screen = read("app/bot/screens/post_calculation.py")

    postpone = service.split('comment=(\n                    "Клиент отложил выбор маршрута', 1)[0]
    assert "CaseStatus.CLIENT_DECISION" in service
    assert "next_status=CaseStatus.CALCULATED" in service
    handler = screen.split("async def postpone_after_calculation", 1)[1]
    assert "ConsultationIntakeService" not in handler
    assert "transfer_to_m1" not in handler
    assert "transfer_to_m2" not in handler
    assert "платёж не создавался" in handler


def test_route_choice_is_locked_and_old_keyboard_cannot_pull_live_case_backwards():
    service = read("app/domain/cases/post_calculation_decision_service.py")

    assert ".with_for_update()" in service
    assert 'outcome="stale_m1"' in service
    assert 'outcome="stale_m2"' in service
    assert 'outcome="stale_other"' in service
    assert 'status.value.startswith("M1_")' in service
    assert 'status.value.startswith("M2_")' in service
    assert "force=True" not in service


def test_post_calculation_router_is_mounted_before_consent_and_fallback():
    source = read("app/bot/bot.py")

    assert source.index("post_calculation.router,") < source.index("consent_flow.router,")
    assert source.index("post_calculation.router,") < source.index("fallback.router,")
