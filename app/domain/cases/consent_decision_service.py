from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.consent_contract import resolve_consent_contract
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.consent_acceptance import ConsentAcceptance
from app.models.user import User


CONSENT_ACCEPT = "accept"
CONSENT_DECLINE = "decline"


class ConsentDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class ConsentDecisionResult:
    case: Case
    decision: str
    outcome: str
    changed: bool
    evidence_id: int | None = None


class ConsentDecisionService:
    """Apply an exact-text consent decision under a Case row lock.

    The callback carries a short contract token. It is resolved to an immutable
    legal text snapshot/version/hash; the service never silently upgrades an
    old button to whatever consent text happens to be current at click time.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    async def _lock_case(self, *, client_id: int, case_id: int) -> Case | None:
        return (
            await self.db.execute(
                select(Case)
                .where(
                    Case.id == int(case_id),
                    Case.client_id == int(client_id),
                )
                .with_for_update()
            )
        ).scalars().first()

    @staticmethod
    def _status(case: Case) -> CaseStatus:
        return (
            case.status
            if isinstance(case.status, CaseStatus)
            else CaseStatus(str(case.status))
        )

    async def _existing_evidence(
        self,
        *,
        client_id: int,
        source_callback_id: str,
    ) -> ConsentAcceptance | None:
        return (
            await self.db.execute(
                select(ConsentAcceptance).where(
                    ConsentAcceptance.user_id == int(client_id),
                    ConsentAcceptance.source_callback_id == source_callback_id,
                )
            )
        ).scalar_one_or_none()

    async def apply(
        self,
        *,
        client_id: int,
        case_id: int,
        decision: str,
        consent_callback_token: str,
        telegram_user_id: int,
        source_chat_id: int | None,
        source_message_id: int | None,
        source_callback_id: str,
    ) -> ConsentDecisionResult:
        normalized = str(decision or "").strip().lower()
        if normalized not in {CONSENT_ACCEPT, CONSENT_DECLINE}:
            raise ConsentDecisionError("Неизвестное решение по согласию")
        callback_id = str(source_callback_id or "").strip()
        if not callback_id:
            raise ConsentDecisionError("Не удалось зафиксировать источник решения")

        contract = resolve_consent_contract(consent_callback_token)
        if contract is None:
            raise ConsentDecisionError(
                "Версия текста согласия из этой кнопки больше не распознаётся. Откройте согласие заново."
            )

        existing = await self._existing_evidence(
            client_id=int(client_id),
            source_callback_id=callback_id,
        )
        if existing is not None:
            expected_status = "accepted" if normalized == CONSENT_ACCEPT else "declined"
            if (
                int(existing.case_id) != int(case_id)
                or str(existing.consent_status) != expected_status
                or str(existing.consent_version) != str(contract["consent_version"])
                or str(existing.text_sha256) != str(contract["text_sha256"])
            ):
                raise ConsentDecisionError(
                    "Источник решения уже использован для другого согласия"
                )
            case = await self._lock_case(client_id=client_id, case_id=case_id)
            if case is None:
                raise LookupError("Дело из этого экрана не найдено или недоступно")
            return ConsentDecisionResult(
                case,
                normalized,
                expected_status,
                False,
                int(existing.id),
            )

        client = (
            await self.db.execute(select(User).where(User.id == int(client_id)))
        ).scalar_one_or_none()
        if client is None:
            raise LookupError("Клиент не найден")
        if int(client.telegram_id) != int(telegram_user_id):
            raise ConsentDecisionError(
                "Telegram-пользователь не соответствует владельцу обращения"
            )

        case = await self._lock_case(client_id=client_id, case_id=case_id)
        if case is None:
            raise LookupError("Дело из этого экрана не найдено или недоступно")

        status = self._status(case)
        if status.value.startswith("M1_"):
            return ConsentDecisionResult(case, normalized, "stale_m1", False)
        if status.value.startswith("M2_"):
            return ConsentDecisionResult(case, normalized, "stale_m2", False)
        if status == CaseStatus.CALCULATED:
            return ConsentDecisionResult(
                case,
                normalized,
                "route_not_selected" if normalized == CONSENT_ACCEPT else "declined",
                False,
            )
        if status != CaseStatus.CLIENT_DECISION:
            return ConsentDecisionResult(case, normalized, "stale_other", False)

        if normalized == CONSENT_ACCEPT:
            await self.cases.transfer_to_m1(
                case=case,
                actor_type="client",
                actor_id=int(client_id),
                comment=(
                    "Клиент отдельно и явно подтвердил согласие на обработку персональных данных "
                    f"для M1; версия {contract['consent_version']}; SHA-256 {contract['text_sha256']}"
                ),
            )
            outcome = "accepted"
        else:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.CALCULATED,
                actor_type="client",
                actor_id=int(client_id),
                comment=(
                    "Клиент явно отказался от согласия для M1; "
                    f"версия {contract['consent_version']}; SHA-256 {contract['text_sha256']}; "
                    "предварительный расчёт сохранён"
                ),
            )
            outcome = "declined"

        decided_at = datetime.now(timezone.utc)
        evidence = ConsentAcceptance(
            case_id=int(case.id),
            user_id=int(client_id),
            consent_type=str(contract["consent_type"]),
            consent_status=outcome,
            consent_version=str(contract["consent_version"]),
            text_sha256=str(contract["text_sha256"]),
            text_snapshot=str(contract["text"]),
            consent_date=decided_at,
            telegram_user_id=int(telegram_user_id),
            source_chat_id=(int(source_chat_id) if source_chat_id is not None else None),
            source_message_id=(
                int(source_message_id) if source_message_id is not None else None
            ),
            source_callback_id=callback_id,
        )
        self.db.add(evidence)
        await self.db.flush()

        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=int(client_id),
            case_id=int(case.id),
            action="PERSONAL_DATA_CONSENT_RECORDED",
            new_value={
                "consent_evidence_id": int(evidence.id),
                "consent_type": evidence.consent_type,
                "consent_status": evidence.consent_status,
                "consent_version": evidence.consent_version,
                "text_sha256": evidence.text_sha256,
                "consent_date": evidence.consent_date.isoformat(),
                "telegram_user_id": int(evidence.telegram_user_id),
                "source_chat_id": evidence.source_chat_id,
                "source_message_id": evidence.source_message_id,
                "source_callback_id": evidence.source_callback_id,
            },
        )
        await self.db.flush()
        return ConsentDecisionResult(
            case,
            normalized,
            outcome,
            True,
            int(evidence.id),
        )


__all__ = [
    "CONSENT_ACCEPT",
    "CONSENT_DECLINE",
    "ConsentDecisionError",
    "ConsentDecisionResult",
    "ConsentDecisionService",
]
