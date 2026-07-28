from __future__ import annotations

from dataclasses import is_dataclass, replace
from typing import TypeVar


DashboardT = TypeVar("DashboardT")


def sanitize_case_dashboard(dashboard: DashboardT) -> DashboardT:
    """Remove non-public lawyer contacts at the client presentation boundary.

    ``Lawyer.phone`` and ``Lawyer.email`` are administrative fields in the
    current schema: there is no public-contact flag or separate client-facing
    column. Admin/CRM read models keep these values, while Telegram receives a
    copy with the contact fields cleared.
    """

    lawyer = getattr(dashboard, "lawyer", None)
    if lawyer is None or not is_dataclass(lawyer) or not is_dataclass(dashboard):
        return dashboard

    changes: dict[str, None] = {}
    if hasattr(lawyer, "phone"):
        changes["phone"] = None
    if hasattr(lawyer, "email"):
        changes["email"] = None
    if not changes:
        return dashboard

    return replace(dashboard, lawyer=replace(lawyer, **changes))
