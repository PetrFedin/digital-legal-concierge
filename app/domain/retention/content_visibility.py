"""Read-side content availability shared by privacy-sensitive case views.

A deletion approval is an immediate disclosure boundary: private content must
not remain readable during the interval before the retention worker removes it.
This module does *not* erase or rewrite any immutable audit or backup material.
"""
from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord


RESTRICTED_CONTENT_RETENTION_STATES = frozenset(
    {"APPROVED", "EXECUTING", "FAILED", "COMPLETED"}
)



def approved_erasure_absent_clause():
    """SQL filter for retrievals where only closure metadata may still remain.

    Keeping this restriction inside the SELECT prevents client functions from
    materializing a closed matter whose deletion was approved but not executed.
    """
    blocked = (
        select(CaseRetentionRecord.id)
        .where(
            CaseRetentionRecord.case_id == Case.id,
            CaseRetentionRecord.status.in_(RESTRICTED_CONTENT_RETENTION_STATES),
        )
        .exists()
    )
    return ~blocked


def private_case_content_available_clause():
    """Read gate for all sensitive client projections, including old callbacks."""
    return and_(
        Case.content_deleted_at.is_(None),
        approved_erasure_absent_clause(),
    )



async def private_case_content_unavailable(
    db: AsyncSession, *, case: Case
) -> bool:
    """Fail closed for erased/approved-to-erase content, not merely closed cases."""
    if case.content_deleted_at is not None:
        return True
    restricted_id = await db.scalar(
        select(CaseRetentionRecord.id)
        .where(
            CaseRetentionRecord.case_id == int(case.id),
            CaseRetentionRecord.status.in_(RESTRICTED_CONTENT_RETENTION_STATES),
        )
        .limit(1)
    )
    return restricted_id is not None


async def restricted_case_ids(
    db: AsyncSession, *, case_ids: set[int]
) -> set[int]:
    """Return unavailable *or missing* case identifiers without reading audit JSON.

    Batch lookup is used for bounded audit-center projections. Unresolvable Case
    references are private by default, not treated as permission to show content.
    """
    ids = {int(value) for value in case_ids if int(value) > 0}
    if not ids:
        return set()

    case_rows = (
        await db.execute(
            select(Case.id, Case.content_deleted_at).where(Case.id.in_(ids))
        )
    ).all()
    present_ids = {int(case_id) for case_id, _ in case_rows}
    unavailable = ids - present_ids
    unavailable.update(
        int(case_id)
        for case_id, deleted_at in case_rows
        if deleted_at is not None
    )
    retention_rows = (
        await db.execute(
            select(CaseRetentionRecord.case_id).where(
                CaseRetentionRecord.case_id.in_(ids),
                CaseRetentionRecord.status.in_(RESTRICTED_CONTENT_RETENTION_STATES),
            )
        )
    ).scalars().all()
    unavailable.update(int(case_id) for case_id in retention_rows)
    return unavailable
