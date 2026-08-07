from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.keyboards import main_menu
from app.bot.screens import common
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.case import Case
from app.models.consultation import Consultation
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


def telegram_message(telegram_id: int, full_name: str = "Клиент"):
    return SimpleNamespace(
        from_user=SimpleNamespace(
            id=telegram_id,
            username=None,
            full_name=full_name,
        )
    )


class StatusMessage:
    def __init__(self, telegram_id: int):
        self.from_user = SimpleNamespace(
            id=telegram_id,
            username=None,
            full_name="Клиент статуса",
        )
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))


@pytest.mark.asyncio
async def test_closed_case_result_is_primary_home_action(tmp_path):
    async with database(tmp_path, "closed-home-result.db") as factory:
        async with factory() as session:
            user = User(telegram_id=961001, full_name="Клиент закрытого дела")
            session.add(user)
            await session.flush()
            case = Case(
                case_number="M2-HOME-FINAL",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_CLOSED,
                title="Закрытая консультация",
            )
            session.add(case)
            await session.flush()
            session.add(
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.DONE,
                    decision="close",
                    client_description="Нужно определить дальнейшие действия после консультации.",
                    lawyer_result="Риски разъяснены, обращение можно завершить.",
                )
            )
            await session.commit()

            text, case_exists, primary_action = await common._home_text(
                session,
                telegram_message(user.telegram_id),
            )

            assert case_exists is False
            assert primary_action == common.CONSULTATION_RESULT_ACTION
            assert "M2-HOME-FINAL" in text
            assert "Итог последней консультации сохранён" in text
            assert "Юрист завершил консультацию" in text
            assert "DONE" not in text
            assert "decision" not in text.lower()
            assert "close" not in text.lower()


@pytest.mark.asyncio
async def test_active_terminal_consultation_replaces_stale_case_action(tmp_path):
    async with database(tmp_path, "active-home-result.db") as factory:
        async with factory() as session:
            user = User(telegram_id=961002, full_name="Клиент активного дела")
            session.add(user)
            await session.flush()
            case = Case(
                case_number="M2-HOME-ACTIVE",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_CONSULTATION_DONE,
                title="Завершённая консультация",
                next_action="Открыть старую запись на консультацию",
            )
            session.add(case)
            await session.flush()
            session.add(
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.DONE,
                    decision="follow_up",
                    client_description="Нужно проверить ответ и определить следующий шаг по делу.",
                    lawyer_result="Нужна повторная встреча после получения ответа.",
                )
            )
            await session.commit()

            text, case_exists, primary_action = await common._home_text(
                session,
                telegram_message(user.telegram_id),
            )

            assert case_exists is True
            assert primary_action == common.CONSULTATION_RESULT_ACTION
            assert "Для повторной записи будет сохранён предыдущий вопрос" in text
            assert "Открыть старую запись" not in text
            assert "итог консультации" in text.lower()
            assert "follow_up" not in text


def test_no_case_inline_menu_can_surface_contextual_result_first():
    markup = main_menu(
        False,
        primary_action=common.CONSULTATION_RESULT_ACTION,
    )
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]

    assert callbacks == [
        "consultation_result_open",
        "calc_start",
        "contact_lawyer",
    ]
    assert markup.inline_keyboard[0][0].text == "👨‍⚖ Открыть итог консультации"


@pytest.mark.asyncio
async def test_status_without_active_case_keeps_saved_result_reachable(tmp_path):
    async with database(tmp_path, "closed-status-result.db") as factory:
        async with factory() as session:
            user = User(telegram_id=961003, full_name="Клиент статуса")
            session.add(user)
            await session.flush()
            case = Case(
                case_number="M2-STATUS-CLOSED",
                client_id=user.id,
                route="M2",
                status=CaseStatus.M2_CLOSED,
                title="Закрытое дело",
            )
            session.add(case)
            await session.flush()
            session.add(
                Consultation(
                    case_id=case.id,
                    status=ConsultationStatus.DONE,
                    decision="close",
                    client_description="Нужно зафиксировать результат завершённой консультации.",
                    lawyer_result="Консультация завершена, дополнительных действий не требуется.",
                )
            )
            await session.commit()

            message = StatusMessage(user.telegram_id)
            await common.status_command(message, session)

            assert len(message.answers) == 2
            assert "Итог последней консультации сохранён" in message.answers[0][0]
            assert "M2-STATUS-CLOSED" in message.answers[0][0]
            inline = message.answers[1][1]
            callbacks = [
                button.callback_data
                for row in inline.inline_keyboard
                for button in row
            ]
            assert callbacks == ["consultation_result_open", "nav_home"]
