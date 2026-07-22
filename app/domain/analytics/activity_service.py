from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics_event import AnalyticsEvent


SESSION_TIMEOUT = timedelta(minutes=30)

BOT_SESSION_STARTED = "BOT_SESSION_STARTED"
BOT_SCREEN_VIEW = "BOT_SCREEN_VIEW"
BOT_COMMAND = "BOT_COMMAND"
BOT_NAVIGATION = "BOT_NAVIGATION"
BOT_ACTION = "BOT_ACTION"
BOT_PAYMENT_ACTION = "BOT_PAYMENT_ACTION"
BOT_DOCUMENT_ACTION = "BOT_DOCUMENT_ACTION"
BOT_FREE_TEXT = "BOT_FREE_TEXT"

CRM_CLIENT_ARCHIVED = "CRM_CLIENT_ARCHIVED"
CRM_CLIENT_RESTORED = "CRM_CLIENT_RESTORED"

VIEW_EVENTS = frozenset({BOT_SCREEN_VIEW})
SESSION_EVENTS = frozenset({BOT_SESSION_STARTED})
ACTION_EVENTS = frozenset(
    {
        BOT_COMMAND,
        BOT_NAVIGATION,
        BOT_ACTION,
        BOT_PAYMENT_ACTION,
        BOT_DOCUMENT_ACTION,
        BOT_FREE_TEXT,
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def safe_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep analytics payload compact and JSON-safe.

    Raw free-form legal messages must not be stored in analytics. Callers pass only
    classified metadata. This function additionally bounds strings and collections
    so one Telegram update cannot create an oversized analytics row.
    """

    if not payload:
        return None

    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        key = str(key)[:100]
        if value is None or isinstance(value, (bool, int, float)):
            cleaned[key] = value
        elif isinstance(value, str):
            cleaned[key] = value[:500]
        elif isinstance(value, (list, tuple, set)):
            cleaned[key] = [
                item if isinstance(item, (bool, int, float)) or item is None else str(item)[:200]
                for item in list(value)[:25]
            ]
        elif isinstance(value, dict):
            cleaned[key] = {
                str(inner_key)[:100]: (
                    inner_value
                    if isinstance(inner_value, (bool, int, float)) or inner_value is None
                    else str(inner_value)[:200]
                )
                for inner_key, inner_value in list(value.items())[:25]
            }
        else:
            cleaned[key] = str(value)[:500]
    return cleaned


class ActivityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        event_name: str,
        user_id: int | None = None,
        case_id: int | None = None,
        route: str | None = None,
        source: str = "telegram_bot",
        payload: dict[str, Any] | None = None,
    ) -> AnalyticsEvent:
        event = AnalyticsEvent(
            event_name=str(event_name)[:100],
            user_id=user_id,
            case_id=case_id,
            route=str(route)[:10] if route else None,
            source=str(source)[:100],
            payload=safe_payload(payload),
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def last_bot_activity_at(self, user_id: int) -> datetime | None:
        value = (
            await self.db.execute(
                select(AnalyticsEvent.created_at)
                .where(AnalyticsEvent.user_id == user_id)
                .where(AnalyticsEvent.source == "telegram_bot")
                .order_by(AnalyticsEvent.created_at.desc(), AnalyticsEvent.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return normalize_datetime(value)

    async def record_bot_interaction(
        self,
        *,
        event_name: str,
        user_id: int,
        case_id: int | None = None,
        route: str | None = None,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> tuple[AnalyticsEvent | None, AnalyticsEvent]:
        now = normalize_datetime(occurred_at) or utcnow()
        last_activity = await self.last_bot_activity_at(user_id)
        session_event: AnalyticsEvent | None = None

        if last_activity is None or now - last_activity >= SESSION_TIMEOUT:
            session_event = await self.record(
                event_name=BOT_SESSION_STARTED,
                user_id=user_id,
                case_id=case_id,
                route=route,
                source="telegram_bot",
                payload={
                    "session_timeout_minutes": int(SESSION_TIMEOUT.total_seconds() / 60),
                    "entry_event": event_name,
                    "screen": (payload or {}).get("screen"),
                },
            )

        interaction = await self.record(
            event_name=event_name,
            user_id=user_id,
            case_id=case_id,
            route=route,
            source="telegram_bot",
            payload=payload,
        )
        return session_event, interaction


async def load_client_archive_states(
    db: AsyncSession,
    user_ids: list[int] | tuple[int, ...] | set[int],
) -> dict[int, bool]:
    ids = [int(user_id) for user_id in user_ids]
    if not ids:
        return {}

    events = list(
        (
            await db.execute(
                select(AnalyticsEvent)
                .where(AnalyticsEvent.user_id.in_(ids))
                .where(
                    AnalyticsEvent.event_name.in_(
                        (CRM_CLIENT_ARCHIVED, CRM_CLIENT_RESTORED)
                    )
                )
                .order_by(
                    AnalyticsEvent.user_id.asc(),
                    AnalyticsEvent.created_at.desc(),
                    AnalyticsEvent.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )

    state: dict[int, bool] = {}
    for event in events:
        if event.user_id is None or event.user_id in state:
            continue
        state[event.user_id] = event.event_name == CRM_CLIENT_ARCHIVED
    return state
