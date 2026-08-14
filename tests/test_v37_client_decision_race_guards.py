from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_m1_rejection_decisions_are_mutually_exclusive_under_row_lock():
    service = read("app/domain/cases/m1_rejection_decision_service.py")
    guard = read("app/bot/screens/m1_rejection_decision_guard.py")
    bot = read("app/bot/bot.py")

    assert ".with_for_update()" in service
    assert "DECISION_TO_M2" in service
    assert "DECISION_CLOSE" in service
    assert "await self.cases.transfer_to_m2(" in service
    assert "next_status=CaseStatus.M1_CLOSED" in service
    assert "force=True" not in service

    assert 'c.data == "m1_rejected_to_m2"' in guard
    assert 'c.data == "m1_rejected_close_confirm"' in guard
    m2_handler = guard.split("async def guarded_rejected_m1_to_m2", 1)[1].split(
        "async def guarded_rejected_m1_close", 1
    )[0]
    assert "ConsultationIntakeService(db).get_or_create_context(user)" in m2_handler
    assert m2_handler.index("get_or_create_context(user)") < m2_handler.index("await db.commit()")
    assert "await db.rollback()" in m2_handler

    assert bot.index("m1_rejection_decision_guard.router,") < bot.index(
        "m1_rejection_recovery.router,"
    )


def test_consent_accept_and_decline_are_serialized_against_route_switch():
    service = read("app/domain/cases/consent_decision_service.py")
    guard = read("app/bot/screens/consent_decision_guard.py")
    bot = read("app/bot/bot.py")

    assert ".with_for_update()" in service
    assert "CONSENT_ACCEPT" in service
    assert "CONSENT_DECLINE" in service
    assert "await self.cases.transfer_to_m1(" in service
    assert "next_status=CaseStatus.CALCULATED" in service
    assert '"route_not_selected"' in service
    assert '"stale_m1"' in service
    assert '"stale_m2"' in service
    assert "force=True" not in service

    assert 'c.data == "consent_accept"' in guard
    assert 'c.data == "consent_decline_confirm"' in guard
    assert "Старая кнопка согласия не выбирает M1 автоматически" in guard
    assert bot.index("consent_decision_guard.router,") < bot.index(
        "consent_stale_guard.router,"
    )
    assert bot.index("consent_decision_guard.router,") < bot.index("consent_flow.router,")


def test_post_calculation_m2_uses_same_locked_case_boundary_as_consent():
    post = read("app/domain/cases/post_calculation_decision_service.py")
    consent = read("app/domain/cases/consent_decision_service.py")

    assert ".with_for_update()" in post
    assert ".with_for_update()" in consent
    assert "CaseStatus.CLIENT_DECISION" in post
    assert "CaseStatus.CLIENT_DECISION" in consent
    assert "await self.cases.transfer_to_m2(" in post
    assert "await self.cases.transfer_to_m1(" in consent
