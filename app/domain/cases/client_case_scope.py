from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case


async def latest_completed_m1_case_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> Case | None:
    """Return the latest completed M1 case for read-only client archive screens.

    This helper must not be used by mutation flows. Closed cases are deliberately
    excluded from CaseService.get_active_case_for_user so stale buttons cannot
    reopen or mutate a completed legal process.
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
    """Resolve a case for read-only screens and report whether it is completed."""

    active = await case_service.get_active_case_for_user(user_id)
    if active is not None:
        return active, False
    completed = await latest_completed_m1_case_for_user(db, user_id=user_id)
    return completed, completed is not None


__all__ = [
    "active_or_latest_completed_m1_case_for_user",
    "latest_completed_m1_case_for_user",
]
