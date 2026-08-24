from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case


CLIENT_COMPLETED_CASE_STATUSES = frozenset(
    {
        CaseStatus.M1_CLOSED,
        CaseStatus.M2_CLOSED,
        CaseStatus.ARCHIVED,
    }
)


def _completed_ordering():
    """Use the real close time first; null close dates must never win on PostgreSQL."""

    return (
        Case.closed_at.desc().nullslast(),
        Case.updated_at.desc().nullslast(),
        Case.id.desc(),
    )


async def completed_case_count_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> int:
    """Count completed matters without loading the whole client archive."""

    value = await db.scalar(
        select(func.count(Case.id))
        .where(Case.client_id == int(user_id))
        .where(Case.status.in_(CLIENT_COMPLETED_CASE_STATUSES))
    )
    return int(value or 0)


async def completed_cases_for_user(
    db: AsyncSession,
    *,
    user_id: int,
    limit: int = 30,
    offset: int = 0,
) -> list[Case]:
    """Return one deterministic page of completed M1/M2 matters.

    The archive is read-only. Pagination belongs here rather than in Telegram UI
    so a long client history never requires loading every closed Case into one
    message while preserving the same close-time ordering across pages.
    """

    bounded_limit = min(max(int(limit), 1), 100)
    bounded_offset = max(int(offset), 0)
    result = await db.execute(
        select(Case)
        .where(Case.client_id == int(user_id))
        .where(Case.status.in_(CLIENT_COMPLETED_CASE_STATUSES))
        .order_by(*_completed_ordering())
        .offset(bounded_offset)
        .limit(bounded_limit)
    )
    return list(result.scalars().all())


async def latest_completed_case_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> Case | None:
    """Return the latest completed case for client read-only archive screens."""

    items = await completed_cases_for_user(db, user_id=user_id, limit=1)
    return items[0] if items else None


async def unambiguous_active_case_for_user(
    *,
    case_service,
    user_id: int,
) -> tuple[Case | None, bool]:
    """Return selected/sole active Case and whether explicit selection is required.

    Multi-case Telegram surfaces must never silently borrow the newest Case when
    the persisted selection is missing or points at a Case that has since become
    terminal. A selected active Case is authoritative; with no selection, the
    only active Case is safe to use. Two or more active Cases require the client
    to choose explicitly before any contextual read or mutation continues.
    """

    selected = await case_service.get_selected_case_for_user(
        int(user_id),
        include_terminal=False,
    )
    if selected is not None:
        return selected, False
    active_cases = await case_service.get_active_cases_for_user(int(user_id))
    if len(active_cases) == 1:
        return active_cases[0], False
    return None, len(active_cases) > 1


async def active_or_latest_completed_case_for_user(
    db: AsyncSession,
    *,
    case_service,
    user_id: int,
) -> tuple[Case | None, bool]:
    """Resolve an unambiguous active Case or latest completed read-only Case.

    Completed history is a fallback only when there are no active matters. If
    several active matters exist without a valid selected context, returning an
    archive would be equally misleading, so the caller receives no Case and can
    route the client to the canonical selector.
    """

    active, selection_required = await unambiguous_active_case_for_user(
        case_service=case_service,
        user_id=user_id,
    )
    if active is not None:
        return active, False
    if selection_required:
        return None, False
    completed = await latest_completed_case_for_user(db, user_id=user_id)
    return completed, completed is not None


async def latest_completed_strict_m1_case_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> Case | None:
    """Return only a completed M1 case for M1-specific stale-action guards."""

    result = await db.execute(
        select(Case)
        .where(Case.client_id == user_id)
        .where(Case.status == CaseStatus.M1_CLOSED)
        .order_by(*_completed_ordering())
        .limit(1)
    )
    return result.scalars().first()


async def latest_completed_strict_m2_case_for_user(
    db: AsyncSession,
    *,
    user_id: int,
) -> Case | None:
    """Return only a completed M2 case for stale consultation-action guards.

    ``ARCHIVED`` is retained for legacy records, but it is considered M2 here
    only when the persisted route is explicitly M2. This prevents an old M2
    Telegram button from borrowing the archive context of a completed M1 case.
    """

    result = await db.execute(
        select(Case)
        .where(Case.client_id == user_id)
        .where(
            or_(
                Case.status == CaseStatus.M2_CLOSED,
                and_(
                    Case.status == CaseStatus.ARCHIVED,
                    Case.route == RouteCode.M2.value,
                ),
            )
        )
        .order_by(*_completed_ordering())
        .limit(1)
    )
    return result.scalars().first()


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
    "completed_case_count_for_user",
    "completed_cases_for_user",
    "latest_completed_case_for_user",
    "latest_completed_m1_case_for_user",
    "latest_completed_strict_m1_case_for_user",
    "latest_completed_strict_m2_case_for_user",
    "unambiguous_active_case_for_user",
]
