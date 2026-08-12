from __future__ import annotations

import inspect
from pathlib import Path

from app.api.technical_case_recovery import (
    guided_workdesk_ui,
    recover_technical_case,
    technical_case_context,
    technical_case_ui,
    technical_cases,
)
from app.domain.cases.admin_manual_status_policy import manual_status_change_allowed
from app.domain.cases.error_recovery_service import (
    CaseErrorRecoveryService,
    SAFE_ERROR_RECOVERY_TARGETS,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.main import create_app


def _first_endpoint(path: str, method: str = "GET"):
    for route in create_app().routes:
        if route.path == path and method in (route.methods or set()):
            return route.endpoint
    return None


def test_technical_recovery_routes_are_mounted_before_legacy_workdesk_ui():
    assert _first_endpoint("/admin/workdesk/ui") is guided_workdesk_ui
    assert _first_endpoint("/admin/technical-cases") is technical_cases
    assert (
        _first_endpoint("/admin/technical-cases/{case_id}/context")
        is technical_case_context
    )
    assert _first_endpoint("/admin/technical-cases/ui") is technical_case_ui
    assert (
        _first_endpoint("/admin/technical-cases/{case_id}/recover", "POST")
        is recover_technical_case
    )


def test_generic_admin_status_switch_cannot_bypass_error_recovery_policy():
    assert not manual_status_change_allowed(
        CaseStatus.ERROR,
        CaseStatus.M1_DOCUMENTS_PENDING,
    )
    assert not manual_status_change_allowed(
        CaseStatus.ERROR,
        CaseStatus.M2_SLOT_PENDING,
    )


def test_error_recovery_targets_exclude_payment_booking_and_terminal_truth():
    assert CaseStatus.M1_DOCUMENTS_PENDING in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_ENFORCEMENT in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M2_DESCRIPTION_PENDING in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M2_SLOT_PENDING in SAFE_ERROR_RECOVERY_TARGETS

    assert CaseStatus.M1_WAITING_PAYMENT_30000 not in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_MONEY_RECEIVED not in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_WAITING_SUCCESS_FEE not in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M2_PAYMENT_PENDING not in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M2_CONSULTATION_BOOKED not in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M1_CLOSED not in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.M2_CLOSED not in SAFE_ERROR_RECOVERY_TARGETS
    assert CaseStatus.ARCHIVED not in SAFE_ERROR_RECOVERY_TARGETS


def test_error_recovery_target_is_derived_from_audited_error_transition():
    source = inspect.getsource(CaseErrorRecoveryService)
    assert 'new_value.get("status")' in source
    assert "CaseStatus.ERROR.value" in source
    assert 'old_value.get("status")' in source
    assert "last_audited_safe_case_stage" in source
    assert "expected_updated_at" in source


def test_telegram_error_state_blocks_old_consultation_mutations_and_offers_support():
    source = Path("app/bot/consultation_route_guard.py").read_text(encoding="utf-8")
    error_branch = source.index("if status == CaseStatus.ERROR")
    m1_branch = source.index('if str(case.route or "") != RouteCode.M1.value')
    assert error_branch < m1_branch
    assert "старая кнопка не создаёт запись" in source
    assert '"message_create"' in source
    assert '"my_case_open"' in source
    assert '"documents_open"' in source
    assert '"case_history_open"' in source


def test_workdesk_error_banner_and_recovery_ui_have_no_arbitrary_status_picker():
    source = Path("app/api/technical_case_recovery.py").read_text(encoding="utf-8")
    assert "Техническая очередь" in source
    assert "Разобрать первое дело" in source
    assert "Безопасное восстановление найдено" in source
    assert "вручную выбрать другой нельзя" in source
    assert "/admin/workdesk/cases/${caseId}/timeline" in source
    assert "/message-center/ui?case_id=${caseId}" in source
    assert "/document-access/ui?case_id=${caseId}" in source
    assert "<select" not in source
