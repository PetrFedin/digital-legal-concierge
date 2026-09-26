from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.client_case_context import ClientCaseContext
from app.models.user import User

logger = logging.getLogger(__name__)

_TERMINAL_CASE_VALUES = {"M1_CLOSED", "M2_CLOSED", "ARCHIVED"}


def _telegram_id(event) -> int | None:
    direct = getattr(getattr(event, "from_user", None), "id", None)
    if direct is not None:
        return int(direct)
    for attribute in (
        "message",
        "callback_query",
        "edited_message",
        "channel_post",
    ):
        nested = getattr(event, attribute, None)
        nested_id = getattr(getattr(nested, "from_user", None), "id", None)
        if nested_id is not None:
            return int(nested_id)
    return None


def context_free_activity(event, *, state_name: str | None = None) -> bool:
    """True when an update is deliberately outside every saved Case context."""

    message_text = str(getattr(event, "text", "") or "").strip()
    if message_text in {
        "/start",
        "/menu",
        "🏠 Главная",
        "🧮 Рассчитать неустойку",
    }:
        return True

    callback_data = str(getattr(event, "data", "") or "")
    if callback_data == "nav_home":
        return True
    if (
        callback_data == "preview_calc_save"
        or callback_data.startswith("preview_calc_save:v2:")
    ):
        return False
    if callback_data.startswith("preview_"):
        return True

    normalized_state = str(state_name or "")
    return normalized_state.startswith("PreviewCalculatorStates:")


async def record_client_activity(
    event,
    *,
    update_case_activity: bool = True,
) -> None:
    """Persist Telegram client activity independently from handler transactions.

    Handlers deliberately commit/rollback at domain boundaries. Recording
    activity in that same transaction would either be lost on read-only rollback
    or accidentally commit unfinished business state. This function therefore
    uses a short independent transaction after each Telegram update.
    """

    telegram_id = _telegram_id(event)
    if telegram_id is None:
        return

    now = datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    select(User.id).where(User.telegram_id == telegram_id)
                )
            ).scalar_one_or_none()
            if user_id is None:
                return

            await db.execute(
                update(User)
                .where(User.id == int(user_id))
                .values(last_activity_at=now)
            )

            if not update_case_activity:
                await db.commit()
                return

            selected_case_id = (
                await db.execute(
                    select(ClientCaseContext.selected_case_id).where(
                        ClientCaseContext.client_id == int(user_id)
                    )
                )
            ).scalar_one_or_none()
            if selected_case_id is None:
                selected_case_id = (
                    await db.execute(
                        select(Case.id)
                        .where(
                            Case.client_id == int(user_id),
                            Case.status.notin_(_TERMINAL_CASE_VALUES),
                        )
                        .order_by(Case.created_at.desc(), Case.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()

            if selected_case_id is not None:
                await db.execute(
                    update(Case)
                    .where(
                        Case.id == int(selected_case_id),
                        Case.client_id == int(user_id),
                        Case.status.notin_(_TERMINAL_CASE_VALUES),
                    )
                    .values(last_client_action_at=now)
                )
            await db.commit()
    except Exception:
        # Activity tracking must never turn an otherwise successful legal action
        # into a client-visible failure. Scheduler/observability will surface a
        # persistent activity-write problem separately.
        logger.exception("Не удалось сохранить Telegram client activity")


__all__ = ["context_free_activity", "record_client_activity"]
