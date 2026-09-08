from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_refund_resolution_uses_unbounded_exact_payment_audit_evidence():
    source = read("app/domain/payments/refund_service.py")
    lookup = source.split("async def _latest_payment_history_event", 1)[1].split(
        "async def _require_exact_resolution_retry_or_conflict", 1
    )[0]
    exact_retry = source.split(
        "async def _require_exact_resolution_retry_or_conflict", 1
    )[1].split("async def _require_exact_reopen_retry_or_conflict", 1)[0]

    assert ".limit(" not in lookup
    assert 'AuditLog.entity_type == "case"' in lookup
    assert "AuditLog.entity_id == int(case_id)" in lookup
    assert "AuditLog.action == str(action)" in lookup
    assert 'new_value.get("payment_id")' in lookup
    assert "actual_decision == str(decision).strip().lower()" in exact_retry
    assert "actual_comment == str(comment).strip()" in exact_retry
    assert "actual_actor_id == expected_actor_id" in exact_retry
    assert "RefundResolutionConflictError" in exact_retry


def test_refund_reopen_distinguishes_exact_network_retry_from_stale_admin_card():
    source = read("app/domain/payments/refund_service.py")
    reopen_guard = source.split(
        "async def _require_exact_reopen_retry_or_conflict", 1
    )[1].split("async def _lock_consultation", 1)[0]
    reopen = source.split("async def reopen_declined_refund", 1)[1].split(
        "async def resolve_refund", 1
    )[0]

    assert 'new_value.get("status")' in reopen_guard
    assert "PaymentStatus.REFUND_PENDING.value" in reopen_guard
    assert "actual_comment == str(comment).strip()" in reopen_guard
    assert "actual_actor_id == expected_actor_id" in reopen_guard
    assert "RefundResolutionConflictError" in reopen_guard
    assert "if payment.status == PaymentStatus.REFUND_PENDING:" in reopen
    assert "_require_exact_reopen_retry_or_conflict(" in reopen


def test_refund_api_maps_stale_domain_decision_to_conflict_and_preserves_draft():
    source = read("app/api/refund_center.py")

    assert "except ValueError as error:" in source
    assert "HTTPException(status_code=409" in source
    assert "e.status=r.status" in source
    assert "refundDrafts=new Map()" in source
    assert "refundDrafts.delete(Number(id))" in source
    assert "if(e.status===409)" in source
    assert "await load()" in source
    assert "решение не применено" in source
    assert "Ваш комментарий сохранён в этой вкладке" in source


def test_refund_retry_has_one_canonical_domain_owner_and_same_stale_recovery():
    source = read("app/api/refund_resolution_guard_impl.py")
    retry = source.split("async def retry_declined_refund", 1)[1].split(
        "_REFUND_RETRY_UI_PATCH", 1
    )[0]
    patch = source.split("_REFUND_RETRY_UI_PATCH", 1)[1]

    assert "ConsultationRefundService(db).reopen_declined_refund(" in retry
    assert "PaymentLifecycleService.transition" not in retry
    assert "except ValueError as error:" in retry
    assert "HTTPException(status_code=409" in retry
    assert "<b>Сейчас.</b>" in patch
    assert "<b>Главный следующий шаг</b>" in patch
    assert "Вторичные действия" in patch
    assert "if(e.status===409)" in patch
    assert "повтор не применён" in patch
    assert "Ваш комментарий сохранён в этой вкладке" in patch
