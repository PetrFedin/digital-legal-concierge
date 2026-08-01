from __future__ import annotations

import inspect
import re

import pytest

from app.api.payment_review_center import (
    PAYMENT_REVIEW_CENTER_HTML,
    resolve_payment_review,
)
from app.api.refund_center import REFUND_CENTER_HTML, resolve_refund
from app.domain.payments.payment_review_service import PaymentReviewService
from app.domain.payments.refund_service import ConsultationRefundService


FINANCIAL_INTERFACES = (
    ("payment review", PAYMENT_REVIEW_CENTER_HTML, "resolveReview", 3),
    ("refund center", REFUND_CENTER_HTML, "resolveRefund", 2),
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _action_function(html: str, name: str) -> str:
    start = html.index(f"async function {name}(")
    end = html.index("\nboot();", start)
    return html[start:end]


@pytest.mark.parametrize(
    ("label", "html", "function_name", "minimum_action_buttons"),
    FINANCIAL_INTERFACES,
)
def test_financial_decisions_lock_the_entire_payment_action_group(
    label: str,
    html: str,
    function_name: str,
    minimum_action_buttons: int,
):
    compact = _compact(html)
    function = _action_function(html, function_name)

    assert "constpendingPayments=newSet()" in compact, label
    assert "asyncfunctionwithPaymentAction(id,button,work)" in compact, label
    assert "pendingPayments.has(id)" in compact, label
    assert "pendingPayments.add(id)" in compact, label
    assert "pendingPayments.delete(id)" in compact, label
    assert "finally" in function or "finally" in html, label
    assert ".disabled=true" in compact, label
    assert ".disabled=false" in compact, label
    assert compact.count("data-payment-id=") >= minimum_action_buttons, label
    assert compact.count(",this)") >= minimum_action_buttons, label
    assert "withPaymentAction(id,button" in _compact(function), label


@pytest.mark.parametrize(
    ("label", "html", "function_name", "minimum_action_buttons"),
    FINANCIAL_INTERFACES,
)
def test_financial_decisions_require_confirmation_and_report_exact_outcomes(
    label: str,
    html: str,
    function_name: str,
    minimum_action_buttons: int,
):
    del minimum_action_buttons
    function = _action_function(html, function_name)
    compact = _compact(function)
    html_compact = _compact(html)

    confirmation = function.index("confirm(")
    single_flight = function.index("withPaymentAction(")
    request = function.index("await api(")
    assert confirmation < single_flight < request, label
    assert "comment.trim().length<5" in compact, label
    assert "не сохранено" in function, label
    assert "сохранено, но список не обновился" in function, label
    assert "role=\"status\"" in html, label
    assert "aria-live=\"polite\"" in html, label
    assert "if(!r.ok)throw" in html_compact, label


def test_refund_completion_explicitly_requires_external_provider_confirmation():
    function = _action_function(REFUND_CENTER_HTML, "resolveRefund")
    assert "деньги по платежу" in function
    assert "фактически возвращены через платёжного провайдера" in function
    assert "Эта кнопка только фиксирует результат в системе" in function


def test_payment_review_backend_serializes_and_rejects_stale_decisions():
    source = inspect.getsource(PaymentReviewService)
    compact = _compact(source)

    assert ".with_for_update()" in source
    assert "payment.status!=PaymentStatus.PAID_REVIEW" in compact
    assert "payment.status==PaymentStatus.PAID" in compact
    assert "payment.status==PaymentStatus.REFUND_PENDING" in compact
    assert "PaymentReviewResolutionError" in source


def test_refund_backend_serializes_and_rejects_conflicting_decisions():
    source = inspect.getsource(ConsultationRefundService.resolve_refund)
    compact = _compact(source)

    assert ".with_for_update()" in source
    assert "payment.status!=PaymentStatus.REFUND_PENDING" in compact
    assert "payment.status==PaymentStatus.REFUNDED" in compact
    assert "payment.status==PaymentStatus.REFUND_DECLINED" in compact
    assert "normalized_decisionnotin{\"refunded\",\"declined\"}" in compact


@pytest.mark.parametrize("endpoint", (resolve_payment_review, resolve_refund))
def test_financial_endpoints_rollback_unexpected_failures(endpoint):
    source = inspect.getsource(endpoint)
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 3
