"""Single runtime owner for the existing lawyer M1/M2 working surface.

This product router replaces precedence between the historical workspace,
consultation, rejection and UI composite routers. It registers the same shipped
business actions once; no new legal route is introduced.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.contract_workspace_ui import (
    contract_aware_lawyer_workspace_ui,
    legacy_lawyer_ui_redirect,
)
from app.api.guided_lawyer_ui import (
    _rebuild_workspace_summary,
    guided_client_no_show,
    guided_workspace_data,
)
from app.api.lawyer_consultation_decision_guard import guarded_complete_consultation
from app.api.lawyer_consultation_desk import consultation_desk_data
from app.api.lawyer_consultation_runtime_ui import lawyer_consultation_runtime_ui
from app.api.lawyer_m1_rejection import router as lawyer_m1_rejection_router
from app.api.lawyer_poa import router as lawyer_poa_router
from app.db.session import get_db
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.presentation_time import format_business_datetime, to_business_timezone

router = APIRouter(tags=["lawyer-product"])


# Existing M1 legal actions that historically arrived through the rejection UI
# composite remain mounted here as domain action routers.
router.include_router(lawyer_m1_rejection_router)
router.include_router(lawyer_poa_router)


def _parse_utc_datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _business_today(value: object, *, now: datetime | None = None) -> bool:
    scheduled_at = _parse_utc_datetime(value)
    if scheduled_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return to_business_timezone(scheduled_at).date() == to_business_timezone(current).date()


def _business_schedule_note(value: object) -> str | None:
    scheduled_at = _parse_utc_datetime(value)
    if scheduled_at is None:
        return None
    return f"Назначено на {format_business_datetime(scheduled_at)}"


async def business_timezone_guided_workspace_data(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Normalize lawyer M2 "today" semantics to the configured business date.

    The underlying guided projection historically compared UTC dates for M2
    appointments. Around midnight that could make the Lawyer Workspace disagree
    with Telegram, the consultation desk and the shared schedule. This product
    boundary owns the public route, so it repairs the outward projection without
    introducing a second URL owner or mutating persisted consultation facts.
    """

    payload = await guided_workspace_data(db=db, x_admin_token=x_admin_token)
    cases = list(payload.get("cases") or [])
    now = datetime.now(timezone.utc)

    for item in cases:
        if str(item.get("route") or "").upper() != "M2":
            continue
        if str(item.get("consultation_status") or "") != ConsultationStatus.BOOKED.value:
            continue

        scheduled_at = item.get("consultation_scheduled_at")
        is_today = _business_today(scheduled_at, now=now)
        item["consultation_today"] = is_today
        item["consultation_today_at"] = scheduled_at if is_today else None

        # Unread client communication remains the first responsibility. Only
        # the schedule-derived branch is recalculated here.
        if int(item.get("unread_client_messages") or 0) > 0:
            continue

        item["recommended_action"] = (
            "Открыть консультацию" if is_today else "Подготовиться к консультации"
        )
        item["priority"] = "high" if is_today else "normal"
        item["action_note"] = _business_schedule_note(scheduled_at)

    payload["cases"] = cases
    payload["summary"] = _rebuild_workspace_summary(cases)
    return payload


router.add_api_route(
    "/lawyer/ui",
    legacy_lawyer_ui_redirect,
    methods=["GET"],
    name="lawyer_ui_redirect",
)
router.add_api_route(
    "/lawyer/workspace/ui",
    contract_aware_lawyer_workspace_ui,
    methods=["GET"],
    name="lawyer_workspace_ui",
)
router.add_api_route(
    "/lawyer/workspace/data",
    business_timezone_guided_workspace_data,
    methods=["GET"],
    name="lawyer_workspace_data",
)
router.add_api_route(
    "/lawyer/consultation-desk/ui",
    lawyer_consultation_runtime_ui,
    methods=["GET"],
    name="lawyer_consultation_desk_ui",
)
router.add_api_route(
    "/lawyer/consultation-desk/data",
    consultation_desk_data,
    methods=["GET"],
    name="lawyer_consultation_desk_data",
)
router.add_api_route(
    "/lawyer/consultations/{consultation_id}/complete",
    guarded_complete_consultation,
    methods=["POST"],
    name="lawyer_complete_consultation",
)
router.add_api_route(
    "/lawyer/consultations/{consultation_id}/client-no-show",
    guided_client_no_show,
    methods=["POST"],
    name="lawyer_client_no_show",
)

__all__ = ["router"]
