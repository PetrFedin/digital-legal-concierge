from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analytics.activity_service import (
    ACTION_EVENTS,
    BOT_DOCUMENT_ACTION,
    BOT_PAYMENT_ACTION,
    BOT_SCREEN_VIEW,
    BOT_SESSION_STARTED,
    CRM_CLIENT_ARCHIVED,
    CRM_CLIENT_RESTORED,
    ActivityService,
    load_client_archive_states,
    normalize_datetime,
)
from app.domain.cases.case_operations import load_case_history
from app.domain.cases.case_service import CaseService
from app.models.analytics_event import AnalyticsEvent
from app.models.audit_log import AuditLog
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.message import Message
from app.models.payment import Payment
from app.models.user import User


PAID_STATUSES = frozenset({"PAID"})
REFUND_STATUSES = frozenset({"REFUNDED"})
FAILED_PAYMENT_STATUSES = frozenset({"FAILED", "CANCELLED", "EXPIRED"})
OPEN_PAYMENT_STATUSES = frozenset({"PENDING", "WAITING_CONFIRMATION"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_iso(value: datetime | None) -> str | None:
    normalized = normalize_datetime(value)
    return normalized.isoformat() if normalized else None


def as_float(value: Decimal | int | float | None) -> float:
    return float(value or 0)


def clamp_limit(value: int, *, default: int = 100, maximum: int = 1000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def event_payload(event: AnalyticsEvent) -> dict[str, Any]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return dict(payload)


def event_title(event: AnalyticsEvent) -> str:
    payload = event_payload(event)
    screen = payload.get("screen") or payload.get("target")
    command = payload.get("command")
    callback_data = payload.get("callback_data")
    labels = {
        BOT_SESSION_STARTED: "Новая сессия в боте",
        BOT_SCREEN_VIEW: "Просмотр экрана",
        BOT_PAYMENT_ACTION: "Действие с оплатой",
        BOT_DOCUMENT_ACTION: "Действие с документом",
        "BOT_COMMAND": "Команда боту",
        "BOT_NAVIGATION": "Переход в боте",
        "BOT_ACTION": "Действие в боте",
        "BOT_FREE_TEXT": "Сообщение боту",
        CRM_CLIENT_ARCHIVED: "Клиент помещен в архив",
        CRM_CLIENT_RESTORED: "Клиент восстановлен из архива",
    }
    suffix = screen or command or callback_data
    base = labels.get(event.event_name, event.event_name)
    return f"{base}: {suffix}" if suffix else base


def engagement_score(
    *,
    sessions: int,
    views: int,
    actions: int,
    cases_count: int,
    paid_count: int,
    documents_count: int,
    last_activity_at: datetime | None,
) -> int:
    score = min(sessions, 10) * 2
    score += min(views, 30)
    score += min(actions, 30)
    score += min(cases_count, 5) * 5
    score += min(paid_count, 5) * 12
    score += min(documents_count, 10) * 2
    normalized = normalize_datetime(last_activity_at)
    if normalized:
        age = utcnow() - normalized
        if age <= timedelta(days=1):
            score += 10
        elif age <= timedelta(days=7):
            score += 6
        elif age <= timedelta(days=30):
            score += 3
    return max(0, min(score, 100))


def lifecycle_stage(
    *,
    cases_count: int,
    active_cases_count: int,
    calculations_count: int,
    payments_count: int,
    paid_count: int,
    consultations_count: int,
) -> str:
    if active_cases_count:
        return "active_client" if paid_count else "active_case"
    if paid_count:
        return "past_client"
    if payments_count:
        return "payment_started"
    if consultations_count:
        return "consultation_lead"
    if calculations_count:
        return "calculation_lead"
    if cases_count:
        return "case_created"
    return "visitor"


class CRMService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _users_by_query(self, query: str | None, *, cap: int = 5000) -> list[User]:
        statement = select(User).order_by(User.updated_at.desc(), User.id.desc())
        cleaned = str(query or "").strip().lower()
        if cleaned:
            like = f"%{cleaned}%"
            conditions = [
                func.lower(func.coalesce(User.full_name, "")).like(like),
                func.lower(func.coalesce(User.telegram_username, "")).like(like),
                func.lower(func.coalesce(User.phone, "")).like(like),
                func.lower(func.coalesce(User.email, "")).like(like),
            ]
            try:
                telegram_id = int(cleaned.lstrip("@"))
                conditions.append(User.telegram_id == telegram_id)
            except ValueError:
                pass
            statement = statement.where(or_(*conditions))
        return list((await self.db.execute(statement.limit(cap))).scalars().all())

    async def _load_profiles(self, users: Iterable[User]) -> list[dict[str, Any]]:
        user_list = list(users)
        ids = [user.id for user in user_list]
        if not ids:
            return []

        cases = list(
            (
                await self.db.execute(
                    select(Case)
                    .where(Case.client_id.in_(ids))
                    .order_by(Case.created_at.desc(), Case.id.desc())
                )
            )
            .scalars()
            .all()
        )
        case_ids = [case.id for case in cases]
        events = list(
            (
                await self.db.execute(
                    select(AnalyticsEvent)
                    .where(AnalyticsEvent.user_id.in_(ids))
                    .order_by(AnalyticsEvent.created_at.desc(), AnalyticsEvent.id.desc())
                )
            )
            .scalars()
            .all()
        )
        payments = (
            list(
                (
                    await self.db.execute(
                        select(Payment).where(Payment.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        documents = (
            list(
                (
                    await self.db.execute(
                        select(Document).where(Document.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        calculations = (
            list(
                (
                    await self.db.execute(
                        select(Calculation).where(Calculation.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        consultations = (
            list(
                (
                    await self.db.execute(
                        select(Consultation).where(Consultation.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        archive_states = await load_client_archive_states(self.db, ids)

        cases_by_user: dict[int, list[Case]] = defaultdict(list)
        case_to_user: dict[int, int] = {}
        for case in cases:
            cases_by_user[case.client_id].append(case)
            case_to_user[case.id] = case.client_id

        events_by_user: dict[int, list[AnalyticsEvent]] = defaultdict(list)
        for event in events:
            if event.user_id is not None:
                events_by_user[event.user_id].append(event)

        payments_by_user: dict[int, list[Payment]] = defaultdict(list)
        for payment in payments:
            user_id = case_to_user.get(payment.case_id)
            if user_id is not None:
                payments_by_user[user_id].append(payment)

        documents_by_user: Counter[int] = Counter()
        for document in documents:
            user_id = case_to_user.get(document.case_id)
            if user_id is not None:
                documents_by_user[user_id] += 1

        calculations_by_user: Counter[int] = Counter()
        for calculation in calculations:
            user_id = case_to_user.get(calculation.case_id)
            if user_id is not None:
                calculations_by_user[user_id] += 1

        consultations_by_user: Counter[int] = Counter()
        for consultation in consultations:
            user_id = case_to_user.get(consultation.case_id)
            if user_id is not None:
                consultations_by_user[user_id] += 1

        profiles: list[dict[str, Any]] = []
        for user in user_list:
            user_cases = cases_by_user[user.id]
            user_events = events_by_user[user.id]
            user_payments = payments_by_user[user.id]
            active_cases = [
                case for case in user_cases if case.status not in CaseService.CLOSED_STATUSES
            ]
            closed_cases = [
                case for case in user_cases if case.status in CaseService.CLOSED_STATUSES
            ]
            paid = [payment for payment in user_payments if payment.status in PAID_STATUSES]
            refunds = [
                payment for payment in user_payments if payment.status in REFUND_STATUSES
            ]
            open_payments = [
                payment for payment in user_payments if payment.status in OPEN_PAYMENT_STATUSES
            ]
            failed_payments = [
                payment
                for payment in user_payments
                if payment.status in FAILED_PAYMENT_STATUSES
            ]
            sessions = sum(
                1 for event in user_events if event.event_name == BOT_SESSION_STARTED
            )
            views = sum(1 for event in user_events if event.event_name == BOT_SCREEN_VIEW)
            actions = sum(1 for event in user_events if event.event_name in ACTION_EVENTS)
            first_activity = min(
                (event.created_at for event in user_events),
                default=user.created_at,
            )
            last_activity = max(
                (event.created_at for event in user_events),
                default=user.updated_at,
            )
            paid_amount = sum((payment.amount for payment in paid), Decimal("0"))
            refunded_amount = sum(
                (payment.amount for payment in refunds), Decimal("0")
            )
            score = engagement_score(
                sessions=sessions,
                views=views,
                actions=actions,
                cases_count=len(user_cases),
                paid_count=len(paid),
                documents_count=documents_by_user[user.id],
                last_activity_at=last_activity,
            )
            profiles.append(
                {
                    "id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": user.telegram_username,
                    "full_name": user.full_name,
                    "phone": user.phone,
                    "email": user.email,
                    "blocked": bool(user.is_blocked),
                    "archived": bool(archive_states.get(user.id, False)),
                    "created_at": as_iso(user.created_at),
                    "first_activity_at": as_iso(first_activity),
                    "last_activity_at": as_iso(last_activity),
                    "sessions": sessions,
                    "views": views,
                    "actions": actions,
                    "cases_count": len(user_cases),
                    "active_cases_count": len(active_cases),
                    "closed_cases_count": len(closed_cases),
                    "calculations_count": calculations_by_user[user.id],
                    "consultations_count": consultations_by_user[user.id],
                    "documents_count": documents_by_user[user.id],
                    "payments_count": len(user_payments),
                    "paid_payments_count": len(paid),
                    "open_payments_count": len(open_payments),
                    "failed_payments_count": len(failed_payments),
                    "paid_amount": as_float(paid_amount),
                    "refunded_amount": as_float(refunded_amount),
                    "net_revenue": as_float(paid_amount - refunded_amount),
                    "average_check": (
                        as_float(paid_amount / len(paid)) if paid else 0.0
                    ),
                    "engagement_score": score,
                    "lifecycle_stage": lifecycle_stage(
                        cases_count=len(user_cases),
                        active_cases_count=len(active_cases),
                        calculations_count=calculations_by_user[user.id],
                        payments_count=len(user_payments),
                        paid_count=len(paid),
                        consultations_count=consultations_by_user[user.id],
                    ),
                }
            )
        return profiles

    async def list_clients(
        self,
        *,
        query: str | None = None,
        archive: str = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        profiles = await self._load_profiles(await self._users_by_query(query))
        archive_filter = str(archive or "active").lower()
        if archive_filter == "archived":
            profiles = [item for item in profiles if item["archived"]]
        elif archive_filter == "active":
            profiles = [item for item in profiles if not item["archived"]]
        elif archive_filter != "all":
            raise ValueError("archive must be active, archived or all")

        profiles.sort(
            key=lambda item: (
                item["last_activity_at"] or "",
                item["id"],
            ),
            reverse=True,
        )
        total = len(profiles)
        safe_offset = max(0, int(offset or 0))
        safe_limit = clamp_limit(limit, maximum=500)
        return {
            "items": profiles[safe_offset : safe_offset + safe_limit],
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "archive": archive_filter,
            "query": query,
        }

    async def archive_client(
        self,
        *,
        user_id: int,
        archived: bool,
        actor_id: int | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        user = (
            await self.db.execute(select(User).where(User.id == user_id))
        ).scalars().first()
        if not user:
            raise LookupError("client not found")
        event_name = CRM_CLIENT_ARCHIVED if archived else CRM_CLIENT_RESTORED
        await ActivityService(self.db).record(
            event_name=event_name,
            user_id=user.id,
            source="admin_crm",
            payload={"actor_id": actor_id, "reason": reason},
        )
        await self.db.flush()
        return {"user_id": user.id, "archived": archived}

    async def client_card(self, user_id: int) -> dict[str, Any]:
        user = (
            await self.db.execute(select(User).where(User.id == user_id))
        ).scalars().first()
        if not user:
            raise LookupError("client not found")
        profile = (await self._load_profiles([user]))[0]
        cases = list(
            (
                await self.db.execute(
                    select(Case)
                    .where(Case.client_id == user.id)
                    .order_by(Case.created_at.desc(), Case.id.desc())
                )
            )
            .scalars()
            .all()
        )
        case_ids = [case.id for case in cases]
        payments = (
            list(
                (
                    await self.db.execute(
                        select(Payment)
                        .where(Payment.case_id.in_(case_ids))
                        .order_by(Payment.created_at.desc(), Payment.id.desc())
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        documents = (
            list(
                (
                    await self.db.execute(
                        select(Document)
                        .where(Document.case_id.in_(case_ids))
                        .order_by(Document.created_at.desc(), Document.id.desc())
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        consultations = (
            list(
                (
                    await self.db.execute(
                        select(Consultation)
                        .where(Consultation.case_id.in_(case_ids))
                        .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        message_count = (
            int(
                (
                    await self.db.execute(
                        select(func.count(Message.id)).where(Message.case_id.in_(case_ids))
                    )
                ).scalar_one()
            )
            if case_ids
            else 0
        )
        recent_events = list(
            (
                await self.db.execute(
                    select(AnalyticsEvent)
                    .where(AnalyticsEvent.user_id == user.id)
                    .order_by(AnalyticsEvent.created_at.desc(), AnalyticsEvent.id.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
        return {
            "profile": profile,
            "cases": [
                {
                    "id": case.id,
                    "number": case.case_number,
                    "title": case.title,
                    "route": case.route,
                    "status": case.status,
                    "next_action": case.next_action,
                    "lawyer_id": case.assigned_lawyer_id,
                    "source": case.source,
                    "archived": case.status in CaseService.CLOSED_STATUSES,
                    "created_at": as_iso(case.created_at),
                    "updated_at": as_iso(case.updated_at),
                }
                for case in cases
            ],
            "payments": [
                {
                    "id": payment.id,
                    "case_id": payment.case_id,
                    "code": payment.payment_code,
                    "title": payment.title,
                    "amount": as_float(payment.amount),
                    "currency": payment.currency,
                    "status": payment.status,
                    "provider": payment.provider,
                    "manual_review_required": bool(payment.manual_review_required),
                    "created_at": as_iso(payment.created_at),
                    "processed_at": as_iso(payment.processed_at),
                }
                for payment in payments
            ],
            "documents": [
                {
                    "id": document.id,
                    "case_id": document.case_id,
                    "type": document.document_type,
                    "title": document.title,
                    "file_name": document.file_name,
                    "status": document.status,
                    "version": document.version,
                    "created_at": as_iso(document.created_at),
                }
                for document in documents
            ],
            "consultations": [
                {
                    "id": consultation.id,
                    "case_id": consultation.case_id,
                    "related_case_id": consultation.related_case_id,
                    "lawyer_id": consultation.lawyer_id,
                    "status": consultation.status,
                    "type": consultation.consultation_type,
                    "subject_type": consultation.subject_type,
                    "scheduled_at": as_iso(consultation.scheduled_at),
                    "created_at": as_iso(consultation.created_at),
                }
                for consultation in consultations
            ],
            "messages_count": message_count,
            "recent_activity": [self._event_payload(event) for event in recent_events],
        }

    def _event_payload(self, event: AnalyticsEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "type": "analytics",
            "event": event.event_name,
            "title": event_title(event),
            "case_id": event.case_id,
            "route": event.route,
            "source": event.source,
            "payload": event_payload(event),
            "created_at": as_iso(event.created_at),
        }

    async def client_timeline(self, user_id: int, *, limit: int = 300) -> list[dict[str, Any]]:
        user = (
            await self.db.execute(select(User).where(User.id == user_id))
        ).scalars().first()
        if not user:
            raise LookupError("client not found")
        safe_limit = clamp_limit(limit, maximum=1000)
        cases = list(
            (
                await self.db.execute(select(Case).where(Case.client_id == user.id))
            )
            .scalars()
            .all()
        )
        case_ids = [case.id for case in cases]
        events = list(
            (
                await self.db.execute(
                    select(AnalyticsEvent)
                    .where(AnalyticsEvent.user_id == user.id)
                    .order_by(AnalyticsEvent.created_at.desc(), AnalyticsEvent.id.desc())
                    .limit(safe_limit)
                )
            )
            .scalars()
            .all()
        )
        timeline = [self._event_payload(event) for event in events]

        if case_ids:
            audits = list(
                (
                    await self.db.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "case")
                        .where(AuditLog.entity_id.in_(case_ids))
                        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                        .limit(safe_limit)
                    )
                )
                .scalars()
                .all()
            )
            timeline.extend(
                {
                    "id": audit.id,
                    "type": "case_audit",
                    "event": audit.action,
                    "title": audit.comment or audit.action,
                    "case_id": audit.entity_id,
                    "actor_type": audit.actor_type,
                    "actor_id": audit.actor_id,
                    "old_value": audit.old_value,
                    "new_value": audit.new_value,
                    "created_at": as_iso(audit.created_at),
                }
                for audit in audits
            )
            payments = list(
                (
                    await self.db.execute(
                        select(Payment)
                        .where(Payment.case_id.in_(case_ids))
                        .order_by(Payment.created_at.desc(), Payment.id.desc())
                        .limit(safe_limit)
                    )
                )
                .scalars()
                .all()
            )
            timeline.extend(
                {
                    "id": payment.id,
                    "type": "payment",
                    "event": f"PAYMENT_{payment.status}",
                    "title": f"Оплата: {payment.title}",
                    "case_id": payment.case_id,
                    "amount": as_float(payment.amount),
                    "currency": payment.currency,
                    "status": payment.status,
                    "created_at": as_iso(payment.created_at),
                }
                for payment in payments
            )
            documents = list(
                (
                    await self.db.execute(
                        select(Document)
                        .where(Document.case_id.in_(case_ids))
                        .order_by(Document.created_at.desc(), Document.id.desc())
                        .limit(safe_limit)
                    )
                )
                .scalars()
                .all()
            )
            timeline.extend(
                {
                    "id": document.id,
                    "type": "document",
                    "event": "DOCUMENT_REGISTERED",
                    "title": document.title or document.file_name,
                    "case_id": document.case_id,
                    "status": document.status,
                    "created_at": as_iso(document.created_at),
                }
                for document in documents
            )
            consultations = list(
                (
                    await self.db.execute(
                        select(Consultation)
                        .where(Consultation.case_id.in_(case_ids))
                        .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                        .limit(safe_limit)
                    )
                )
                .scalars()
                .all()
            )
            timeline.extend(
                {
                    "id": consultation.id,
                    "type": "consultation",
                    "event": f"CONSULTATION_{consultation.status}",
                    "title": "Консультация",
                    "case_id": consultation.case_id,
                    "status": consultation.status,
                    "scheduled_at": as_iso(consultation.scheduled_at),
                    "created_at": as_iso(consultation.created_at),
                }
                for consultation in consultations
            )

        timeline.sort(
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )
        return timeline[:safe_limit]

    async def archived_cases(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        statement = (
            select(Case, User)
            .join(User, User.id == Case.client_id)
            .where(Case.status.in_(CaseService.CLOSED_STATUSES))
            .order_by(Case.updated_at.desc(), Case.id.desc())
        )
        cleaned = str(query or "").strip().lower()
        if cleaned:
            like = f"%{cleaned}%"
            statement = statement.where(
                or_(
                    func.lower(Case.case_number).like(like),
                    func.lower(func.coalesce(Case.title, "")).like(like),
                    func.lower(func.coalesce(User.full_name, "")).like(like),
                    func.lower(func.coalesce(User.telegram_username, "")).like(like),
                )
            )
        safe_offset = max(0, int(offset or 0))
        safe_limit = clamp_limit(limit, maximum=500)
        rows = (
            await self.db.execute(statement.offset(safe_offset).limit(safe_limit))
        ).all()
        count_statement = (
            select(func.count(Case.id))
            .join(User, User.id == Case.client_id)
            .where(Case.status.in_(CaseService.CLOSED_STATUSES))
        )
        if cleaned:
            like = f"%{cleaned}%"
            count_statement = count_statement.where(
                or_(
                    func.lower(Case.case_number).like(like),
                    func.lower(func.coalesce(Case.title, "")).like(like),
                    func.lower(func.coalesce(User.full_name, "")).like(like),
                    func.lower(func.coalesce(User.telegram_username, "")).like(like),
                )
            )
        total = int((await self.db.execute(count_statement)).scalar_one())
        return {
            "items": [
                {
                    "id": case.id,
                    "number": case.case_number,
                    "title": case.title,
                    "route": case.route,
                    "status": case.status,
                    "next_action": case.next_action,
                    "lawyer_id": case.assigned_lawyer_id,
                    "client": {
                        "id": user.id,
                        "telegram_id": user.telegram_id,
                        "full_name": user.full_name,
                        "username": user.telegram_username,
                    },
                    "created_at": as_iso(case.created_at),
                    "closed_at": as_iso(case.updated_at),
                }
                for case, user in rows
            ],
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
        }

    async def case_archive_card(self, case_id: int) -> dict[str, Any]:
        case = (
            await self.db.execute(select(Case).where(Case.id == case_id))
        ).scalars().first()
        if not case:
            raise LookupError("case not found")
        client = (
            await self.db.execute(select(User).where(User.id == case.client_id))
        ).scalars().first()
        payments = list(
            (
                await self.db.execute(
                    select(Payment)
                    .where(Payment.case_id == case.id)
                    .order_by(Payment.created_at.desc(), Payment.id.desc())
                )
            )
            .scalars()
            .all()
        )
        documents = list(
            (
                await self.db.execute(
                    select(Document)
                    .where(Document.case_id == case.id)
                    .order_by(Document.created_at.desc(), Document.id.desc())
                )
            )
            .scalars()
            .all()
        )
        consultations = list(
            (
                await self.db.execute(
                    select(Consultation)
                    .where(Consultation.case_id == case.id)
                    .order_by(Consultation.created_at.desc(), Consultation.id.desc())
                )
            )
            .scalars()
            .all()
        )
        history = await load_case_history(
            self.db,
            case_id=case.id,
            client_view=False,
            limit=500,
        )
        return {
            "case": {
                "id": case.id,
                "number": case.case_number,
                "title": case.title,
                "route": case.route,
                "status": case.status,
                "source": case.source,
                "next_action": case.next_action,
                "lawyer_id": case.assigned_lawyer_id,
                "internal_comment": case.internal_comment,
                "created_at": as_iso(case.created_at),
                "updated_at": as_iso(case.updated_at),
                "archived": case.status in CaseService.CLOSED_STATUSES,
            },
            "client": (
                {
                    "id": client.id,
                    "telegram_id": client.telegram_id,
                    "full_name": client.full_name,
                    "username": client.telegram_username,
                    "phone": client.phone,
                    "email": client.email,
                }
                if client
                else None
            ),
            "history": history,
            "payments": [
                {
                    "id": item.id,
                    "title": item.title,
                    "amount": as_float(item.amount),
                    "currency": item.currency,
                    "status": item.status,
                    "provider": item.provider,
                    "created_at": as_iso(item.created_at),
                    "processed_at": as_iso(item.processed_at),
                }
                for item in payments
            ],
            "documents": [
                {
                    "id": item.id,
                    "type": item.document_type,
                    "title": item.title,
                    "file_name": item.file_name,
                    "status": item.status,
                    "version": item.version,
                    "created_at": as_iso(item.created_at),
                }
                for item in documents
            ],
            "consultations": [
                {
                    "id": item.id,
                    "status": item.status,
                    "type": item.consultation_type,
                    "lawyer_id": item.lawyer_id,
                    "scheduled_at": as_iso(item.scheduled_at),
                    "created_at": as_iso(item.created_at),
                }
                for item in consultations
            ],
        }

    async def analytics_overview(self, *, days: int = 30) -> dict[str, Any]:
        safe_days = max(1, min(int(days or 30), 3660))
        since = utcnow() - timedelta(days=safe_days)
        events = list(
            (
                await self.db.execute(
                    select(AnalyticsEvent).where(AnalyticsEvent.created_at >= since)
                )
            )
            .scalars()
            .all()
        )
        users = list((await self.db.execute(select(User))).scalars().all())
        user_ids = {event.user_id for event in events if event.user_id is not None}
        sessions = [event for event in events if event.event_name == BOT_SESSION_STARTED]
        views = [event for event in events if event.event_name == BOT_SCREEN_VIEW]
        actions = [event for event in events if event.event_name in ACTION_EVENTS]
        active_by_day: dict[str, set[int]] = defaultdict(set)
        events_by_day: Counter[str] = Counter()
        screen_views: Counter[str] = Counter()
        event_counts: Counter[str] = Counter()
        for event in events:
            day = normalize_datetime(event.created_at).date().isoformat()
            events_by_day[day] += 1
            event_counts[event.event_name] += 1
            if event.user_id is not None:
                active_by_day[day].add(event.user_id)
            if event.event_name == BOT_SCREEN_VIEW:
                payload = event_payload(event)
                screen_views[str(payload.get("screen") or "unknown")] += 1

        payments = list(
            (
                await self.db.execute(
                    select(Payment).where(Payment.created_at >= since)
                )
            )
            .scalars()
            .all()
        )
        paid = [payment for payment in payments if payment.status in PAID_STATUSES]
        refunds = [payment for payment in payments if payment.status in REFUND_STATUSES]
        paid_amount = sum((item.amount for item in paid), Decimal("0"))
        refunded_amount = sum((item.amount for item in refunds), Decimal("0"))

        daily = []
        for offset in range(safe_days - 1, -1, -1):
            day = (utcnow().date() - timedelta(days=offset)).isoformat()
            daily.append(
                {
                    "date": day,
                    "active_users": len(active_by_day.get(day, set())),
                    "events": events_by_day.get(day, 0),
                }
            )

        return {
            "period_days": safe_days,
            "since": since.isoformat(),
            "registered_users_total": len(users),
            "active_users": len(user_ids),
            "sessions": len(sessions),
            "views": len(views),
            "actions": len(actions),
            "payments_created": len(payments),
            "payments_paid": len(paid),
            "payments_failed": sum(
                1 for item in payments if item.status in FAILED_PAYMENT_STATUSES
            ),
            "payments_open": sum(
                1 for item in payments if item.status in OPEN_PAYMENT_STATUSES
            ),
            "revenue": as_float(paid_amount),
            "refunds": as_float(refunded_amount),
            "net_revenue": as_float(paid_amount - refunded_amount),
            "average_check": as_float(paid_amount / len(paid)) if paid else 0.0,
            "event_counts": [
                {"event": name, "count": count}
                for name, count in event_counts.most_common()
            ],
            "screen_views": [
                {"screen": screen, "count": count}
                for screen, count in screen_views.most_common(50)
            ],
            "daily": daily,
        }

    async def funnel(self, *, days: int = 30) -> dict[str, Any]:
        safe_days = max(1, min(int(days or 30), 3660))
        since = utcnow() - timedelta(days=safe_days)
        events = list(
            (
                await self.db.execute(
                    select(AnalyticsEvent).where(AnalyticsEvent.created_at >= since)
                )
            )
            .scalars()
            .all()
        )
        visited: set[int] = set()
        calculator_opened: set[int] = set()
        case_opened: set[int] = set()
        documents_opened: set[int] = set()
        payment_interacted: set[int] = set()
        consultation_interacted: set[int] = set()
        for event in events:
            if event.user_id is None:
                continue
            visited.add(event.user_id)
            payload = event_payload(event)
            screen = str(payload.get("screen") or "").lower()
            callback_data = str(payload.get("callback_data") or "").lower()
            target = str(payload.get("target") or "").lower()
            joined = " ".join((screen, callback_data, target))
            if "calc" in joined or "calculator" in joined:
                calculator_opened.add(event.user_id)
            if "case" in joined or "my_case" in joined:
                case_opened.add(event.user_id)
            if "doc" in joined or "upload" in joined:
                documents_opened.add(event.user_id)
            if event.event_name == BOT_PAYMENT_ACTION or "pay" in joined:
                payment_interacted.add(event.user_id)
            if "consult" in joined or "m2" in joined:
                consultation_interacted.add(event.user_id)

        cases = list(
            (
                await self.db.execute(select(Case).where(Case.created_at >= since))
            )
            .scalars()
            .all()
        )
        case_to_user = {case.id: case.client_id for case in cases}
        case_ids = list(case_to_user)
        calculations = (
            list(
                (
                    await self.db.execute(
                        select(Calculation).where(Calculation.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        payments = (
            list(
                (
                    await self.db.execute(
                        select(Payment).where(Payment.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        documents = (
            list(
                (
                    await self.db.execute(
                        select(Document).where(Document.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        consultations = (
            list(
                (
                    await self.db.execute(
                        select(Consultation).where(Consultation.case_id.in_(case_ids))
                    )
                )
                .scalars()
                .all()
            )
            if case_ids
            else []
        )
        created_case_users = {case.client_id for case in cases}
        calculated_users = {
            case_to_user[item.case_id]
            for item in calculations
            if item.case_id in case_to_user
        }
        uploaded_users = {
            case_to_user[item.case_id]
            for item in documents
            if item.case_id in case_to_user
        }
        payment_created_users = {
            case_to_user[item.case_id]
            for item in payments
            if item.case_id in case_to_user
        }
        paid_users = {
            case_to_user[item.case_id]
            for item in payments
            if item.case_id in case_to_user and item.status in PAID_STATUSES
        }
        consultation_users = {
            case_to_user[item.case_id]
            for item in consultations
            if item.case_id in case_to_user
        }
        closed_users = {
            case.client_id
            for case in cases
            if case.status in CaseService.CLOSED_STATUSES
        }

        stages = [
            ("visited", "Зашли в бот", visited),
            ("calculator_opened", "Открыли калькулятор", calculator_opened),
            ("case_created", "Создали дело", created_case_users),
            ("calculation_completed", "Сохранили расчет", calculated_users),
            ("documents_uploaded", "Добавили документы", uploaded_users | documents_opened),
            ("payment_started", "Перешли к оплате", payment_created_users | payment_interacted),
            ("paid", "Успешно оплатили", paid_users),
            ("consultation", "Перешли к консультации", consultation_users | consultation_interacted),
            ("case_viewed", "Открывали свое дело", case_opened),
            ("case_closed", "Дело закрыто", closed_users),
        ]
        base = len(visited)
        previous = None
        output = []
        for code, title, users_set in stages:
            count = len(users_set)
            output.append(
                {
                    "code": code,
                    "title": title,
                    "users": count,
                    "conversion_from_visited": round(count / base * 100, 2) if base else 0.0,
                    "conversion_from_previous": (
                        round(count / previous * 100, 2)
                        if previous not in (None, 0)
                        else 0.0
                    ),
                }
            )
            previous = count
        return {
            "period_days": safe_days,
            "since": since.isoformat(),
            "stages": output,
        }
