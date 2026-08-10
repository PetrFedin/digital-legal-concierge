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
    """Return the latest completed client case for read-only archive screens.

    This helper is deliberately broader than mutation scope: both M1 and M2
    remain readable after completion, while CaseService.get_active_case_for_user
    continues to exclude closed cases so stale buttons cannot reopen them.
    """

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
    """Resolve the active case or latest completed case for read-only screens."""

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
    """Return latest M1_CLOSED for M1-specific stale-button recovery only.

    Do not broaden this helper to M2: callers use it to prove that an old M1
    payment/action belongs to a finished M1 route rather than another case.
    """

    result = await db.execute(
        select(Case)
        .where(Case.client_id == user_id)
        .where(Case.status == CaseStatus.M1_CLOSED)
        .order_by(Case.closed_at.desc(), Case.updated_at.desc(), Case.id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def active_or_latest_completed_m1_case_for_user(
    db: AsyncSession,
    *,
    case_service,
    user_id: int,
) -> tuple[Case | None, bool]:
    """M1-specific read helper retained for stale M1 action/payment guards."""

    active = await case_service.get_active_case_for_user(user_id)
    if active is not None:
        return active, False
    completed = await latest_completed_m1_case_for_user(db, user_id=user_id)
    return completed, completed is not None


__all__ = [
    "CLIENT_COMPLETED_CASE_STATUSES",
    "active_or_latest_completed_case_for_user",
    "active_or_latest_completed_m1_case_for_user",
    "latest_completed_case_for_user",
    "latest_completed_m1_case_for_user",
]
