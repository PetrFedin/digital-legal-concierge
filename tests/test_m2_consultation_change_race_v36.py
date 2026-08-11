from __future__ import annotations

import pytest

from app.domain.consultations.consultation_change_service import ConsultationChangeService
from app.domain.statuses.consultation_statuses import ConsultationStatus


@pytest.mark.asyncio
async def test_cancel_rebook_reloads_booked_consultation_under_lock(db_session, consultation, case, user):
    service = ConsultationChangeService(db_session)

    locked = await service._lock_booked_consultation(
        case_id=case.id,
        consultation_id=consultation.id,
    )

    assert locked.id == consultation.id
    assert locked.case_id == case.id
    assert locked.status == ConsultationStatus.BOOKED


@pytest.mark.asyncio
async def test_stale_second_cancel_is_rejected_after_first_transition(
    db_session,
    consultation,
    case,
):
    service = ConsultationChangeService(db_session)
    consultation.status = ConsultationStatus.CANCELLED

    with pytest.raises(ValueError, match="уже была изменена"):
        await service._lock_booked_consultation(
            case_id=case.id,
            consultation_id=consultation.id,
        )
