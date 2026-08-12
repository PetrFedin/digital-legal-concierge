from __future__ import annotations

import inspect

from app.api.guided_refund_center import guided_refund_ui, refund_context
from app.domain.payments.payment_webhook_service import (
    M1_EXPECTED_PAYMENT_CASE_STATUSES,
    PaymentWebhookService,
)
from app.domain.payments.refund_service import ConsultationRefundService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in create_app().routes
        if route.path == path and method in (route.methods or set())
    )


def test_each_m1_payment_has_one_exact_expected_case_stage():
    assert M1_EXPECTED_PAYMENT_CASE_STATUSES == {
        PaymentCode.M1_INITIAL_PAYMENT: CaseStatus.M1_WAITING_PAYMENT_30000,
        PaymentCode.M1_COURT_PAYMENT: CaseStatus.M1_WAITING_PAYMENT_70000,
        PaymentCode.M1_SUCCESS_FEE: CaseStatus.M1_WAITING_SUCCESS_FEE,
    }


def test_stale_m1_success_is_routed_to_refund_without_case_transition():
    source = inspect.getsource(PaymentWebhookService._mark_stale_m1_payment_refund)
    assert "PaymentStatus.REFUND_PENDING" in source
    assert "M1_STALE_PAYMENT_REFUND_REQUIRED" in source
    assert "change_status" not in source


def test_shared_refund_resolution_supports_m1_and_m2():
    source = inspect.getsource(ConsultationRefundService.resolve_refund)
    assert "is_consultation" in source
    assert "M1_PAYMENT_REFUND_COMPLETED" in source
    assert "CONSULTATION_REFUND_COMPLETED" in source


def test_guided_refund_ui_precedes_old_consultation_named_screen():
    assert _first_route("/admin/refunds/ui").endpoint is guided_refund_ui
    assert _first_route("/admin/refunds/context").endpoint is refund_context
