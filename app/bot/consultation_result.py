from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation


TERMINAL_CONSULTATION_STATUSES = frozenset(
    {
        ConsultationStatus.DONE,
        ConsultationStatus.CLIENT_NO_SHOW,
        ConsultationStatus.LAWYER_NO_SHOW,
        ConsultationStatus.CANCELLED,
        ConsultationStatus.CLOSED,
        ConsultationStatus.RESCHEDULED,
    }
)


@dataclass(frozen=True)
class ConsultationResultView:
    title: str
    status_text: str
    next_step: str
    primary_label: str
    primary_callback: str
    show_lawyer_result: bool = False


def normalize_consultation_status(value) -> ConsultationStatus | None:
    try:
        return value if isinstance(value, ConsultationStatus) else ConsultationStatus(str(value))
    except ValueError:
        return None


def is_terminal_consultation(consultation: Consultation | None) -> bool:
    if consultation is None:
        return False
    return normalize_consultation_status(consultation.status) in TERMINAL_CONSULTATION_STATUSES


def clip_client_result(value: str | None, *, limit: int = 2200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def consultation_result_view(consultation: Consultation) -> ConsultationResultView | None:
    status = normalize_consultation_status(consultation.status)
    if status not in TERMINAL_CONSULTATION_STATUSES:
        return None

    if status == ConsultationStatus.DONE:
        decision = str(consultation.decision or "").strip().lower()
        if decision == "close":
            return ConsultationResultView(
                title="✅ Консультация завершена",
                status_text="Юрист завершил консультацию, обращение закрыто.",
                next_step="Дополнительных действий по этому обращению сейчас не требуется.",
                primary_label="🏠 Завершить и на главную",
                primary_callback="nav_home",
                show_lawyer_result=True,
            )
        if decision == "to_m1":
            return ConsultationResultView(
                title="✅ Консультация завершена",
                status_text="По результату консультации вопрос передан в юридическое сопровождение.",
                next_step="Следующий шаг — перейти к документам по делу. Сохранённый результат консультации не потеряется.",
                primary_label="📄 Продолжить сопровождение",
                primary_callback="documents_open",
                show_lawyer_result=True,
            )
        if decision == "follow_up":
            return ConsultationResultView(
                title="✅ Консультация завершена",
                status_text="Юрист рекомендует повторную консультацию.",
                next_step="Для повторной записи будет сохранён предыдущий вопрос; перед выбором времени его можно обновить.",
                primary_label="📅 Записаться повторно",
                primary_callback="consult_follow_up_start",
                show_lawyer_result=True,
            )
        return ConsultationResultView(
            title="✅ Консультация завершена",
            status_text="Результат консультации зафиксирован.",
            next_step="Следующий шаг нужно согласовать с юридической командой по текущему делу.",
            primary_label="✉️ Уточнить следующий шаг",
            primary_callback="message_create",
            show_lawyer_result=True,
        )

    if status == ConsultationStatus.CLIENT_NO_SHOW:
        return ConsultationResultView(
            title="⚠️ Встреча не состоялась",
            status_text="По консультации отмечено, что клиент не подключился к назначенному времени.",
            next_step="Напишите юридической команде, чтобы согласовать перенос или другой вариант продолжения.",
            primary_label="✉️ Написать команде",
            primary_callback="message_create",
        )

    if status == ConsultationStatus.LAWYER_NO_SHOW:
        return ConsultationResultView(
            title="⚠️ Юрист не подключился",
            status_text="Встреча не состоялась по стороне юриста.",
            next_step="Команда должна предложить бесплатный перенос либо возврат. Новую оплату по этой консультации вносить не нужно.",
            primary_label="✉️ Связаться с командой",
            primary_callback="message_create",
        )

    if status == ConsultationStatus.CANCELLED:
        return ConsultationResultView(
            title="Консультация отменена",
            status_text="Подтверждённая запись отменена.",
            next_step="Если консультация была оплачена, возврат обрабатывается отдельно. Статус возврата можно уточнить у команды.",
            primary_label="✉️ Уточнить статус",
            primary_callback="message_create",
        )

    if status == ConsultationStatus.RESCHEDULED:
        return ConsultationResultView(
            title="Консультация перенесена",
            status_text="Предыдущая запись больше не является актуальной.",
            next_step="Откройте текущее дело — там показаны актуальные дата, время и следующий шаг.",
            primary_label="📁 Открыть актуальное дело",
            primary_callback="my_case_open",
        )

    return ConsultationResultView(
        title="Консультация закрыта",
        status_text="Эта запись завершена и больше не требует действий по старому времени.",
        next_step="Возвращайтесь на главную; итог остаётся доступен в разделе «Моё дело».",
        primary_label="🏠 На главную",
        primary_callback="nav_home",
    )


async def latest_case_consultation(
    db: AsyncSession,
    *,
    case_id: int,
) -> Consultation | None:
    return (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id == case_id)
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
        )
    ).scalars().first()


async def latest_terminal_client_consultation(
    db: AsyncSession,
    *,
    client_id: int,
    case_id: int | None = None,
) -> tuple[Case, Consultation] | None:
    terminal_values = [status.value for status in TERMINAL_CONSULTATION_STATUSES]
    statement = (
        select(Case, Consultation)
        .join(Consultation, Consultation.case_id == Case.id)
        .where(Case.client_id == client_id)
        .where(Consultation.status.in_(terminal_values))
    )
    if case_id is not None:
        statement = statement.where(Case.id == case_id)
    row = (
        await db.execute(
            statement.order_by(
                Consultation.created_at.desc(),
                Consultation.id.desc(),
            )
        )
    ).first()
    if not row:
        return None
    return row[0], row[1]


async def prepare_follow_up_consultation(
    db: AsyncSession,
    *,
    case: Case,
    outcome: Consultation,
    client_id: int,
) -> tuple[Consultation, bool]:
    if case.client_id != client_id or outcome.case_id != case.id:
        raise ValueError("Консультация не относится к текущему клиенту")
    if normalize_consultation_status(outcome.status) != ConsultationStatus.DONE:
        raise ValueError("Повторная запись доступна только после завершённой консультации")
    if str(outcome.decision or "").strip().lower() != "follow_up":
        raise ValueError("Повторная запись не указана следующим шагом юриста")

    latest = await latest_case_consultation(db, case_id=case.id)
    if latest and latest.id != outcome.id and not is_terminal_consultation(latest):
        return latest, False

    case_status = (
        case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    )
    if case_status != CaseStatus.M2_CONSULTATION_DONE:
        raise ValueError("Текущий этап дела уже изменился. Откройте актуальное дело.")

    description = str(outcome.client_description or "").strip()
    if len(description) < 20:
        raise ValueError(
            "В предыдущей консультации нет сохранённого вопроса. Напишите команде, чтобы восстановить запись."
        )

    follow_up = Consultation(
        case_id=case.id,
        related_case_id=outcome.related_case_id,
        status=ConsultationStatus.DOCUMENTS_OPTIONAL,
        consultation_type=str(outcome.consultation_type or "online"),
        subject_type=str(outcome.subject_type or "new_or_other"),
        client_description=description,
    )
    db.add(follow_up)
    await db.flush()

    await CaseService(db).change_status(
        case=case,
        next_status=CaseStatus.M2_SLOT_PENDING,
        actor_type="client",
        actor_id=client_id,
        comment="Клиент продолжил рекомендованную повторную консультацию",
    )
    await add_case_history_event(
        db,
        actor_type="client",
        actor_id=client_id,
        case_id=case.id,
        action="CONSULTATION_FOLLOW_UP_STARTED",
        new_value={
            "previous_consultation_id": outcome.id,
            "consultation_id": follow_up.id,
            "description_reused": True,
        },
        comment="Создана повторная консультация по рекомендации юриста",
    )
    await db.flush()
    return follow_up, True
