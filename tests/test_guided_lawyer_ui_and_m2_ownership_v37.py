from __future__ import annotations

import inspect

from app.api import guided_lawyer_ui
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in create_app().routes
        if route.path == path and method in (route.methods or set())
    )


def test_m2_consultation_actions_use_guided_slot_owned_handlers_first():
    complete = _first_route("/lawyer/consultations/{consultation_id}/complete", "POST")
    no_show = _first_route(
        "/lawyer/consultations/{consultation_id}/client-no-show",
        "POST",
    )

    assert complete.endpoint.__name__ == "guided_complete_consultation"
    assert no_show.endpoint.__name__ == "guided_client_no_show"


def test_m2_outcome_does_not_require_generic_case_assignment():
    helper = inspect.getsource(guided_lawyer_ui._case_after_consultation_outcome)
    complete = inspect.getsource(guided_lawyer_ui.guided_complete_consultation)

    assert "case.assigned_lawyer_id == lawyer_id" in helper
    assert "assigned_case(" not in complete
    assert "ConsultationOutcomeService(db).complete" in complete


def test_consultation_desk_preserves_unsaved_result_and_decision():
    route = _first_route("/lawyer/consultation-desk/ui")
    assert route.endpoint.__name__ == "guided_consultation_desk_ui"
    html = guided_lawyer_ui._inject_patch(
        "<html><body></body></html>",
        guided_lawyer_ui._CONSULTATION_DRAFT_PATCH,
    )

    assert "sessionStorage.setItem" in html
    assert "sessionStorage.getItem" in html
    assert "originalClose" in html
    assert "draft.decision" in html


def test_lawyer_workspace_case_id_deep_link_focus_is_active():
    route = _first_route("/lawyer/workspace/ui")
    assert route.endpoint.__name__ == "guided_lawyer_workspace_ui"
    patch = guided_lawyer_ui._WORKSPACE_DEEP_LINK_PATCH

    assert "URLSearchParams" in patch
    assert "params.get('case_id')" in patch
    assert "currentTab='cases'" in patch
    assert "scrollIntoView" in patch
