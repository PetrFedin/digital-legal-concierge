from __future__ import annotations

import inspect

from app.api.guided_lawyer_ui import (
    guided_client_no_show,
    guided_complete_consultation,
)
from app.api.lawyer_consultation_desk import CONSULTATION_DESK_HTML
from app.domain.consultations.outcome_service import ConsultationOutcomeService


def test_lawyer_outcome_authority_requires_exact_slot_snapshot() -> None:
    complete = inspect.getsource(ConsultationOutcomeService.complete)
    no_show = inspect.getsource(ConsultationOutcomeService.mark_client_no_show)

    assert "_require_expected_slot(consultation, expected_slot_id)" in complete
    assert "_require_expected_slot(consultation, expected_slot_id)" in no_show
    assert complete.index("_require_assigned_lawyer") < complete.index(
        "_require_expected_slot"
    )
    assert no_show.index("_require_assigned_lawyer") < no_show.index(
        "_require_expected_slot"
    )


def test_lawyer_api_passes_visible_slot_snapshot_into_domain_authority() -> None:
    complete = inspect.getsource(guided_complete_consultation)
    no_show = inspect.getsource(guided_client_no_show)

    assert 'expected_slot_id=payload.get("expected_slot_id")' in complete
    assert 'expected_slot_id=payload.get("expected_slot_id")' in no_show


def test_lawyer_consultation_desk_sends_slot_id_from_same_card() -> None:
    html = CONSULTATION_DESK_HTML

    assert '"slot_id": int(consultation.slot_id)' not in html  # data is server Python
    assert "expectedSlotId=Number(row?.slot_id||0)" in html
    assert "expected_slot_id:expectedSlotId" in html
    assert "Черновик сохранён" in html
