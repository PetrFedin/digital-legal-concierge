from __future__ import annotations

import inspect

from fastapi.routing import iter_route_contexts

from app.api.lawyer_m1_rejection import reject_m1_case
from app.api.lawyer_workspace_rejection_ui import (
    _M1_REJECTION_PATCH,
    lawyer_workspace_with_rejection_ui,
)
from app.domain.cases.case_transition_policy import allowed_next_statuses
from app.domain.cases.sla_service import PAUSED_CASE_STATUSES
from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_templates import TEMPLATES
from app.domain.statuses.case_statuses import CaseStatus
from app.main import create_app


def _first_endpoint(path: str, method: str = "GET"):
    for route in iter_route_contexts(create_app().routes):
        if route.path == path and method in (route.methods or set()):
            return route.endpoint
    return None


def test_lawyer_rejection_workspace_and_api_are_mounted_first():
    assert _first_endpoint("/lawyer/workspace/ui") is lawyer_workspace_with_rejection_ui
    assert (
        _first_endpoint("/lawyer/cases/{case_id}/reject", "POST")
        is reject_m1_case
    )


def test_m1_review_and_docs_request_can_reject_without_forcing_m2():
    assert CaseStatus.M1_REJECTED in allowed_next_statuses(CaseStatus.M1_LAWYER_REVIEW)
    assert CaseStatus.M1_REJECTED in allowed_next_statuses(CaseStatus.M1_DOCS_REQUESTED)
    assert "Отказать в полном ведении" in _M1_REJECTION_PATCH
    assert "клиент получит выбор" in _M1_REJECTION_PATCH
    assert "перейти в консультацию или завершить обращение" in _M1_REJECTION_PATCH
    assert "/lawyer/cases/${id}/reject" in _M1_REJECTION_PATCH
    assert "expected_status:x.status" in _M1_REJECTION_PATCH
    assert "expected_updated_at:x.updated_at" in _M1_REJECTION_PATCH


def test_rejection_backend_locks_snapshot_records_sla_and_notifies_client():
    source = inspect.getsource(reject_m1_case)
    assert "for_update=True" in source
    assert "assert_case_snapshot" in source
    assert "CaseStatus.M1_REJECTED" in source
    assert "M1_CASE_REJECTED" in source
    assert "CaseSLAService" in source
    assert "NotificationEngine" in source
    assert "next_action" in source

    rule = NOTIFICATION_RULES["M1_CASE_REJECTED"]
    assert "client" in rule["recipients"]
    assert "admin" in rule["recipients"]
    assert rule["template"] == "m1_case_rejected"
    assert "m1_case_rejected" in TEMPLATES


def test_sla_pauses_while_client_decides_after_rejection():
    assert CaseStatus.M1_REJECTED.value in PAUSED_CASE_STATUSES
