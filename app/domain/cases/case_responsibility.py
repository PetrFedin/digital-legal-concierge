from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation


TERMINAL_CASE_STATUS_VALUES = frozenset(
    {
        CaseStatus.M1_CLOSED.value,
        CaseStatus.M2_CLOSED.value,
        CaseStatus.ARCHIVED.value,
    }
)

# M2 responsibility belongs to the lawyer selected by the current consultation
# slot. Cancelled/rescheduled consultations deliberately do not keep ownership.
M2_CURRENT_LAWYER_STATUSES = frozenset(
    {
        ConsultationStatus.SLOT_RESERVED.value,
        ConsultationStatus.PAYMENT_PENDING.value,
        ConsultationStatus.BOOKED.value,
        ConsultationStatus.DONE.value,
        ConsultationStatus.CLIENT_NO_SHOW.value,
        ConsultationStatus.LAWYER_NO_SHOW.value,
    }
)


def _is_terminal(case: Case) -> bool:
    return str(case.status) in TERMINAL_CASE_STATUS_VALUES


async def effective_lawyer_ids_for_cases(
    db: AsyncSession,
    cases: Iterable[Case],
) -> dict[int, int | None]:
    """Resolve the lawyer who may currently act on each case.

    M1 uses ``Case.assigned_lawyer_id``. M2 intentionally does not: the current
    consultation slot is the source of truth. Only the newest consultation can
    grant M2 responsibility, so an old DONE/CANCELLED booking can never leak
    access after the client starts or books a newer consultation.
    """

    case_list = list(cases)
    result: dict[int, int | None] = {
        int(case.id): (
            int(case.assigned_lawyer_id)
            if case.assigned_lawyer_id is not None
            else None
        )
        for case in case_list
        if str(case.route or "") != "M2"
    }

    m2_cases = [case for case in case_list if str(case.route or "") == "M2"]
    if not m2_cases:
        return result

    case_ids = [int(case.id) for case in m2_cases]
    consultations = list(
        (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id.in_(case_ids))
                .order_by(
                    Consultation.case_id.asc(),
                    Consultation.created_at.desc(),
                    Consultation.id.desc(),
                )
            )
        ).scalars().all()
    )
    latest: dict[int, Consultation] = {}
    for consultation in consultations:
        latest.setdefault(int(consultation.case_id), consultation)

    for case in m2_cases:
        consultation = latest.get(int(case.id))
        lawyer_id: int | None = None
        if consultation is not None and consultation.lawyer_id is not None:
            consultation_status = str(consultation.status or "")
            if (
                consultation_status in M2_CURRENT_LAWYER_STATUSES
                or _is_terminal(case)
            ):
                lawyer_id = int(consultation.lawyer_id)
        result[int(case.id)] = lawyer_id

    return result


async def effective_lawyer_id_for_case(
    db: AsyncSession,
    case: Case,
) -> int | None:
    return (await effective_lawyer_ids_for_cases(db, [case])).get(int(case.id))


async def lawyer_can_access_case(
    db: AsyncSession,
    *,
    case: Case,
    lawyer_id: int | None,
) -> bool:
    if lawyer_id is None:
        return False
    return await effective_lawyer_id_for_case(db, case) == int(lawyer_id)


__all__ = [
    "M2_CURRENT_LAWYER_STATUSES",
    "TERMINAL_CASE_STATUS_VALUES",
    "effective_lawyer_id_for_case",
    "effective_lawyer_ids_for_cases",
    "lawyer_can_access_case",
]
