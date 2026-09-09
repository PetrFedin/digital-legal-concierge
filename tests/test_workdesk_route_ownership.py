from __future__ import annotations

from collections import Counter

from fastapi.routing import iter_route_contexts

from app.api.assignment_queue import (
    actionable_unassigned_queue,
    consultation_queue_with_slot_lawyer,
    workdesk_case_responsibility,
)
from app.api.m1_internal_payment_recovery import (
    recover_payment_stage,
    recovery_context,
    recovery_ui,
)
from app.api.workdesk import (
    workdesk_case_consultation_outcomes,
    workdesk_case_consultations_today,
    workdesk_case_documents,
    workdesk_case_sla,
)
from app.api.workdesk_integrity_guard import workdesk_integrity_guard
from app.api.workdesk_projections import (
    guarded_active_work_queue,
    guarded_workdesk_attention,
    guarded_workdesk_case_action,
)
from app.api.workdesk_runtime_ui import workdesk_runtime_ui
from app.api.workdesk_timeline import workdesk_case_timeline
from app.api.workdesk_ui_guard import router as retired_workdesk_ui_guard_router
from app.main import create_app


def _routes():
    return [
        route
        for route in iter_route_contexts(create_app().routes)
        if route.path.startswith("/admin/workdesk")
        or route.path.startswith("/admin/work-queues")
    ]


def _endpoint(path: str, method: str = "GET"):
    matches = [
        route
        for route in _routes()
        if route.path == path and method in (route.methods or set())
    ]
    assert len(matches) == 1, f"{method} {path} has {len(matches)} runtime owners"
    return matches[0].endpoint


def test_workdesk_runtime_has_no_duplicate_method_path_pairs():
    pairs = Counter(
        (method, route.path)
        for route in _routes()
        for method in (route.methods or set())
        if method not in {"HEAD", "OPTIONS"}
    )
    duplicates = {pair: count for pair, count in pairs.items() if count > 1}
    assert duplicates == {}


def test_retired_workdesk_ui_guard_has_no_public_routes():
    assert retired_workdesk_ui_guard_router.routes == []


def test_workdesk_ui_and_daily_actions_have_explicit_product_owners():
    assert _endpoint("/admin/workdesk/ui") is workdesk_runtime_ui
    assert _endpoint("/admin/workdesk/attention") is guarded_workdesk_attention
    assert _endpoint("/admin/work-queues/active") is guarded_active_work_queue
    assert (
        _endpoint("/admin/workdesk/cases/{case_id}/documents")
        is workdesk_case_documents
    )
    assert (
        _endpoint("/admin/workdesk/cases/{case_id}/consultation-outcomes")
        is workdesk_case_consultation_outcomes
    )
    assert (
        _endpoint("/admin/workdesk/cases/{case_id}/consultations-today")
        is workdesk_case_consultations_today
    )
    assert _endpoint("/admin/workdesk/cases/{case_id}/sla") is workdesk_case_sla
    assert (
        _endpoint("/admin/workdesk/cases/{case_id}/action/{task}")
        is guarded_workdesk_case_action
    )
    assert _endpoint("/admin/workdesk/cases/{case_id}/timeline") is workdesk_case_timeline
    assert _endpoint("/admin/workdesk/integrity") is workdesk_integrity_guard


def test_m1_payment_recovery_has_one_exact_runtime_owner_per_operation():
    path = "/admin/workdesk/cases/{case_id}/recover-payment-stage"
    assert _endpoint(path, "GET") is recovery_context
    assert _endpoint(path, "POST") is recover_payment_stage
    assert _endpoint(f"{path}/ui", "GET") is recovery_ui


def test_assignment_projections_are_exact_routes_not_shadow_precedence():
    assert (
        _endpoint("/admin/workdesk/cases/{case_id}/responsibility")
        is workdesk_case_responsibility
    )
    assert _endpoint("/admin/work-queues/unassigned") is actionable_unassigned_queue
    assert (
        _endpoint("/admin/work-queues/consultations")
        is consultation_queue_with_slot_lawyer
    )
