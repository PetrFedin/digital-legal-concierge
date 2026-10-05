from __future__ import annotations

import inspect

from app import main
from app.bot import bot
from app.bot.screens import documents, m1_stages, no_payment, no_payment_legal
from app.config import settings
from app.domain.cases.case_transition_policy import transition_allowed
from app.domain.documents import document_service
from app.domain.payments.mode import (
    payment_mode_valid,
    payments_disabled,
    payments_enabled,
)
from app.domain.statuses.case_statuses import CaseStatus


def _compact(value: str) -> str:
    return "".join(value.split())


def test_disabled_payment_mode_is_valid_but_not_enabled(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "disabled")

    assert payments_disabled() is True
    assert payments_enabled() is False
    assert payment_mode_valid() is True


def test_readiness_separates_payment_capability_from_service_health():
    source = inspect.getsource(main.create_app)

    assert '"payment_mode_valid": payment_mode_valid()' in source
    assert '"payment_provider_configured": payment_mode_valid()' in source
    assert '"disabled_by_configuration": payment_disabled' in source
    assert '"pilot_flows_continue_without_payment": payment_disabled' in source
    assert '"enabled": not payment_disabled' in source
    assert 'RedirectResponse(url="/operator")' in source


def test_pilot_handlers_are_registered_before_generic_payment_handlers():
    source = inspect.getsource(bot.build_dispatcher)
    compact = _compact(source)

    assert compact.index("no_payment_legal.router") < compact.index(
        "no_payment.router"
    )
    assert compact.index("no_payment.router") < compact.index("payments.router")
    assert compact.index("no_payment.router") < compact.index(
        "consultations.router"
    )
    assert compact.index("no_payment.router") < compact.index("m1_stages.router")


def test_no_payment_booking_confirms_slot_without_creating_payment():
    source = inspect.getsource(no_payment)
    booking = inspect.getsource(no_payment._book_without_payment)

    assert "Payment(" not in source
    assert "create_payment_link" not in source
    assert "confirm_booking" in booking
    assert "ConsultationStatus.BOOKED" in booking
    assert "CaseStatus.M2_PAYMENT_PENDING" in booking
    assert "CaseStatus.M2_CONSULTATION_BOOKED" in booking
    assert "CONSULTATION_BOOKED_WITHOUT_PAYMENT" in booking
    assert "dedupe_key=" in booking
    assert "await db.flush()" in booking


def test_no_payment_actions_reject_stale_or_wrong_case_stages():
    source = inspect.getsource(no_payment)
    advance = inspect.getsource(no_payment._advance_path)

    assert 'return "invalid"' in advance
    assert source.count('result == "invalid"') >= 3
    assert "Кнопка не соответствует текущему этапу дела" in source
    assert "У вас уже есть активное дело другого маршрута" in source


def test_cancelled_booked_consultation_can_return_to_slot_selection():
    assert transition_allowed(
        CaseStatus.M2_CONSULTATION_BOOKED,
        CaseStatus.M2_SLOT_PENDING,
    )

    source = inspect.getsource(no_payment.cancel_confirm)
    assert ".with_for_update()" in source
    assert "release_slot" in source
    assert "ConsultationStatus.CANCELLED" in source
    assert "CaseStatus.M2_SLOT_PENDING" in source
    assert "await db.commit()" in source
    assert "await db.rollback()" in source
    assert "возврат не требуется" in source


def test_client_actions_do_not_claim_unperformed_legal_events():
    poa = inspect.getsource(m1_stages.poa_done)
    court = inspect.getsource(m1_stages.court_status)
    pilot_court = inspect.getsource(
        no_payment_legal.court_status_without_side_effects
    )

    assert "next_status=CaseStatus.M1_POA_RECEIVED" in _compact(poa)
    assert "next_status=CaseStatus.M1_CLAIM_SENT" not in _compact(poa)
    assert "next_status=CaseStatus.M1_WAITING_30_DAYS" not in _compact(poa)
    assert "change_status" not in court
    assert "change_status" not in pilot_court
    assert "Просмотр этого экрана не переводит дело в суд" in pilot_court


def test_m1_review_requires_ddu_and_document_service_returns_count():
    screen_source = inspect.getsource(documents.finish)
    service_source = inspect.getsource(
        document_service.DocumentService.send_documents_to_review
    )

    assert 'required_types = {"DDU"} if case.route == "M1" else set()' in screen_source
    assert "MissingRequiredDocumentsError" in service_source
    assert "required_types" in service_source
    assert "return len(verified_new)" in service_source


def test_document_completion_preserves_late_case_and_booked_consultation():
    source = inspect.getsource(documents.finish)

    assert "CaseStatus.M2_CONSULTATION_BOOKED" in source
    assert "Дата и время сохранены" in source
    assert "Текущий этап дела не изменён" in source
    assert "if not case:" in source
    assert "await db.rollback()" in source


def test_document_skip_is_limited_to_prebooking_m2_stages():
    source = inspect.getsource(documents.skip)

    assert "_M2_CAN_SKIP_STATUSES" in source
    assert "status not in _M2_CAN_SKIP_STATUSES" in source
    assert "пропуск уже не меняет статус" in source
