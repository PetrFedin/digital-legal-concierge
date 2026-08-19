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


async def record_client_activity(event) -> None:
    """Persist Telegram client activity independently from handler transactions.

    Handlers deliberately commit/rollback at domain boundaries. Recording
    activity in that same transaction would either be lost on read-only rollback
    or accidentally commit unfinished business state. This function therefore
    uses a short independent transaction after each Telegram update.
    """

    telegram_user = getattr(event, "from_user", None)
    telegram_id = getattr(telegram_user, "id", None)
    if telegram_id is None:
        return

    now = datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            user_id = (
                await db.execute(
                    select(User.id).where(User.telegram_id == int(telegram_id))
                )
            ).scalar_one_or_none()
            if user_id is None:
                return

            await db.execute(
                update(User)
                .where(User.id == int(user_id))
                .values(last_activity_at=now)
            )

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


__all__ = ["record_client_activity"]
