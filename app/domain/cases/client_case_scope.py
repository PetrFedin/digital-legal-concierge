from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case


CLIENT_COMPLETED_CASE_STATUSES = frozenset(
    {
        CaseStatus.M1_CLOSED,
        CaseStatus.M2_CLOSED,
        CaseStatus.ARCHIVED,
    }
)


async def latest_completed_case_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> Case | None:
    """Return the latest completed case for client read-only archive screens."""

    result = await db.execute(
        select(Case)
        .where(Case.client_id == user_id)
        .where(Case.status.in_(CLIENT_COMPLETED_CASE_STATUSES))
        .order_by(Case.closed_at.desc(), Case.updated_at.desc(), Case.id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def active_or_latest_completed_case_for_user(
    db: AsyncSession,
    *,
    case_service,
    user_id: int,
) -> tuple[Case | None, bool]:
    """Resolve active or latest completed case for read-only client screens."""

    active = await case_service.get_active_case_for_user(user_id)
    if active is not None:
        return active, False
    completed = await latest_completed_case_for_user(db, user_id=user_id)
    return completed, completed is not None


async def latest_completed_m1_case_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> Case | None:
    """Compatibility alias for older read-only Telegram screens.

    The name predates the complete M2 archive. Closed M2 must now remain visible
    in the same Documents/Payments/History cabinet surfaces as closed M1.
    """

    return await latest_completed_case_for_user(db, user_id=user_id)


async def active_or_latest_completed_m1_case_for_user(
    db: AsyncSession,
    *,
    case_service,
    user_id: int,
) -> tuple[Case | None, bool]:
    """Compatibility alias for the route-complete read-only archive scope."""

    return await active_or_latest_completed_case_for_user(
        db,
        case_service=case_service,
        user_id=user_id,
    )


__all__ = [
    "CLIENT_COMPLETED_CASE_STATUSES",
    "active_or_latest_completed_case_for_user",
    "active_or_latest_completed_m1_case_for_user",
    "latest_completed_case_for_user",
    "latest_completed_m1_case_for_user",
]
