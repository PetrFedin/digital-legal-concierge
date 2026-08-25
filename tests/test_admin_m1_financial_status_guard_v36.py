from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.api.admin import manual_status
from app.domain.cases.admin_manual_status_policy import (
    assert_manual_status_change_allowed,
    manual_status_change_allowed,
)
from app.domain.statuses.case_statuses import CaseStatus


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (CaseStatus.M1_ENFORCEMENT, CaseStatus.M1_MONEY_RECEIVED),
        (CaseStatus.M1_ENFORCEMENT, CaseStatus.M1_WAITING_SUCCESS_FEE),
        (CaseStatus.M1_WAITING_SUCCESS_FEE, CaseStatus.M1_SUCCESS_FEE_RECEIVED),
        (CaseStatus.M1_WAITING_SUCCESS_FEE, CaseStatus.M1_CLOSED),
        (CaseStatus.M1_SUCCESS_FEE_RECEIVED, CaseStatus.M1_CLOSED),
        (CaseStatus.M1_CLOSED, CaseStatus.ARCHIVED),
    ),
)
def test_generic_admin_status_cannot_touch_financial_managed_chain(current, target):
    assert manual_status_change_allowed(current, target) is False
    with pytest.raises(ValueError, match="реальное юридическое, консультационное или финансовое событие"):
        assert_manual_status_change_allowed(current, target)


def test_rejected_case_can_still_use_its_non_financial_close_transition():
    assert manual_status_change_allowed(
        CaseStatus.M1_REJECTED,
        CaseStatus.M1_CLOSED,
    ) is True
    assert_manual_status_change_allowed(
        CaseStatus.M1_REJECTED,
        CaseStatus.M1_CLOSED,
    )


def test_unrelated_admin_correction_is_not_changed_by_financial_guard():
    assert manual_status_change_allowed(
        CaseStatus.M1_DOCUMENTS_PENDING,
        CaseStatus.ERROR,
    ) is True


def test_manual_status_endpoint_checks_financial_guard_after_snapshot_and_before_force():
    source = inspect.getsource(manual_status)

    snapshot_index = source.index("expected_status")
    guard_index = source.index("assert_manual_status_change_allowed")
    change_index = source.index("CaseService(db).change_status")
    force_index = source.index("force=True")

    assert snapshot_index < guard_index < change_index <= force_index
    assert "status_code=409" in source
    assert "await db.rollback()" in source


def test_admin_cabinet_uses_backend_allowed_status_options_and_explains_locked_financial_stage():
    source = Path("app/api/web_admin.py").read_text(encoding="utf-8")

    assert "manual_status_change_allowed(case.status, status.value)" in source
    assert '"manual_status_options": manual_status_options' in source
    assert '"manual_status_note": _manual_status_note(case)' in source
    assert "const statusOptions=d.case.manual_status_options||[]" in source
    assert "Показаны только серверно разрешённые административные переходы." in source
    assert "Финальный финансовый этап защищён" in source
    assert "Ручная смена статуса здесь недоступна" in source
    assert "Открыть финансовую карточку" in source
    assert "await api('/admin/statuses')" not in source
