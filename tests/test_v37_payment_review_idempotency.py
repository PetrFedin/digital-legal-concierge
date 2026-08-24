from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_payment_review_origin_and_resolution_evidence_do_not_age_out():
    source = read("app/domain/payments/payment_review_service.py")
    origin = source.split("async def _latest_review_origin_status", 1)[1].split(
        "async def _latest_resolution_event", 1
    )[0]
    resolution = source.split("async def _latest_resolution_event", 1)[1].split(
        "def _require_comment", 1
    )[0]

    assert ".limit(" not in origin
    assert ".limit(" not in resolution
    assert 'AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_REQUIRED"' in origin
    assert 'new_value.get("payment_id")' in origin
    assert 'AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_RESOLVED"' in resolution
    assert 'new_value.get("payment_id")' in resolution


def test_payment_review_exact_retry_matches_actor_decision_context_and_comment():
    source = read("app/domain/payments/payment_review_service.py")
    exact = source.split("async def _require_exact_retry_or_conflict", 1)[1].split(
        "async def _record_resolution", 1
    )[0]

    assert "actual_decision == str(decision).strip().lower()" in exact
    assert "actual_consultation_id == int(consultation.id)" in exact
    assert "actual_comment == str(comment).strip()" in exact
    assert "actual_actor_id == expected_actor_id" in exact
    assert "not compare_slot or actual_slot_id == expected_slot_id" in exact
    assert "PaymentReviewConflictError" in exact


def test_each_terminal_payment_review_branch_requires_exact_retry_evidence():
    source = read("app/domain/payments/payment_review_service.py")
    confirm = source.split("async def confirm_existing_booking", 1)[1].split(
        "async def assign_new_slot", 1
    )[0]
    assign = source.split("async def assign_new_slot", 1)[1].split(
        "async def route_to_refund", 1
    )[0]
    refund = source.split("async def route_to_refund", 1)[1].split(
        "async def resolve", 1
    )[0]

    assert "if payment.status == PaymentStatus.PAID:" in confirm
    assert 'decision="confirm_existing"' in confirm
    assert "compare_slot=True" in confirm
    assert "if payment.status == PaymentStatus.PAID:" in assign
    assert 'decision="assign_slot"' in assign
    assert "compare_slot=True" in assign
    assert "if payment.status == PaymentStatus.REFUND_PENDING:" in refund
    assert 'decision="refund_pending"' in refund


def test_payment_review_ui_uses_durable_origin_and_preserves_stale_decision_draft():
    source = read("app/api/payment_review_center.py")
    review_context = source.split("async def review_event_context", 1)[1].split(
        "def allowed_actions_for_candidate", 1
    )[0]

    assert ".limit(" not in review_context
    assert 'new_value.get("payment_id")' in review_context
    assert "e.status=r.status" in source
    assert "reviewDrafts=new Map()" in source
    assert "restoreDraftSelections" in source
    assert "consultationId,slotId" in source
    assert "reviewDrafts.delete(Number(id))" in source
    assert "if(e.status===409)" in source
    assert "await load()" in source
    assert "решение не применено" in source
    assert "Ваш допустимый выбор и комментарий сохранены" in source


def test_payment_review_card_matches_staff_action_hierarchy():
    source = read("app/api/payment_review_center.py")

    assert '<div class="section-label">Сейчас</div>' in source
    assert '<div class="section-label">Главный следующий шаг</div>' in source
    assert '<div class="section-label">Решение</div>' in source
    assert '<div class="section-label">Вторичные действия</div>' in source
