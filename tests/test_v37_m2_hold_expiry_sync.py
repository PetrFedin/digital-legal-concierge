from __future__ import annotations

import inspect

from app.domain.consultations.slot_service import SlotService


def test_expired_hold_syncs_consultation_and_case_back_to_slot_choice():
    source = inspect.getsource(SlotService.release_expired_holds)
    assert "ConsultationStatus.SLOT_PENDING" in source
    assert "CaseStatus.M2_SLOT_PENDING" in source
    assert "CaseStatus.M2_PAYMENT_PENDING" in source
    assert "CaseService" in source
    assert "Резерв консультации истёк" in source


def test_expired_hold_release_remains_audited_case_transition():
    source = inspect.getsource(SlotService.release_expired_holds)
    assert "case_service.change_status" in source
    assert 'actor_type="system"' in source
