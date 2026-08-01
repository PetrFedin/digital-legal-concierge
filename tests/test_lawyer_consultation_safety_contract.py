from __future__ import annotations

import inspect
import re

import pytest

from app.api.lawyer import (
    LAWYER_HTML,
    client_no_show,
    complete_consultation,
)
from app.domain.consultations.outcome_service import ConsultationOutcomeService


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _action_function(name: str) -> str:
    start = LAWYER_HTML.index(f"async function {name}(")
    end = LAWYER_HTML.find("\nasync function ", start + 1)
    if end < 0:
        end = LAWYER_HTML.index("\nboot();", start)
    return LAWYER_HTML[start:end]


def test_lawyer_outcomes_lock_all_controls_for_the_consultation():
    compact = _compact(LAWYER_HTML)

    assert "constpendingConsultations=newSet()" in compact
    assert "asyncfunctionwithConsultationAction(id,button,work)" in compact
    assert "pendingConsultations.has(id)" in compact
    assert "pendingConsultations.add(id)" in compact
    assert "pendingConsultations.delete(id)" in compact
    assert ".disabled=true" in compact
    assert ".disabled=false" in compact
    assert "finally" in compact
    assert compact.count("data-consultation-id=") >= 3
    assert compact.count(",this)") >= 2


def test_lawyer_decision_select_exactly_matches_backend_decisions():
    options = set(
        re.findall(
            r'<option value="(close|to_m1|follow_up|other)">',
            LAWYER_HTML,
        )
    )
    assert options == ConsultationOutcomeService.VALID_DECISIONS
    assert "prompt('Решение:" not in LAWYER_HTML


@pytest.mark.parametrize(
    ("function_name", "minimum_length", "success_marker", "failure_marker"),
    (
        ("completeConsultation", 20, "Результат консультации", "не сохранён"),
        ("noShow", 5, "Неявка клиента", "не сохранена"),
    ),
)
def test_lawyer_outcomes_validate_confirm_and_report_exact_results(
    function_name: str,
    minimum_length: int,
    success_marker: str,
    failure_marker: str,
):
    function = _action_function(function_name)
    compact = _compact(function)

    assert f".trim().length<{minimum_length}" in compact
    assert function.index("confirm(") < function.index("withConsultationAction(")
    assert function.index("withConsultationAction(") < function.index("await api(")
    assert success_marker in function
    assert failure_marker in function
    assert "список не обновился" in function
    assert "withConsultationAction(id,button" in compact


def test_lawyer_feedback_is_accessible_and_load_errors_are_visible():
    compact = _compact(LAWYER_HTML)

    assert 'role="status"' in LAWYER_HTML
    assert 'aria-live="polite"' in LAWYER_HTML
    assert "if(!r.ok)throw" in compact
    assert "try{awaitload()}catch(e)" in compact
    assert "feedback(e.message,'bad')" in compact


def test_outcome_service_restricts_actor_and_serializes_both_results():
    complete = _compact(inspect.getsource(ConsultationOutcomeService.complete))
    no_show = _compact(
        inspect.getsource(ConsultationOutcomeService.mark_client_no_show)
    )
    actor_check = inspect.getsource(
        ConsultationOutcomeService._require_assigned_lawyer
    )
    lock = inspect.getsource(ConsultationOutcomeService._lock_consultation)

    assert "consultation.lawyer_id!=lawyer_id" in _compact(actor_check)
    assert ".with_for_update()" in lock
    assert "consultation.status==ConsultationStatus.DONE" in complete
    assert "consultation.status!=ConsultationStatus.BOOKED" in complete
    assert "consultation.status==ConsultationStatus.CLIENT_NO_SHOW" in no_show
    assert "consultation.status!=ConsultationStatus.BOOKED" in no_show
    assert "self._lock_case(" in inspect.getsource(ConsultationOutcomeService.complete)
    assert "self._lock_slot(" in inspect.getsource(ConsultationOutcomeService.complete)
    assert "self._lock_case(" in inspect.getsource(
        ConsultationOutcomeService.mark_client_no_show
    )
    assert "self._lock_slot(" in inspect.getsource(
        ConsultationOutcomeService.mark_client_no_show
    )


@pytest.mark.parametrize("endpoint", (complete_consultation, client_no_show))
def test_lawyer_outcome_endpoints_rollback_every_failure(endpoint):
    source = inspect.getsource(endpoint)
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 3
