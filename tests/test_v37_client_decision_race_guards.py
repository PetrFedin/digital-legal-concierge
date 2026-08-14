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


def test_consent_accept_and_decline_are_case_bound_and_serialized():
    service = read("app/domain/cases/consent_decision_service.py")
    guard = read("app/bot/screens/consent_decision_guard.py")
    bot = read("app/bot/bot.py")

    assert "case_id: int" in service
    assert "Case.id == int(case_id)" in service
    assert "Case.client_id == int(client_id)" in service
    assert ".with_for_update()" in service
    assert "CONSENT_ACCEPT" in service
    assert "CONSENT_DECLINE" in service
    assert "await self.cases.transfer_to_m1(" in service
    assert "next_status=CaseStatus.CALCULATED" in service
    assert '"route_not_selected"' in service
    assert '"stale_m1"' in service
    assert '"stale_m2"' in service
    assert "force=True" not in service

    assert "consent_accept:v2:" in guard
    assert "consent_decline:v2:" in guard
    assert "consent_decline_confirm:v2:" in guard
    assert "legacy_unbound_consent_refresh" in guard
    assert "case_id=case_id" in guard
    assert bot.index("consent_decision_guard.router,") < bot.index(
        "consent_stale_guard.router,"
    )
    assert bot.index("consent_decision_guard.router,") < bot.index("consent_flow.router,")


def test_unbound_historical_consent_callbacks_are_navigation_only():
    guard = read("app/bot/screens/consent_decision_guard.py")

    legacy = guard.split("async def legacy_unbound_consent_refresh", 1)[1].split(
        "async def guarded_consent_decline_prompt", 1
    )[0]
    assert "await guarded_consent_open(callback, db)" in legacy
    assert "ConsentDecisionService" not in legacy
    assert "db.commit" not in legacy
    assert '"consent_accept",' in guard
    assert '"consent_decline",' in guard
    assert '"consent_decline_confirm",' in guard


def test_post_calculation_m2_and_consent_use_same_exact_case_lock_boundary():
    post = read("app/domain/cases/post_calculation_decision_service.py")
    consent = read("app/domain/cases/consent_decision_service.py")

    for source in (post, consent):
        assert "Case.id == int(case_id)" in source
        assert "Case.client_id == int(client_id)" in source
        assert ".with_for_update()" in source
    assert "CaseStatus.CLIENT_DECISION" in post
    assert "CaseStatus.CLIENT_DECISION" in consent
    assert "await self.cases.transfer_to_m2(" in post
    assert "await self.cases.transfer_to_m1(" in consent
