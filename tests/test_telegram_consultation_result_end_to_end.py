from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import bot
from app.bot.consultation_result import (
    consultation_result_view,
    latest_terminal_client_consultation,
    prepare_follow_up_consultation,
)
from app.bot.screens import consultation_results, my_case
from app.domain.consultations.consultation_intake import ConsultationIntakeService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User


@asynccontextmanager
async def database(tmp_path, name: str):
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


def test_terminal_result_actions_are_human_and_decision_specific():
    close = Consultation(
        case_id=1,
        status=ConsultationStatus.DONE,
        decision="close",
        lawyer_result="Риски разъяснены, дополнительных действий по обращению не требуется.",
    )
    to_m1 = Consultation(
        case_id=1,
        status=ConsultationStatus.DONE,
        decision="to_m1",
        lawyer_result="Рекомендуется перейти к полноценному сопровождению дела.",
    )
    follow_up = Consultation(
        case_id=1,
        status=ConsultationStatus.DONE,
        decision="follow_up",
        lawyer_result="Нужна повторная встреча после получения ответа застройщика.",
    )
    other = Consultation(
        case_id=1,
        status=ConsultationStatus.DONE,
        decision="other",
        lawyer_result="Следующий шаг нужно согласовать после проверки новых обстоятельств.",
    )

    close_view = consultation_result_view(close)
    m1_view = consultation_result_view(to_m1)
    follow_view = consultation_result_view(follow_up)
    other_view = consultation_result_view(other)

    assert close_view is not None
    assert close_view.primary_callback == "nav_home"
    assert close_view.show_lawyer_result is True
    assert m1_view is not None
    assert m1_view.primary_callback == "documents_open"
    assert follow_view is not None
    assert follow_view.primary_callback == "consult_follow_up_start"
    assert other_view is not None
    assert other_view.primary_callback == "message_create"
    assert "to_m1" not in m1_view.status_text
    assert "follow_up" not in follow_view.status_text


def test_no_show_result_does_not_expose_internal_lawyer_comment():
    client_no_show = Consultation(
        case_id=1,
        status=ConsultationStatus.CLIENT_NO_SHOW,
        lawyer_result="Внутренняя служебная заметка юриста о попытках дозвониться.",
    )
    lawyer_no_show = Consultation(
        case_id=1,
        status=ConsultationStatus.LAWYER_NO_SHOW,
        lawyer_result="Внутренняя служебная причина отсутствия юриста.",
    )

    client_view = consultation_result_view(client_no_show)
    lawyer_view = consultation_result_view(lawyer_no_show)

    assert client_view is not None
    assert client_view.show_lawyer_result is False
    assert client_view.primary_callback == "message_create"
    assert lawyer_view is not None
    assert lawyer_view.show_lawyer_result is False
    assert lawyer_view.primary_callback == "message_create"
    assert "Новую оплату" in lawyer_view.next_step
    assert "возврат" in lawyer_view.next_step.lower()


def test_my_case_detects_terminal_consultation_even_with_scheduled_time():
    view = SimpleNamespace(
        unread_team_messages=0,
        consultation_summary="Консультация проведена · 07.08.2026 в 12:00 UTC",
        action=None,
    )

    buttons = my_case._case_buttons(view)

    assert ("👨‍⚖ Итог консультации", "consultation_result_open") in buttons
    assert ("🔄 Обновить статус", "my_case_open") not in buttons


def test_closed_result_has_no_stale_message_or_case_action():
    consultation = Consultation(
        case_id=1,
        status=ConsultationStatus.DONE,
        decision="close",
        lawyer_result="Обращение завершено.",
    )
    view = consultation_result_view(consultation)
    case = Case(
        case_number="M2-CLOSED-BUTTONS",
        client_id=1,
        route="M2",
        status=CaseStatus.M2_CLOSED,
        title="Закрытое дело",
    )

    assert view is not None
    buttons = consultation_results._result_buttons(view, case=case)
    assert buttons == [("🏠 На главную", "nav_home")]


@pytest.mark.asyncio
async def test_closed_case_keeps_completed_consultation_result_available(tmp_path):
    async with database(tmp_path, "closed-result.db") as factory:
        async with factory() as session:
            owner = User(telegram_id=940001, full_name="Клиент с закрытым делом")
            stranger = User(telegram_id=940002, full_name="Другой клиент")
            session.add_all([owner, stranger])
            await session.flush()

            case = Case(
                case_number="M2-CLOSED-RESULT",
                client_id=owner.id,
                route="M2",
                status=CaseStatus.M2_CLOSED,
                title="Закрытая консультация",
            )
            foreign_case = Case(
                case_number="M2-FOREIGN-RESULT",
                client_id=stranger.id,
                route="M2",
                status=CaseStatus.M2_CLOSED,
                title="Чужая консультация",
            )
            session.add_all([case, foreign_case])
            await session.flush()

            result = Consultation(
                case_id=case.id,
                status=ConsultationStatus.DONE,
                decision="close",
                client_description="Нужно определить дальнейшие действия после проверки документов.",
                lawyer_result="Юрист разъяснил риски, сроки и подтвердил завершение обращения.",
            )
            foreign_result = Consultation(
                case_id=foreign_case.id,
                status=ConsultationStatus.DONE,
                decision="close",
                client_description="Чужой вопрос, который не должен попадать другому клиенту.",
                lawyer_result="Чужой результат консультации.",
            )
            session.add_all([result, foreign_result])
            await session.commit()

            latest = await latest_terminal_client_consultation(
                session,
                client_id=owner.id,
            )

            assert latest is not None
            latest_case, latest_consultation = latest
            assert latest_case.id == case.id
            assert latest_consultation.id == result.id
            assert latest_consultation.lawyer_result.startswith("Юрист разъяснил")


@pytest.mark.asyncio
async def test_follow_up_reuses_question_and_reaches_real_booking(tmp_path):
    async with database(tmp_path, "follow-up-e2e.db") as factory:
        async with factory() as session:
            user = User(telegram_id=940003, full_name="Клиент повторной консультации")
            lawyer = Lawyer(full_name="Юрист повторной встречи", is_active=True, workload_limit=10)
            session.add_all([user, lawyer])
            await session.flush()

            case = Case(
                case_number="M2-FOLLOW-UP",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_CONSULTATION_DONE,
                title="Повторная консультация",
                next_action="Назначить следующую консультацию",
            )
            session.add(case)
            await session.flush()

            question = (
                "Нужно проверить ответ застройщика и определить дальнейшие действия по договору."
            )
            outcome = Consultation(
                case_id=case.id,
                status=ConsultationStatus.DONE,
                decision="follow_up",
                client_description=question,
                lawyer_result="Нужна повторная встреча после получения ответа застройщика.",
            )
            session.add(outcome)
            starts_at = datetime.now(timezone.utc) + timedelta(days=2)
            slot = ConsultationSlot(
                lawyer_id=lawyer.id,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=1),
                status="available",
            )
            session.add(slot)
            await session.flush()

            follow_up, created = await prepare_follow_up_consultation(
                session,
                case=case,
                outcome=outcome,
                client_id=user.id,
            )
            same_follow_up, created_again = await prepare_follow_up_consultation(
                session,
                case=case,
                outcome=outcome,
                client_id=user.id,
            )

            assert created is True
            assert created_again is False
            assert same_follow_up.id == follow_up.id
            assert follow_up.status == ConsultationStatus.DOCUMENTS_OPTIONAL
            assert follow_up.client_description == question
            assert case.status == CaseStatus.M2_SLOT_PENDING

            nonterminal = (
                await session.execute(
                    select(Consultation).where(
                        Consultation.case_id == case.id,
                        Consultation.status.notin_(
                            [
                                ConsultationStatus.DONE,
                                ConsultationStatus.CLIENT_NO_SHOW,
                                ConsultationStatus.LAWYER_NO_SHOW,
                                ConsultationStatus.CANCELLED,
                                ConsultationStatus.CLOSED,
                                ConsultationStatus.RESCHEDULED,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            assert [item.id for item in nonterminal] == [follow_up.id]

            intake = ConsultationIntakeService(session)
            prepared_case, prepared = await intake.prepare_slot_selection(client=user)
            assert prepared_case.id == case.id
            assert prepared.id == follow_up.id

            case, reserved, reserved_slot = await intake.reserve_slot(
                client=user,
                slot_id=slot.id,
                payment_required=False,
            )
            booked, booked_slot = await intake.confirm_without_payment(
                client=user,
                case=case,
                consultation=reserved,
            )
            await session.commit()

            assert booked.id == follow_up.id
            assert booked.status == ConsultationStatus.BOOKED
            assert booked_slot.id == reserved_slot.id == slot.id
            assert booked_slot.status == "booked"
            assert case.status == CaseStatus.M2_CONSULTATION_BOOKED


def test_result_router_precedes_booking_router_and_has_recovery_actions():
    dispatcher_source = inspect.getsource(bot.build_dispatcher)
    result_source = inspect.getsource(consultation_results)
    case_source = inspect.getsource(my_case)
    compact = "".join(dispatcher_source.split())

    assert compact.index("consultation_results.router") < compact.index(
        "consultation_intake.router"
    )
    assert "TerminalBookedOpenFilter" in result_source
    assert "TerminalContactLawyerFilter" in result_source
    assert "consultation_result_open" in result_source
    assert "consult_follow_up_start" in result_source
    assert "Что дальше:" in result_source
    assert "message_create" in result_source
    assert "message_history" in result_source
    assert "my_case_open" in result_source
    assert "nav_home" in result_source
    assert "Итог консультации" in case_source
    assert "Итог последней консультации сохранён" in case_source
