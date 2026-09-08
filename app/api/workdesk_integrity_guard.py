from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workdesk_integrity import build_workdesk_integrity
from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.m1_internal_payment_recovery import M1InternalPaymentRecoveryService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    normalize_roles,
)
from app.security.document_access import resolve_document_actor

router = APIRouter(tags=["workdesk-integrity-guard"])

_TERMINAL_CASE_STATUSES = {
    CaseStatus.M1_CLOSED.value,
    CaseStatus.M2_CLOSED.value,
    CaseStatus.ARCHIVED.value,
}
_M2_CLIENT_OPEN_PAYMENT_STATUSES = {
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_CONFIRMATION.value,
}


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


def _new_item(case: Case, user: User, issue: dict) -> dict:
    return {
        "case_id": int(case.id),
        "case_number": case.case_number,
        "client_name": user.full_name,
        "route": case.route,
        "status": str(case.status),
        "status_label": get_client_visible_status(str(case.status)),
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        "severity": issue["severity"],
        "issue_count": 1,
        "issues": [issue],
        "primary_action": {
            "label": issue.get("action_label") or "Открыть карточку",
            "href": issue.get("action_href"),
            "kind": issue.get("action_kind") or "case",
        },
    }


def _append_issue(items: list[dict], case: Case, user: User, issue: dict) -> None:
    item = next(
        (x for x in items if int(x.get("case_id") or 0) == int(case.id)),
        None,
    )
    if item is None:
        items.append(_new_item(case, user, issue))
        return
    if not any(x.get("code") == issue.get("code") for x in item.get("issues") or []):
        item.setdefault("issues", []).append(issue)
    _rebuild_item(item)


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
        _append_issue(items, case, user, issue)
        item = next(x for x in items if int(x["case_id"]) == case_id)

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


async def _add_stuck_internal_paid_stages(db: AsyncSession, items: list[dict]) -> None:
    source_statuses = (
        CaseStatus.M1_PAYMENT_30000_RECEIVED.value,
        CaseStatus.M1_PAYMENT_70000_RECEIVED.value,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
    )
    rows = list(
        (
            await db.execute(
                select(Case, User)
                .join(User, User.id == Case.client_id)
                .where(Case.status.in_(source_statuses))
                .order_by(Case.updated_at.asc(), Case.id.asc())
            )
        ).all()
    )
    if not rows:
        return

    case_ids = [int(case.id) for case, _user in rows]
    paid_rows = list(
        (
            await db.execute(
                select(Payment)
                .where(Payment.case_id.in_(case_ids))
                .where(Payment.status == PaymentStatus.PAID.value)
                .order_by(Payment.created_at.asc(), Payment.id.asc())
            )
        ).scalars().all()
    )
    paid_by_case: dict[int, list[Payment]] = {}
    for payment in paid_rows:
        paid_by_case.setdefault(int(payment.case_id), []).append(payment)

    for case, user in rows:
        plan = M1InternalPaymentRecoveryService.plan_for_status(case.status)
        if plan is None:
            continue
        proof = next(
            (
                payment
                for payment in reversed(paid_by_case.get(int(case.id), []))
                if str(payment.payment_code) == plan.payment_code.value
            ),
            None,
        )
        if proof is not None:
            issue = {
                "code": "m1_internal_paid_stage_stuck",
                "severity": "critical",
                "title": "Оплата подтверждена, но дело осталось на внутреннем платёжном статусе",
                "detail": (
                    f"Найден PAID-платёж #{proof.id} нужного назначения. Штатный flow должен был "
                    f"сразу перейти в «{get_client_visible_status(plan.target.value)}». "
                    "Восстановление проверит статус и доказательство ещё раз под блокировкой."
                ),
                "action_label": "Восстановить этап",
                "action_href": f"/admin/workdesk/cases/{int(case.id)}/recover-payment-stage/ui",
                "action_kind": None,
            }
        else:
            issue = {
                "code": "m1_internal_paid_stage_without_proof",
                "severity": "critical",
                "title": "Внутренний платёжный статус не подтверждён фактическим PAID-платежом",
                "detail": (
                    "Автоматическое продолжение заблокировано: у дела нет PAID-платежа нужного "
                    "назначения. Нельзя вручную переводить дело вперёд без финансового доказательства."
                ),
                "action_label": "Разобрать технически",
                "action_href": f"/admin/technical-cases/ui?case_id={int(case.id)}",
                "action_kind": None,
            }
        _append_issue(items, case, user, issue)


async def _add_unreachable_assignees(db: AsyncSession, items: list[dict]) -> None:
    login_rows = (
        await db.execute(
            select(AdminUser.email, AdminUser.role).where(
                AdminUser.is_active.is_(True),
                AdminUser.email.is_not(None),
            )
        )
    ).all()
    login_ready_emails = {
        str(email).strip().lower()
        for email, roles in login_rows
        if str(email or "").strip()
        and ROLE_LAWYER in normalize_roles(roles)
    }

    rows = list(
        (
            await db.execute(
                select(Case, User, Lawyer)
                .join(User, User.id == Case.client_id)
                .join(Lawyer, Lawyer.id == Case.assigned_lawyer_id)
                .where(Case.assigned_lawyer_id.is_not(None))
                .where(Case.status.notin_(tuple(_TERMINAL_CASE_STATUSES)))
                .order_by(Case.updated_at.asc(), Case.id.asc())
                .limit(1000)
            )
        ).all()
    )
    for case, user, lawyer in rows:
        email = str(lawyer.email or "").strip().lower()
        if lawyer.is_active and email and email in login_ready_emails:
            continue
        issue = {
            "code": "assigned_lawyer_cannot_login",
            "severity": "critical",
            "title": "Назначенный юрист не может полноценно войти в рабочий кабинет",
            "detail": (
                f"Дело назначено «{lawyer.full_name}», но профиль не связан с активным "
                "персональным аккаунтом с ролью lawyer либо профиль отключён. До исправления "
                "ответственность и SLA формально существуют, но действие юриста может быть невозможно."
            ),
            "action_label": "Переназначить юриста",
            "action_href": None,
            "action_kind": "assign",
        }
        _append_issue(items, case, user, issue)


async def _add_stuck_money_received(db: AsyncSession, items: list[dict]) -> None:
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
        _append_issue(items, case, user, issue)


async def _add_m2_payment_reservation_integrity(
    db: AsyncSession,
    items: list[dict],
) -> None:
    """Expose stale provider-link risk before the client clicks Telegram."""

    rows = list(
        (
            await db.execute(
                select(Payment, Case, User)
                .join(Case, Case.id == Payment.case_id)
                .join(User, User.id == Case.client_id)
                .where(Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT.value)
                .where(Payment.status.in_(tuple(_M2_CLIENT_OPEN_PAYMENT_STATUSES)))
                .where(Case.status.notin_(tuple(_TERMINAL_CASE_STATUSES)))
                .order_by(Payment.created_at.asc(), Payment.id.asc())
                .limit(1000)
            )
        ).all()
    )
    if not rows:
        return

    cases: dict[int, tuple[Case, User]] = {}
    payments_by_case: dict[int, list[Payment]] = {}
    for payment, case, user in rows:
        case_id = int(case.id)
        cases[case_id] = (case, user)
        payments_by_case.setdefault(case_id, []).append(payment)

    case_ids = list(cases)
    consultation_rows = list(
        (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id.in_(case_ids))
                .order_by(Consultation.case_id.asc(), Consultation.created_at.asc(), Consultation.id.asc())
            )
        ).scalars().all()
    )
    latest_consultation: dict[int, Consultation] = {}
    for consultation in consultation_rows:
        latest_consultation[int(consultation.case_id)] = consultation

    slot_ids = {
        int(consultation.slot_id)
        for consultation in latest_consultation.values()
        if consultation.slot_id is not None
    }
    slots: dict[int, ConsultationSlot] = {}
    if slot_ids:
        slot_rows = list(
            (
                await db.execute(
                    select(ConsultationSlot).where(ConsultationSlot.id.in_(slot_ids))
                )
            ).scalars().all()
        )
        slots = {int(slot.id): slot for slot in slot_rows}

    for case_id, case_payments in payments_by_case.items():
        case, user = cases[case_id]
        if str(case.status) != CaseStatus.M2_PAYMENT_PENDING.value:
            for payment in case_payments:
                _append_issue(
                    items,
                    case,
                    user,
                    {
                        "code": f"m2_live_payment_outside_payment_stage_{int(payment.id)}",
                        "severity": "critical",
                        "title": "У M2 осталась активная платёжная ссылка вне этапа оплаты",
                        "detail": (
                            f"Платёж #{payment.id} остаётся {payment.status}, хотя дело находится в "
                            f"{get_client_visible_status(str(case.status))}. Клиентский guard скроет старую ссылку, "
                            "но финансовую запись нужно разобрать, чтобы она не оставалась доступной у провайдера."
                        ),
                        "action_label": "Проверить платёж",
                        "action_href": f"/admin/payment-reviews/ui?case_id={case_id}&payment_id={int(payment.id)}",
                        "action_kind": None,
                    },
                )
            continue

        consultation = latest_consultation.get(case_id)
        slot = (
            slots.get(int(consultation.slot_id))
            if consultation is not None and consultation.slot_id is not None
            else None
        )
        if consultation is None or slot is None:
            # Base integrity already explains the missing consultation/held slot.
            continue

        expected_key = f"consultation:{int(consultation.id)}:slot:{int(slot.id)}"
        for payment in case_payments:
            if str(payment.reservation_key or "") == expected_key:
                continue
            _append_issue(
                items,
                case,
                user,
                {
                    "code": f"m2_payment_reservation_mismatch_{int(payment.id)}",
                    "severity": "critical",
                    "title": "Платёж M2 привязан не к текущему резерву консультации",
                    "detail": (
                        f"Платёж #{payment.id}: reservation_key={payment.reservation_key or 'пусто'}, "
                        f"ожидается {expected_key}. Такой provider-link нельзя считать текущей оплатой "
                        "этого слота; клиентский Telegram-контур уже блокирует его при открытии."
                    ),
                    "action_label": "Разобрать платёж",
                    "action_href": f"/admin/payment-reviews/ui?case_id={case_id}&payment_id={int(payment.id)}",
                    "action_kind": None,
                },
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
    await _add_stuck_internal_paid_stages(db, items)
    await _add_unreachable_assignees(db, items)
    await _add_stuck_money_received(db, items)
    await _add_m2_payment_reservation_integrity(db, items)
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
