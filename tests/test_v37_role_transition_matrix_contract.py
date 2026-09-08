from __future__ import annotations

import inspect

from app.api import lawyer_m1_claim, lawyer_m1_enforcement, lawyer_poa
from app.api.access_management import PRODUCT_WORKSPACE_ROLES
from app.api.consultation_outcomes_product import (
    _inject_business_timezone_ui,
    product_mark_lawyer_no_show,
)
from app.api.guided_consultation_outcomes import _inject_client_no_show_ui
from app.api.message_center_product_impl import require_product_staff_scope
from app.api.payment_review_center import require_admin as require_payment_review_admin
from app.api.refund_center import require_admin as require_refund_admin
from app.api.consultation_outcomes import OUTCOMES_HTML
from app.bot.case_callback_scope import (
    CASE_BOUND_MUTATING_ACTIONS,
    PAYMENT_CASE_BOUND_ACTIONS,
    bound_case_callback,
    resolve_case_callback_scope,
)
from app.domain.cases.admin_manual_status_policy import (
    DOMAIN_MANAGED_STATUSES,
    manual_status_change_allowed,
)
from app.domain.consultations.client_no_show_resolution_service import (
    ClientNoShowResolutionService,
)
from app.domain.consultations.no_show_resolution_service import NoShowResolutionService
from app.domain.consultations.outcome_service import ConsultationOutcomeService
from app.domain.statuses.case_statuses import CaseStatus
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_OPERATOR,
    ROLE_SUPERADMIN,
)
from app.security.document_access import load_authorized_document, resolve_document_actor


def _source(value) -> str:
    return inspect.getsource(value)


def test_operator_is_auxiliary_and_never_a_standalone_product_authority():
    assert PRODUCT_WORKSPACE_ROLES == frozenset(
        {ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER}
    )
    assert ROLE_OPERATOR not in PRODUCT_WORKSPACE_ROLES

    message_scope = _source(require_product_staff_scope)
    assert "ROLE_ADMIN, ROLE_SUPERADMIN" in message_scope
    assert "ROLE_LAWYER not in scope.roles" in message_scope
    assert "Роль «Оператор» является дополнительной" in message_scope
    assert "require_lawyer_actor" in message_scope


def test_client_mutations_are_bound_to_the_exact_selected_case():
    assert PAYMENT_CASE_BOUND_ACTIONS <= CASE_BOUND_MUTATING_ACTIONS
    for action in (
        "pay_start_30000",
        "pay_court_70000",
        "pay_success_fee",
        "consult_pay",
        "message_create",
        "consult_booking_start",
        "consult_reschedule",
        "consult_cancel",
        "documents_open",
        "payments_open",
    ):
        assert action in CASE_BOUND_MUTATING_ACTIONS
        assert bound_case_callback(action, 73) == f"{action}:v2:73"

    source = _source(resolve_case_callback_scope)
    assert "get_active_cases_for_user" in source
    assert "get_active_case_for_user" in source
    assert "int(selected_case.id) != expected_case_id" in source
    assert "Действие не выполнено" in source
    assert "len(active_cases) > 1" in source


def test_documents_allow_only_staff_base_roles_and_scope_lawyer_to_responsibility():
    actor_source = _source(resolve_document_actor)
    document_source = _source(load_authorized_document)

    assert "ROLE_SUPERADMIN" in actor_source
    assert "ROLE_ADMIN" in actor_source
    assert "ROLE_LAWYER" in actor_source
    assert "ROLE_OPERATOR" not in actor_source
    assert "mfa_required" in actor_source
    assert "role_denied" in actor_source
    assert "lawyer_can_access_case" in document_source
    assert "lawyer_not_responsible" in document_source


def test_m1_legal_facts_are_owned_by_the_assigned_lawyer_with_stale_snapshot_checks():
    functions = (
        lawyer_poa.confirm_poa_received,
        lawyer_m1_claim.start_claim_preparation,
        lawyer_m1_claim.mark_claim_sent,
        lawyer_m1_claim.open_court_stage,
        lawyer_m1_claim.open_court_payment,
        lawyer_m1_enforcement.record_money_received,
    )
    for function in functions:
        source = _source(function)
        assert "require_lawyer_actor" in source
        assert "expected_status" in source
        assert "expected_updated_at" in source
        assert "await db.rollback()" in source
        assert "status_code=409" in source

    poa_source = _source(lawyer_poa.confirm_poa_received)
    enforcement_source = _source(lawyer_m1_enforcement.record_money_received)
    assert "assigned_case" in poa_source and "for_update=True" in poa_source
    assert "assigned_case" in enforcement_source and "for_update=True" in enforcement_source


def test_financial_review_and_refund_decisions_are_admin_owned_not_lawyer_or_operator_owned():
    payment_review = _source(require_payment_review_admin)
    refund_review = _source(require_refund_admin)

    for source in (payment_review, refund_review):
        assert "ROLE_ADMIN" in source
        assert "has_role" in source
        assert "ROLE_LAWYER" not in source
        assert "ROLE_OPERATOR" not in source
        assert "status_code=403" in source


def test_generic_admin_status_selector_cannot_manufacture_domain_facts():
    assert DOMAIN_MANAGED_STATUSES == frozenset(CaseStatus)

    allowed = []
    for current in CaseStatus:
        for target in CaseStatus:
            if current == target:
                continue
            if manual_status_change_allowed(current, target):
                allowed.append((current, target))

    assert allowed == [(CaseStatus.M1_REJECTED, CaseStatus.M1_CLOSED)]


def test_m2_lawyer_result_is_assigned_lawyer_owned_and_exact_retry_safe():
    complete = _source(ConsultationOutcomeService.complete)
    client_no_show = _source(ConsultationOutcomeService.mark_client_no_show)

    assert complete.index("_require_assigned_lawyer") < complete.index(
        "ConsultationStatus.DONE"
    )
    assert "stored_result != normalized_result" in complete
    assert "stored_decision != normalized_decision" in complete
    assert "Старое действие не применено" in complete

    assert "_require_assigned_lawyer" in client_no_show
    assert "stored_comment != normalized_comment" in client_no_show


def test_m2_admin_no_show_is_bound_to_exact_slot_and_exact_admin_provenance():
    endpoint = _source(product_mark_lawyer_no_show)
    service = _source(ConsultationOutcomeService.mark_lawyer_no_show)

    assert 'payload.get("expected_slot_id")' in endpoint
    assert "expected_slot_id=expected_slot_id" in endpoint
    assert "status_code=409" in endpoint
    assert "event.actor_id != admin_id" in service
    assert "int(consultation.slot_id or 0) != int(expected_slot_id)" in service
    assert "_latest_consultation_history_event" in service


def test_m2_lawyer_no_show_rebook_exact_retry_is_actor_slot_and_comment_bound():
    service = _source(ConsultationOutcomeService.rebook_after_lawyer_no_show)

    assert "ConsultationStatus.BOOKED" in service
    assert 'action="CONSULTATION_REBOOKED_AFTER_LAWYER_NO_SHOW"' in service
    assert "event.actor_id != admin_id" in service
    assert "event_slot_id != normalized_slot_id" in service
    assert "str(event.comment or \"\").strip() != normalized_comment" in service
    assert "int(consultation.slot_id or 0) != normalized_slot_id" in service


def test_m2_no_show_refund_resolution_is_atomic_and_audit_bound():
    source = _source(NoShowResolutionService.route_lawyer_no_show_to_refund)
    retry = _source(NoShowResolutionService._require_exact_refund_retry_or_conflict)
    release = _source(NoShowResolutionService._release_consultation_slot)

    assert "_latest_m2_payment_for_update" in source
    assert "_release_consultation_slot" in source
    assert "consultation.status = ConsultationStatus.CANCELLED" in source
    assert "PaymentStatus.REFUND_PENDING" in source
    assert "self.REFUND_ACTION" in source
    assert "event.actor_id != admin_id" in retry
    assert "event_payment_id != int(payment.id)" in retry
    assert "slot.consultation_id = None" in release
    assert 'slot.status = "available"' in release


def test_m2_client_no_show_rebook_and_close_require_exact_admin_provenance():
    rebook = _source(ClientNoShowResolutionService.prepare_new_paid_booking)
    close = _source(ClientNoShowResolutionService.close_case)
    audit = _source(ClientNoShowResolutionService._latest_resolution_event)

    assert "self.REBOOK_ACTION" in rebook
    assert "_require_exact_actor_comment" in rebook
    assert "replacement_id" in rebook
    assert "int(current.id) == replacement_id" in rebook
    assert "self.CLOSE_ACTION" in close
    assert "_require_exact_actor_comment" in close
    assert "AuditLog.action == action" in audit
    assert ".limit(" not in audit


def test_m2_outcomes_browser_keeps_drafts_and_refreshes_after_conflict():
    html = _inject_client_no_show_ui(OUTCOMES_HTML)
    html = _inject_business_timezone_ui(html)

    assert "async function refreshAfterConflict" in html
    assert "window.clientNoShowRebook" in html
    assert "window.clientNoShowClose" in html
    assert "rememberDraft(id,mode,comment)" in html
    assert "if(await refreshAfterConflict(e,row))return" in html
    assert "Введённый черновик не удалён" in html
    assert "try{await load()}" in html


def test_terminal_role_matrix_keeps_business_close_separate_from_generic_admin_edit():
    assert manual_status_change_allowed(
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        CaseStatus.M1_CLOSED,
    ) is False
    assert manual_status_change_allowed(
        CaseStatus.M2_CONSULTATION_DONE,
        CaseStatus.M2_CLOSED,
    ) is False
    assert manual_status_change_allowed(
        CaseStatus.M1_CLOSED,
        CaseStatus.ARCHIVED,
    ) is False

    complete = _source(ConsultationOutcomeService.complete)
    assert "next_status=CaseStatus.M2_CLOSED" in complete
    assert 'actor_type="lawyer"' in complete
