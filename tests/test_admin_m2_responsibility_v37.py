from __future__ import annotations

import inspect

from app.api import assignment_queue
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in create_app().routes
        if route.path == path and method in (route.methods or set())
    )


def test_guided_workdesk_ui_precedes_legacy_workdesk_route():
    route = _first_route("/admin/workdesk/ui")
    assert route.endpoint.__name__ == "guided_workdesk_ui"


def test_m2_responsibility_comes_from_consultation_lawyer_not_case_assignment():
    source = inspect.getsource(assignment_queue.workdesk_case_responsibility)

    assert 'str(case.route or "") == "M2"' in source
    assert "Lawyer.id == Consultation.lawyer_id" in source
    assert '"mode": "consultation_slot"' in source


def test_workdesk_m2_card_relabels_assignment_and_hides_sla_controls():
    patch = assignment_queue._WORKDESK_RESPONSIBILITY_PATCH

    assert "Юрист консультации" in patch
    assert "Контроль консультации" in patch
    assert "Время консультации" in patch
    assert "Назначить перед SLA" in patch
    assert "node.remove()" in patch
