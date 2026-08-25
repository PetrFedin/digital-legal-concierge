from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.api.admin import manual_status
from app.domain.cases.admin_manual_status_policy import (
    DOMAIN_MANAGED_STATUSES,
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
    with pytest.raises(ValueError, match="управляется конкретным действием"):
        assert_manual_status_change_allowed(current, target)


def test_rejected_case_can_still_use_its_explicit_close_transition():
    assert manual_status_change_allowed(
        CaseStatus.M1_REJECTED,
        CaseStatus.M1_CLOSED,
    ) is True
    assert_manual_status_change_allowed(
        CaseStatus.M1_REJECTED,
        CaseStatus.M1_CLOSED,
    )


def test_every_case_status_is_owned_by_the_domain_policy():
    assert DOMAIN_MANAGED_STATUSES == frozenset(CaseStatus)


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (CaseStatus.NEW, CaseStatus.CALCULATED),
        (CaseStatus.CALCULATED, CaseStatus.M1_DOCUMENTS_PENDING),
        (CaseStatus.M1_DOCUMENTS_PENDING, CaseStatus.M1_DOCUMENTS_RECEIVED),
        (CaseStatus.M1_DOCUMENTS_RECEIVED, CaseStatus.M1_LAWYER_REVIEW),
        (CaseStatus.M1_LAWYER_REVIEW, CaseStatus.M1_DOCS_REQUESTED),
        (CaseStatus.M1_DOCUMENTS_PENDING, CaseStatus.ERROR),
        (CaseStatus.M1_DOCUMENTS_PENDING, CaseStatus.ARCHIVED),
        (CaseStatus.ERROR, CaseStatus.M1_DOCUMENTS_PENDING),
    ),
)
def test_generic_admin_status_cannot_manufacture_client_legal_or_lifecycle_facts(current, target):
    assert manual_status_change_allowed(current, target) is False
    with pytest.raises(ValueError, match="generic-сменой"):
        assert_manual_status_change_allowed(current, target)


def test_manual_status_endpoint_checks_domain_guard_after_snapshot_and_before_force():
    source = inspect.getsource(manual_status)

    snapshot_index = source.index("expected_status")
    guard_index = source.index("assert_manual_status_change_allowed")
    change_index = source.index("CaseService(db).change_status")
    force_index = source.index("force=True")

    assert snapshot_index < guard_index < change_index <= force_index
    assert "status_code=409" in source
    assert "await db.rollback()" in source


def test_admin_cabinet_uses_backend_allowed_status_options_and_never_loads_global_status_list():
    source = Path("app/api/web_admin.py").read_text(encoding="utf-8")

    assert "manual_status_change_allowed(case.status, status.value)" in source
    assert '"manual_status_options": manual_status_options' in source
    assert '"manual_status_note": _manual_status_note(case)' in source
    assert "const statusOptions=d.case.manual_status_options||[]" in source
    assert "Показаны только серверно разрешённые административные переходы." in source
    assert "Ручная смена статуса здесь недоступна" in source
    assert "await api('/admin/statuses')" not in source
