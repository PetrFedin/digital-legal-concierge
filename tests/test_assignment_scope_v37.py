from __future__ import annotations

import inspect

from app.admin.admin_dashboard import AdminDashboardService
from app.api import workdesk
from app.domain.cases.assignment_policy import (
    AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES,
    automatic_assignment_required,
)
from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.statuses.case_statuses import CaseStatus
from app.main import create_app


def test_automatic_assignment_starts_only_after_m1_documents_are_received():
    assert not automatic_assignment_required(CaseStatus.NEW)
    assert not automatic_assignment_required(CaseStatus.CALCULATOR_STARTED)
    assert not automatic_assignment_required(CaseStatus.CALCULATED)
    assert not automatic_assignment_required(CaseStatus.CLIENT_DECISION)
    assert not automatic_assignment_required(CaseStatus.M1_DOCUMENTS_PENDING)
    assert automatic_assignment_required(CaseStatus.M1_DOCUMENTS_RECEIVED)
    assert automatic_assignment_required(CaseStatus.M1_LAWYER_REVIEW)


def test_m2_calendar_flow_is_not_sent_to_generic_auto_assignment():
    for status in (
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
        CaseStatus.M2_DOCUMENTS_OPTIONAL,
        CaseStatus.M2_SLOT_PENDING,
        CaseStatus.M2_PAYMENT_PENDING,
        CaseStatus.M2_CONSULTATION_BOOKED,
        CaseStatus.M2_CONSULTATION_DONE,
    ):
        assert not automatic_assignment_required(status)


def test_assignment_queue_and_dashboard_use_same_policy():
    assignment_source = inspect.getsource(CaseAssignmentService.assign_queue)
    dashboard_source = inspect.getsource(AdminDashboardService.build)

    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in assignment_source
    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in dashboard_source
    assert CaseStatus.M1_DOCUMENTS_RECEIVED.value in AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES
    assert CaseStatus.M2_CONSULTATION_BOOKED.value not in AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES


def test_workdesk_adds_unassigned_reason_only_when_assignment_is_due():
    source = inspect.getsource(workdesk._attention_reasons)
    assert "automatic_assignment_required(case.status)" in source


def test_static_safe_unassigned_route_precedes_legacy_dynamic_route():
    paths = [route.path for route in create_app().routes]
    static_index = paths.index("/admin/work-queues/unassigned")
    dynamic_index = paths.index("/admin/work-queues/{queue_name}")
    assert static_index < dynamic_index
