from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workdesk_integrity import build_workdesk_integrity
from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor

router = APIRouter(tags=["workdesk-integrity-guard"])


def _rebuild_item(item: dict) -> None:
    issues = list(item.get("issues") or [])
    issues.sort(key=lambda x: (0 if x.get("severity") == "critical" else 1, str(x.get("code") or "")))
    item["issues"] = issues
    item["issue_count"] = len(issues)
    item["severity"] = "critical" if any(x.get("severity") == "critical" for x in issues) else "warning"
    if issues:
        first = issues[0]
        item["primary_action"] = {
            "label": first.get("action_label") or "Открыть карточку",
            "href": first.get("action_href"),
            "kind": first.get("action_kind") or "case",
        }


def _route_actionable_exceptions(item: dict) -> None:
    case_id = int(item.get("case_id") or 0)
    changed = False
    for issue in item.get("issues") or []:
        code = str(issue.get("code") or "")
        if code == "m2_client_no_show_resolution_required":
            issue["action_label"] = "Решить неявку клиента"
            issue["action_href"] = f"/admin/consultation-outcomes/ui?case_id={case_id}"
            issue["action_kind"] = None
            changed = True
        elif code == "m2_unsupported_legacy_outcome":
            issue["action_label"] = "Открыть контроль консультаций"
            issue["action_href"] = f"/admin/consultation-outcomes/ui?case_id={case_id}"
            issue["action_kind"] = None
            changed = True
    if changed:
        _rebuild_item(item)


def _normalize_refund_pending_case(item: dict) -> None:
    if str(item.get("status")) != CaseStatus.M2_CONSULTATION_DONE.value:
        return
    issues = list(item.get("issues") or [])
    has_refund = any(x.get("code") == "active_refund_pending" for x in issues)
    if not has_refund:
        return
    item["issues"] = [
        x
        for x in issues
        if x.get("code") != "m2_done_case_consultation_status_mismatch"
    ]
    _rebuild_item(item)


async def _add_declined_refunds(db: AsyncSession, items: list[dict]) -> None:
    rows = list(
        (
            await db.execute(
                select(Payment, Case, User)
                .join(Case, Case.id == Payment.case_id)
                .join(User, User.id == Case.client_id)
                .where(Payment.status == PaymentStatus.REFUND_DECLINED.value)
                .order_by(Payment.updated_at.asc(), Payment.id.asc())
                .limit(500)
            )
        ).all()
    )
    by_case = {int(item["case_id"]): item for item in items}
    for payment, case, user in rows:
        case_id = int(case.id)
        issue = {
            "code": f"refund_declined_{int(payment.id)}",
            "severity": "warning",
            "title": "Возврат отклонён и требует повторного решения",
            "detail": (
                f"Платёж #{payment.id} не отмечен возвращённым. Устраните причину отказа, "
                "затем верните его в очередь возврата; юридический/консультационный этап "
                "не должен изменяться этой операцией."
            ),
            "action_label": "Повторить возврат",
            "action_href": f"/admin/refunds/ui?payment_id={int(payment.id)}&case_id={case_id}",
            "action_kind": None,
        }
        item = by_case.get(case_id)
        if item is None:
            item = {
                "case_id": case_id,
                "case_number": case.case_number,
                "client_name": user.full_name,
                "route": case.route,
                "status": str(case.status),
                "status_label": get_client_visible_status(str(case.status)),
                "updated_at": (
                    payment.updated_at.isoformat()
                    if payment.updated_at
                    else case.updated_at.isoformat()
                    if case.updated_at
                    else None
                ),
                "severity": "warning",
                "issue_count": 1,
                "issues": [issue],
                "primary_action": {
                    "label": issue["action_label"],
                    "href": issue["action_href"],
                    "kind": "case",
                },
            }
            items.append(item)
            by_case[case_id] = item
        elif not any(x.get("code") == issue["code"] for x in item.get("issues") or []):
            item.setdefault("issues", []).append(issue)

        # CANCELLED + M2_DONE is expected while a refund is unresolved. Once the
        # financial issue is explicit, the generic consultation mismatch is not
        # useful and would point to the wrong action screen.
        if str(case.status) == CaseStatus.M2_CONSULTATION_DONE.value:
            item["issues"] = [
                x
                for x in item.get("issues") or []
                if x.get("code") != "m2_done_case_consultation_status_mismatch"
            ]
        _rebuild_item(item)


async def _add_stuck_money_received(db: AsyncSession, items: list[dict]) -> None:
    existing = {int(item["case_id"]) for item in items}
    rows = list(
        (
            await db.execute(
                select(Case, User)
                .join(User, User.id == Case.client_id)
                .where(Case.status == CaseStatus.M1_MONEY_RECEIVED.value)
                .order_by(Case.updated_at.asc(), Case.id.asc())
            )
        ).all()
    )
    for case, user in rows:
        case_id = int(case.id)
        issue = {
            "code": "m1_money_received_stage_stuck",
            "severity": "critical",
            "title": "Факт взыскания сохранён, но финальный платёж не открыт",
            "detail": (
                "M1_MONEY_RECEIVED — внутренний переход. Штатный enforcement-сервис "
                "в той же транзакции рассчитывает success fee и переводит дело в "
                "M1_WAITING_SUCCESS_FEE. Сохранённое промежуточное состояние требует проверки."
            ),
            "action_label": "Открыть карточку",
            "action_href": None,
            "action_kind": "case",
        }
        if case_id in existing:
            item = next(x for x in items if int(x["case_id"]) == case_id)
            if not any(x.get("code") == issue["code"] for x in item.get("issues") or []):
                item.setdefault("issues", []).append(issue)
                _rebuild_item(item)
            continue
        items.append(
            {
                "case_id": case_id,
                "case_number": case.case_number,
                "client_name": user.full_name,
                "route": case.route,
                "status": str(case.status),
                "status_label": get_client_visible_status(str(case.status)),
                "updated_at": case.updated_at.isoformat() if case.updated_at else None,
                "severity": "critical",
                "issue_count": 1,
                "issues": [issue],
                "primary_action": {
                    "label": "Открыть карточку",
                    "href": None,
                    "kind": "case",
                },
            }
        )


@router.get("/admin/workdesk/integrity")
async def workdesk_integrity_guard(
    request: Request,
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")

    result = await build_workdesk_integrity(db)
    items = list(result.get("items") or [])
    for item in items:
        _normalize_refund_pending_case(item)
        _route_actionable_exceptions(item)
    await _add_declined_refunds(db, items)
    await _add_stuck_money_received(db, items)
    items.sort(
        key=lambda item: (
            0 if item.get("severity") == "critical" else 1,
            str(item.get("updated_at") or ""),
            int(item["case_id"]),
        )
    )
    total = len(items)
    critical = sum(1 for item in items if item.get("severity") == "critical")
    result["items"] = items[:limit]
    result["count"] = len(result["items"])
    result["total"] = total
    result["critical_count"] = critical
    result["warning_count"] = total - critical
    result["result_truncated"] = total > limit
    return result
