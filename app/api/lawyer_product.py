"""Single runtime owner for the existing lawyer M1/M2 working surface.

This product router replaces precedence between the historical workspace,
consultation, rejection and UI composite routers. It registers the same shipped
business actions once; no new legal route is introduced.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
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
from app.api.lawyer_case_card import router as lawyer_case_card_router
from app.api.lawyer_m1_rejection import router as lawyer_m1_rejection_router
from app.api.lawyer_poa import router as lawyer_poa_router
from app.db.session import get_db
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.self_filing_package import SelfFilingPackage
from app.presentation_time import format_business_datetime, to_business_timezone

router = APIRouter(tags=["lawyer-product"])


# Existing M1 legal actions that historically arrived through the rejection UI
# composite remain mounted here as domain action routers.
router.include_router(lawyer_m1_rejection_router)
router.include_router(lawyer_poa_router)
router.include_router(lawyer_case_card_router)


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

    m1_ids = [
        int(item.get("case_id"))
        for item in cases
        if str(item.get("route") or "").upper() == "M1" and item.get("case_id")
    ]
    case_modes: dict[int, str | None] = {}
    package_by_case: dict[int, SelfFilingPackage] = {}
    if m1_ids:
        case_modes = {
            int(case_id): service_mode
            for case_id, service_mode in (
                await db.execute(
                    select(Case.id, Case.service_mode).where(Case.id.in_(m1_ids))
                )
            ).all()
        }
        packages = list(
            (
                await db.execute(
                    select(SelfFilingPackage).where(
                        SelfFilingPackage.case_id.in_(m1_ids)
                    )
                )
            ).scalars().all()
        )
        package_by_case = {int(item.case_id): item for item in packages}

    for item in cases:
        if str(item.get("route") or "").upper() == "M1":
            case_id = int(item.get("case_id") or 0)
            service_mode = str(case_modes.get(case_id) or "")
            item["service_mode"] = service_mode or None
            if service_mode == M1ServiceMode.SELF_FILING_PACKAGE.value:
                item["route_label"] = "Пакет для самостоятельной подачи"
                package = package_by_case.get(case_id)
                if package is not None:
                    item["self_filing"] = {
                        "package_id": int(package.id),
                        "package_version": int(package.version or 1),
                        "package_status": package.status,
                        "documents_complete_at": (
                            package.documents_complete_at.isoformat()
                            if package.documents_complete_at
                            else None
                        ),
                        "court_name": package.court_name,
                        "payment_confirmed_at": (
                            package.payment_confirmed_at.isoformat()
                            if package.payment_confirmed_at
                            else None
                        ),
                        "sla_started_at": (
                            package.sla_started_at.isoformat()
                            if package.sla_started_at
                            else None
                        ),
                        "sla_due_at": (
                            package.sla_due_at.isoformat()
                            if package.sla_due_at
                            else None
                        ),
                        "email_delivery_status": package.email_delivery_status,
                        "email_delivery_attempts": int(
                            package.email_delivery_attempts or 0
                        ),
                    }

                status = str(item.get("status") or "")
                unread = int(item.get("unread_client_messages") or 0)
                if status in {
                    "M1_SELF_FILING_DOCUMENTS_RECEIVED",
                    "M1_SELF_FILING_LAWYER_REVIEW",
                } and not unread:
                    item["priority"] = "high"
                    item["recommended_action"] = "Проверить комплект и подсудность"
                    item["action_note"] = (
                        "Подтвердите все приложения и конкретный суд до открытия 15 000 ₽."
                    )
                elif status == "M1_SELF_FILING_DOCS_REQUESTED" and not unread:
                    item["priority"] = "normal"
                    item["recommended_action"] = "Ожидать документы клиента"
                    item["action_note"] = (
                        "Запрос уже зафиксирован; повторно отправлять его без новых фактов не нужно."
                    )
                elif status == "M1_SELF_FILING_PAYMENT_PENDING" and not unread:
                    item["priority"] = "normal"
                    item["recommended_action"] = "Ожидать подтверждение 15 000 ₽"
                    item["action_note"] = (
                        "Срок выдачи ещё не идёт: после подтверждения оплаты результат должен быть отправлен в течение 3 календарных дней."
                    )
                elif status == "M1_SELF_FILING_PREPARATION":
                    due = package.sla_due_at if package is not None else None
                    due_utc = _parse_utc_datetime(due) if due is not None else None
                    overdue = bool(due_utc and due_utc <= now)
                    item["priority"] = "critical" if overdue else "high"
                    item["recommended_action"] = (
                        "Завершить просроченный пакет"
                        if overdue
                        else "Подготовить и утвердить итоговый пакет"
                    )
                    item["action_note"] = (
                        "Срок выдачи в течение 3 календарных дней после оплаты истёк."
                        if overdue
                        else "Утвердите все четыре финальные версии с фиксированными SHA-256 и отправьте их на подтверждённый email клиента."
                    )
                    item["sla_status"] = (
                        "SELF_FILING_OVERDUE"
                        if overdue
                        else "SELF_FILING_ACTION_PENDING"
                    )
                    item["sla_label"] = (
                        "Просрочен срок подготовки пакета"
                        if overdue
                        else "Срок подготовки пакета"
                    )
                    item["sla_due_at"] = due_utc.isoformat() if due_utc else None
                elif status == "M1_SELF_FILING_READY" and not unread:
                    item["priority"] = "high"
                    item["recommended_action"] = "Контролировать доставку готового пакета"
                    item["action_note"] = (
                        f"Email: {package.email_delivery_status if package else 'статус уточняется'}."
                    )
                elif status in {
                    "M1_SELF_FILING_PROFILE_PENDING",
                    "M1_SELF_FILING_DOCUMENTS_PENDING",
                } and not unread:
                    item["priority"] = "normal"
                    item["recommended_action"] = "Ожидать действие клиента"
                    item["action_note"] = (
                        "Клиенту уже показан точный следующий шаг в «Моём деле»."
                    )
            continue

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
