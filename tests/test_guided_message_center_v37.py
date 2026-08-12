from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.api import guided_message_center
from app.api.guided_message_center import CaseResponsibility
from app.api.message_center import StaffScope
from app.api.message_center_role_ui import role_safe_message_center_ui
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in create_app().routes
        if route.path == path and method in (route.methods or set())
    )


def _scope(*, lawyer_id: int | None):
    return StaffScope(
        payload={},
        roles=frozenset({"lawyer" if lawyer_id is not None else "admin"}),
        lawyer_id=lawyer_id,
    )


def _responsibility(
    *,
    lawyer_id: int | None = None,
    mode: str = "consultation_slot",
    assignment_required: bool = False,
):
    return CaseResponsibility(
        mode=mode,
        lawyer_id=lawyer_id,
        lawyer_name="Иван Юрист" if lawyer_id else None,
        consultation_id=44 if mode == "consultation_slot" else None,
        consultation_status="BOOKED" if lawyer_id else "SLOT_PENDING",
        assignment_required=assignment_required,
        responsibility_pending=lawyer_id is None,
    )


def _case(*, status="M2_CONSULTATION_BOOKED", route="M2"):
    return SimpleNamespace(status=status, route=route)


def test_route_aware_message_handlers_precede_legacy_handlers():
    assert _first_route("/message-center/status").endpoint.__name__ == (
        "guided_message_center_status"
    )
    assert _first_route(
        "/message-center/cases/{case_id}/messages"
    ).endpoint.__name__ == "guided_case_messages"
    assert _first_route(
        "/message-center/cases/{case_id}/reply", "POST"
    ).endpoint.__name__ == "guided_reply_to_client"
    assert _first_route("/message-center/ui").endpoint is role_safe_message_center_ui


def test_m2_consultation_lawyer_can_access_own_conversation_without_case_assignment():
    responsibility = _responsibility(lawyer_id=7)

    assert guided_message_center._allows_case(_scope(lawyer_id=7), responsibility)
    assert not guided_message_center._allows_case(
        _scope(lawyer_id=8), responsibility
    )
    assert guided_message_center._reply_allowed(
        _scope(lawyer_id=7),
        _case(),
        responsibility,
    )


def test_m2_support_before_slot_does_not_force_random_m1_assignment():
    responsibility = _responsibility(lawyer_id=None)

    assert responsibility.assignment_required is False
    assert guided_message_center._reply_allowed(
        _scope(lawyer_id=None),
        _case(status="M2_SLOT_PENDING"),
        responsibility,
    )


def test_actionable_m1_without_lawyer_blocks_reply_until_assignment():
    responsibility = _responsibility(
        lawyer_id=None,
        mode="case_assignment",
        assignment_required=True,
    )

    assert not guided_message_center._reply_allowed(
        _scope(lawyer_id=None),
        _case(status="M1_LAWYER_REVIEW", route="M1"),
        responsibility,
    )


def test_completed_case_conversation_is_read_only_for_every_role():
    responsibility = _responsibility(lawyer_id=7)
    closed_case = _case(status="M2_CLOSED")

    assert not guided_message_center._reply_allowed(
        _scope(lawyer_id=7),
        closed_case,
        responsibility,
    )
    assert not guided_message_center._reply_allowed(
        _scope(lawyer_id=None),
        closed_case,
        responsibility,
    )


def test_message_center_ui_explains_m2_responsibility_and_has_safe_return_path():
    patch = guided_message_center._MESSAGE_CENTER_ROUTE_PATCH

    assert "Юрист консультации" in patch
    assert "ответственный определяется слотом" in patch
    assert "Дело завершено" in patch
    assert "href=\"/operator\"" in patch


def test_broad_staff_reply_is_not_written_under_another_lawyer_identity():
    source = inspect.getsource(guided_message_center.guided_reply_to_client)

    assert "message_lawyer_id = None" in source
    assert "Администратор отправляет сообщение от имени команды" in source
    assert "message_lawyer_id = responsibility.lawyer_id" not in source


def test_cancelled_or_rescheduled_m2_slot_is_not_current_responsibility():
    statuses = guided_message_center._M2_CURRENT_RESPONSIBILITY_STATUSES

    assert "BOOKED" in statuses
    assert "PAYMENT_PENDING" in statuses
    assert "CANCELLED" not in statuses
    assert "RESCHEDULED" not in statuses
