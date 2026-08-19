from __future__ import annotations

import inspect

from app.api.consultation_outcomes_product import consultation_outcomes_ui
from app.api.guided_consultation_outcomes import (
    _CLIENT_NO_SHOW_UI_PATCH,
    _client_no_show_rows,
    close_after_client_no_show,
    guided_outcome_queue,
    rebook_after_client_no_show,
)
from app.domain.consultations.client_no_show_resolution_service import (
    ClientNoShowResolutionService,
)
from app.domain.notifications.notification_rules import NOTIFICATION_RULES
from app.domain.notifications.notification_templates import TEMPLATES
from app.domain.statuses.case_statuses import CaseStatus
from app.main import create_app


def _first_endpoint(path: str, method: str = "GET"):
    for route in create_app().routes:
        if route.path == path and method in (route.methods or set()):
            return route.endpoint
    return None


def test_client_no_show_paths_have_one_product_router_owner():
    assert _first_endpoint("/admin/consultation-outcomes") is guided_outcome_queue
    assert _first_endpoint("/admin/consultation-outcomes/ui") is consultation_outcomes_ui
    assert (
        _first_endpoint(
            "/admin/consultation-outcomes/{consultation_id}/client-no-show/rebook",
            "POST",
        )
        is rebook_after_client_no_show
    )
    assert (
        _first_endpoint(
            "/admin/consultation-outcomes/{consultation_id}/client-no-show/close",
            "POST",
        )
        is close_after_client_no_show
    )


def test_client_no_show_has_two_explicit_terminal_next_actions():
    assert "Открыть новую запись" in _CLIENT_NO_SHOW_UI_PATCH
    assert "Закрыть обращение" in _CLIENT_NO_SHOW_UI_PATCH
    assert "новой оплат" in _CLIENT_NO_SHOW_UI_PATCH.lower()
    assert "payment_reused" in inspect.getsource(rebook_after_client_no_show)

    service_source = inspect.getsource(ClientNoShowResolutionService)
    assert "client_no_show_rebook" in service_source
    assert "client_no_show_closed" in service_source
    assert "old_payment_reused" in service_source
    assert "CaseStatus.M2_SLOT_PENDING" in service_source
    assert "CaseStatus.M2_CLOSED" in service_source


def test_resolved_client_no_show_does_not_remain_in_admin_queue():
    source = inspect.getsource(_client_no_show_rows)
    assert 'Case.route == "M2"' in source
    assert "CaseStatus.M2_CLOSED.value" in source
    assert "CaseStatus.ARCHIVED.value" in source


def test_client_is_notified_after_admin_resolves_no_show():
    rebook = NOTIFICATION_RULES["CONSULTATION_CLIENT_NO_SHOW_REBOOKING_OPENED"]
    closed = NOTIFICATION_RULES["CONSULTATION_CLIENT_NO_SHOW_CASE_CLOSED"]
    assert "client" in rebook["recipients"]
    assert "client" in closed["recipients"]
    assert "consultation_client_no_show_rebooking_opened" in TEMPLATES
    assert "consultation_client_no_show_case_closed" in TEMPLATES
