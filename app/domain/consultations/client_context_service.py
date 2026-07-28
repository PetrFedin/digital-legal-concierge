from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.statuses.case_statuses import CLOSED_CASE_STATUSES, RouteCode
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.user import User


class ClientConsultationContextError(LookupError):
    """The requested client consultation context is missing or ambiguous."""


class ClientConsultationContextService:
    """Resolve owned M2 context without depending on a generic active case."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_user(self, *, telegram_id: int) -> User:
        user = (
            await self.db.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
        ).scalar_one_or_none()
        if user is None or user.is_blocked:
            raise ClientConsultationContextError(
                "Клиент недоступен для оформления консультации."
            )
        return user

    async def load_active_m2_case(self, *, client_id: int) -> Case:
        cases = list(
            (
                await self.db.execute(
                    select(Case)
                    .where(
                        Case.client_id == client_id,
                        Case.route == RouteCode.M2.value,
                        Case.status.notin_(CLOSED_CASE_STATUSES),
                    )
                    .order_by(Case.updated_at.desc(), Case.id.desc())
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if not cases:
            raise ClientConsultationContextError(
                "Активное консультационное дело не найдено."
            )
        if len(cases) > 1:
            raise ClientConsultationContextError(
                "Найдено несколько активных консультационных дел."
            )
        return cases[0]

    async def load_single_consultation(
        self,
        *,
        case_id: int,
        statuses: set[str] | frozenset[str],
    ) -> Consultation:
        consultations = list(
            (
                await self.db.execute(
                    select(Consultation)
                    .where(
                        Consultation.case_id == case_id,
                        Consultation.status.in_(statuses),
                    )
                    .order_by(
                        Consultation.created_at.desc(),
                        Consultation.id.desc(),
                    )
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if not consultations:
            raise ClientConsultationContextError(
                "Активная консультация не найдена."
            )
        if len(consultations) > 1:
            raise ClientConsultationContextError(
                "Найдено несколько активных консультаций."
            )
        return consultations[0]
