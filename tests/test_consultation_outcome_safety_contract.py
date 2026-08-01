from __future__ import annotations

import inspect
import re

import pytest

from app.api.consultation_outcomes import (
    OUTCOMES_HTML,
    mark_lawyer_no_show,
    rebook_after_lawyer_no_show,
    refund_after_lawyer_no_show,
)
from app.domain.consultations.no_show_resolution_service import (
    NoShowResolutionService,
)
from app.domain.consultations.outcome_service import ConsultationOutcomeService


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _action_function(name: str) -> str:
    start = OUTCOMES_HTML.index(f"async function {name}(")
    end = OUTCOMES_HTML.find("\nasync function ", start + 1)
    if end < 0:
        end = OUTCOMES_HTML.index("\nboot();", start)
    return OUTCOMES_HTML[start:end]


def test_consultation_decisions_lock_the_entire_action_group():
    compact = _compact(OUTCOMES_HTML)

    assert "constpendingConsultations=newSet()" in compact
    assert "asyncfunctionwithConsultationAction(id,button,work)" in compact
    assert "pendingConsultations.has(id)" in compact
    assert "pendingConsultations.add(id)" in compact
    assert "pendingConsultations.delete(id)" in compact
    assert "consultationControls(id)" in compact
    assert ".disabled=true" in compact
    assert ".disabled=false" in compact
    assert "finally" in compact
    assert compact.count("data-consultation-id=") >= 4
    assert compact.count(",this)") >= 3


@pytest.mark.parametrize(
    ("function_name", "success_marker", "failure_marker"),
    (
        ("markNoShow", "зафиксирована", "не сохранена"),
        ("rebook", "без повторной оплаты", "не сохранён"),
        ("refund", "направлен в очередь возврата", "не сохранено"),
    ),
)
def test_consultation_decisions_confirm_before_request_and_report_outcomes(
    function_name: str,
    success_marker: str,
    failure_marker: str,
):
    function = _action_function(function_name)
    compact = _compact(function)

    assert function.index("confirm(") < function.index("withConsultationAction(")
    assert function.index("withConsultationAction(") < function.index("await api(")
    assert "validComment(comment)" in compact
    assert success_marker in function
    assert failure_marker in function
    assert "список не обновился" in function
    assert "withConsultationAction(id,button" in compact


def test_consultation_feedback_is_accessible_and_http_failures_are_not_successes():
    compact = _compact(OUTCOMES_HTML)

    assert 'role="status"' in OUTCOMES_HTML
    assert 'aria-live="polite"' in OUTCOMES_HTML
    assert "if(!r.ok)throw" in compact
    assert "comment.trim().length<5" in compact
    assert "try{awaitload()}catch(e)" in compact


def test_refund_action_explains_that_provider_refund_is_separate():
    function = _action_function("refund")
    assert "Это не выполняет банковский возврат автоматически" in function
    assert "очередь возврата" in function


def test_consultation_outcome_service_locks_consultation_case_and_slot():
    consultation_lock = inspect.getsource(
        ConsultationOutcomeService._lock_consultation
    )
    case_lock = inspect.getsource(ConsultationOutcomeService._lock_case)
    slot_lock = inspect.getsource(ConsultationOutcomeService._lock_slot)

    assert ".with_for_update()" in consultation_lock
    assert ".with_for_update()" in case_lock
    assert "get_slot_for_update" in slot_lock


def test_lawyer_no_show_and_rebook_reject_stale_or_conflicting_states():
    no_show = _compact(
        inspect.getsource(ConsultationOutcomeService.mark_lawyer_no_show)
    )
    rebook = _compact(
        inspect.getsource(
            ConsultationOutcomeService.rebook_after_lawyer_no_show
        )
    )

    assert (
        "consultation.status==ConsultationStatus.LAWYER_NO_SHOW"
        in no_show
    )
    assert "consultation.status!=ConsultationStatus.BOOKED" in no_show
    assert (
        "consultation.status!=ConsultationStatus.LAWYER_NO_SHOW"
        in rebook
    )
    assert "book_available_slot" in rebook


def test_no_show_refund_locks_related_records_and_is_idempotent():
    source = inspect.getsource(
        NoShowResolutionService.route_lawyer_no_show_to_refund
    )
    compact = _compact(source)

    assert source.count(".with_for_update()") >= 3
    assert "consultation.status==ConsultationStatus.CANCELLED" in compact
    assert "payment.status==PaymentStatus.REFUND_PENDING" in compact
    assert (
        "consultation.status!=ConsultationStatus.LAWYER_NO_SHOW"
        in compact
    )
    assert "payment.status=PaymentStatus.REFUND_PENDING" in compact


@pytest.mark.parametrize(
    "endpoint",
    (
        mark_lawyer_no_show,
        rebook_after_lawyer_no_show,
        refund_after_lawyer_no_show,
    ),
)
def test_consultation_outcome_endpoints_rollback_unexpected_failures(endpoint):
    source = inspect.getsource(endpoint)
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 3
