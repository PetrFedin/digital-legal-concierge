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


def test_orphan_refund_retry_requires_same_actor_comment_and_encoded_context():
    source = read("app/domain/payments/orphan_payment_review_service.py")
    exact = source.split("async def _require_exact_retry_or_conflict", 1)[1].split(
        "async def route_to_refund", 1
    )[0]
    route = source.split("async def route_to_refund", 1)[1]

    assert "PaymentReviewService(self.db)._latest_resolution_event(" in exact
    assert 'actual_decision == "refund_orphan"' in exact
    assert "actual_orphan_consultation_id == int(linked_consultation_id)" in exact
    assert "actual_orphan_slot_id == int(linked_slot_id or 0)" in exact
    assert "actual_comment == str(comment).strip()" in exact
    assert "actual_actor_id == expected_actor_id" in exact
    assert "if payment.status == PaymentStatus.REFUND_PENDING:" in route
    assert "_require_exact_retry_or_conflict(" in route


def test_payment_review_resolution_is_durable_case_audit_history():
    service = read("app/domain/payments/payment_review_service.py")
    history = read("app/domain/cases/case_history.py")
    audit_center = read("app/api/audit_center.py")
    record = service.split("async def _record_resolution", 1)[1].split(
        "async def confirm_existing_booking", 1
    )[0]

    assert "await add_case_history_event(" in record
    assert 'action="CONSULTATION_PAYMENT_REVIEW_RESOLVED"' in record
    assert '"payment_id": payment.id' in record
    assert '"consultation_id": consultation.id' in record
    assert '"decision": decision' in record
    assert "comment=comment" in record

    # add_case_history_event is the canonical case audit writer: Payment Review
    # therefore lands in the same immutable AuditLog chain as other case history.
    assert "AuditLog(" in history
    assert "entity_type='case'" in history
    assert "entity_id=case_id" in history
    assert "action=action" in history

    # Audit Center deliberately has no action allow-list, so this new action is
    # visible to the superadmin audit-history without another projection layer.
    assert "select(AuditLog)" in audit_center
    assert ".order_by(AuditLog.chain_sequence.desc(), AuditLog.id.desc())" in audit_center
    assert "AuditLog.action.in_(" not in audit_center
    assert '"action": audit.action' in audit_center
    assert '"entity_id": audit.entity_id' in audit_center
    assert '"comment": audit.comment' in audit_center


def test_payment_review_409_rolls_back_before_returning_conflict():
    source = read("app/api/payment_review_center.py")
    endpoint = source.split('@router.post("/{payment_id}/resolve")', 1)[1].split(
        '@router.get("/ui"', 1
    )[0]
    conflict = endpoint.split("except (", 1)[1].split("except Exception", 1)[0]

    assert "PaymentReviewResolutionError" in conflict
    assert "SlotUnavailableError" in conflict
    assert "await db.rollback()" in conflict
    assert "raise HTTPException(status_code=409" in conflict
    assert conflict.index("await db.rollback()") < conflict.index(
        "raise HTTPException(status_code=409"
    )


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


def test_payment_review_409_reloads_server_truth_before_restoring_valid_draft():
    source = read("app/api/payment_review_center.py")
    resolve = source.split("async function resolveReview", 1)[1]
    stale = resolve.split("if(e.status===409)", 1)[1]

    assert "await load()" in stale
    assert "restoreDraftSelections(id)" in stale
    assert stale.index("await load()") < stale.index("restoreDraftSelections(id)")
    assert "решение не применено" in stale
    assert "Ваш допустимый выбор и комментарий сохранены" in stale


def test_payment_review_card_matches_staff_action_hierarchy():
    source = read("app/api/payment_review_center.py")

    assert '<div class="section-label">Сейчас</div>' in source
    assert '<div class="section-label">Главный следующий шаг</div>' in source
    assert '<div class="section-label">Решение</div>' in source
    assert '<div class="section-label">Вторичные действия</div>' in source
