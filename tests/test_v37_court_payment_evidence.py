from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.api.lawyer_workspace_rejection_ui import enhanced_lawyer_workspace_html
from app.domain.cases.admin_manual_status_policy import manual_status_change_allowed
from app.domain.cases.m1_court_decision import (
    normalize_court_decision_date,
    normalize_court_decision_reference,
)
from app.domain.statuses.case_statuses import CaseStatus


def test_court_payment_ui_requires_structured_decision_evidence():
    html = enhanced_lawyer_workspace_html()
    assert "court_reference_" in html
    assert "court_date_" in html
    assert "decision_reference" in html
    assert "decision_date" in html
    assert "Судебный акт зафиксирован" in html


def test_court_decision_reference_and_date_are_validated():
    assert normalize_court_decision_reference("  А40-12345/2026  ") == "А40-12345/2026"
    with pytest.raises(ValueError, match="идентификатор"):
        normalize_court_decision_reference("123")
    assert normalize_court_decision_date("2026-08-12") == date(2026, 8, 12)
    with pytest.raises(ValueError, match="формате"):
        normalize_court_decision_date("12.08.2026")
    future = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="будущем"):
        normalize_court_decision_date(future)


def test_generic_admin_status_cannot_manufacture_court_or_payment_stages():
    assert not manual_status_change_allowed(
        CaseStatus.M1_LAWYER_REVIEW,
        CaseStatus.M1_COURT_STAGE,
    )
    assert not manual_status_change_allowed(
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_WAITING_PAYMENT_70000,
    )
    assert not manual_status_change_allowed(
        CaseStatus.M1_WAITING_PAYMENT_70000,
        CaseStatus.M1_ENFORCEMENT,
    )
    assert not manual_status_change_allowed(
        CaseStatus.M2_DESCRIPTION_PENDING,
        CaseStatus.M2_CONSULTATION_BOOKED,
    )


def test_rejected_m1_explicit_close_exception_remains_allowed():
    assert manual_status_change_allowed(
        CaseStatus.M1_REJECTED,
        CaseStatus.M1_CLOSED,
    )
