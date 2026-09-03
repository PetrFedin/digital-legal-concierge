from __future__ import annotations

import inspect

from app.api.lawyer_consultation_decision_guard import (
    ALLOWED_COMPLETION_DECISIONS,
    guarded_complete_consultation,
    guarded_consultation_desk_html,
    guarded_consultation_desk_ui,
)
from app.api.lawyer_m1_rejection import reject_m1_case
from app.api.message_center_role_ui import role_safe_message_center_ui
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in create_app().routes
        if route.path == path and method in (route.methods or set())
    )


def test_m1_rejection_mutation_precedes_legacy_lawyer_routes():
    assert _first_route(
        "/lawyer/cases/{case_id}/reject",
        "POST",
    ).endpoint is reject_m1_case


def test_m2_completion_guard_precedes_guided_and_legacy_completion_routes():
    assert _first_route(
        "/lawyer/consultations/{consultation_id}/complete",
        "POST",
    ).endpoint is guarded_complete_consultation


def test_consultation_desk_exposes_only_deterministic_completion_choices():
    assert ALLOWED_COMPLETION_DECISIONS == frozenset(
        {"close", "to_m1", "follow_up"}
    )
    html = guarded_consultation_desk_html()
    assert 'value="close"' in html
    assert 'value="to_m1"' in html
    assert 'value="follow_up"' in html
    assert 'value="other"' not in html
    assert "законченный следующий шаг" in html
    assert _first_route(
        "/lawyer/consultation-desk/ui"
    ).endpoint is guarded_consultation_desk_ui


def test_role_safe_message_center_shell_still_requires_staff_session():
    assert _first_route("/message-center/ui").endpoint is role_safe_message_center_ui
    source = inspect.getsource(role_safe_message_center_ui)
    assert "require_staff_scope" in source
    assert 'RedirectResponse(url="/login"' in source
