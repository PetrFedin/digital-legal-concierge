from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workdesk_integrity_guard import workdesk_integrity_guard as base_integrity
from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.user import User

router = APIRouter(tags=["active-case-integrity-guard"])

_TERMINAL = {
    CaseStatus.M1_CLOSED.value,
    CaseStatus.M2_CLOSED.value,
    CaseStatus.ARCHIVED.value,
}
_MAX_ACTIVE_SCAN = 5000


def _severity_key(item: dict) -> tuple[int, str, int]:
    return (
        0 if item.get("severity") == "critical" else 1,
        str(item.get("updated_at") or ""),
        int(item.get("case_id") or 0),
    )


def _rebuild_item(item: dict) -> None:
    issues = list(item.get("issues") or [])
    issues.sort(
        key=lambda issue: (
            0 if issue.get("severity") == "critical" else 1,
            str(issue.get("code") or ""),
        )
    )
    item["issues"] = issues
    item["issue_count"] = len(issues)
    item["severity"] = (
        "critical"
        if any(issue.get("severity") == "critical" for issue in issues)
        else "warning"
    )
    if issues:
        first = issues[0]
        item["primary_action"] = {
            "label": first.get("action_label") or "Открыть карточку",
            "href": first.get("action_href"),
            "kind": first.get("action_kind") or "case",
        }


def _new_item(case: Case, user: User, issue: dict) -> dict:
    return {
        "case_id": int(case.id),
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
            "label": issue["action_label"],
            "href": issue["action_href"],
            "kind": None,
        },
    }


async def _active_case_conflicts(db: AsyncSession) -> tuple[dict[int, list[tuple[Case, User]]], bool]:
    rows = list(
        (
            await db.execute(
                select(Case, User)
                .join(User, User.id == Case.client_id)
                .where(Case.status.notin_(tuple(_TERMINAL)))
                .order_by(Case.client_id.asc(), Case.created_at.asc(), Case.id.asc())
                .limit(_MAX_ACTIVE_SCAN + 1)
            )
        ).all()
    )
    truncated = len(rows) > _MAX_ACTIVE_SCAN
    rows = rows[:_MAX_ACTIVE_SCAN]
    grouped: dict[int, list[tuple[Case, User]]] = defaultdict(list)
    for case, user in rows:
        grouped[int(case.client_id)].append((case, user))
    return {
        client_id: items
        for client_id, items in grouped.items()
        if len(items) > 1
    }, truncated


def _duplicate_issue(case: Case, siblings: list[tuple[Case, User]]) -> dict:
    other = [
        item
        for item, _user in siblings
        if int(item.id) != int(case.id)
    ]
    summary = "; ".join(
        f"{item.case_number} ({item.route or 'без маршрута'} / {get_client_visible_status(str(item.status))})"
        for item in other
    )
    return {
        "code": "multiple_active_cases_for_client",
        "severity": "critical",
        "title": "У клиента одновременно несколько активных дел",
        "detail": (
            "Нарушен базовый инвариант продукта: у одного клиента должен быть только один "
            "незакрытый маршрут M1 или M2. Другие активные дела: "
            f"{summary or 'не удалось перечислить'}. Ничего не закрывайте автоматически: "
            "сверьте историю, документы, оплаты и консультации и явно определите, какое дело "
            "является текущим."
        ),
        "action_label": "Открыть это дело",
        "action_href": f"/admin/workdesk/ui?case_id={int(case.id)}",
        "action_kind": None,
    }


@router.get("/admin/workdesk/integrity")
async def active_case_integrity_guard(
    request: Request,
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Preserve the full integrity audit and add the one-active-case invariant.

    This exact route is mounted before the historical integrity guard. Calling
    the existing handler directly keeps every current payment/document/M1/M2
    check and its auth contract; we request the full scan before applying this
    final invariant and only then slice to the UI limit.
    """

    result = await base_integrity(
        request=request,
        limit=_MAX_ACTIVE_SCAN,
        db=db,
        x_admin_token=x_admin_token,
    )
    items = list(result.get("items") or [])
    by_case_id = {
        int(item.get("case_id") or 0): item
        for item in items
        if int(item.get("case_id") or 0) > 0
    }

    conflicts, scan_truncated = await _active_case_conflicts(db)
    for siblings in conflicts.values():
        for case, user in siblings:
            case_id = int(case.id)
            issue = _duplicate_issue(case, siblings)
            item = by_case_id.get(case_id)
            if item is None:
                item = _new_item(case, user, issue)
                items.append(item)
                by_case_id[case_id] = item
            elif not any(
                existing.get("code") == issue["code"]
                for existing in item.get("issues") or []
            ):
                item.setdefault("issues", []).append(issue)
                _rebuild_item(item)

    items.sort(key=_severity_key)
    total = len(items)
    critical = sum(1 for item in items if item.get("severity") == "critical")
    requested_limit = int(limit)
    result["items"] = items[:requested_limit]
    result["count"] = len(result["items"])
    result["total"] = total
    result["critical_count"] = critical
    result["warning_count"] = total - critical
    result["result_truncated"] = total > requested_limit
    result["duplicate_active_client_count"] = len(conflicts)
    result["active_case_invariant_scan_truncated"] = scan_truncated
    return result


__all__ = ["router"]
