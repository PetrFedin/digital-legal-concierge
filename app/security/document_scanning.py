from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


LEGACY_READMISSION_ISSUE = 172


async def rescan_legacy_documents(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Fail closed until legacy records can be re-admitted through DLC-INT-00.

    The pre-DLC-INT-00 implementation parsed historical filesystem bytes before
    malware admission. Keeping that path active would violate the authoritative
    secure-ingest order and could also dereference legacy storage identities that
    predate the current portable Case storage contract.

    Issue #172 owns the controlled migration. Until then, legacy rows remain
    LEGACY_UNVERIFIED / SCAN_ERROR and are not upgraded to VERIFIED here.
    """

    _ = db
    _ = limit
    return {
        "verified": 0,
        "quarantined": 0,
        "missing": 0,
        "duplicate": 0,
        "scan_error": 0,
        "blocked_pending_controlled_readmission": 1,
    }


__all__ = ["LEGACY_READMISSION_ISSUE", "rescan_legacy_documents"]
