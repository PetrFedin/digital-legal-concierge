from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.calculator import to_m2, unknown_calc_data
from app.bot.screens.consultations import (
    save_description,
    subject_existing_case,
    subject_new_case,
    subject_start,
)
from app.bot.context import BotContextService
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.user import User


class FakeState:
    def __init__(self, events):
        self.events = events
        self.data = {"stale": "discard-me"}
        self.state = None

    async def clear(self):
        self.events.append("state.clear")
        self.data = {}
        self.state = None

    async def update_data(self, **values):
        self.events.append("state.update")
        self.data.update(values)

    async def set_state(self, value):
        self.events.append("state.set")
        self.state = value

    async def get_data(self):
        return dict(self.data)


class FakeOutputMessage:
    def __init__(self, events):
        self.events = events
        self.texts = []

    async def edit_text(self, text, **kwargs):
        self.events.append("callback.edit_text")
        self.texts.append(text)


class FakeCallback:
    def __init__(self, telegram_user, events, data="calc_to_m2"):
        self.from_user = telegram_user
        self.data = data
        self.message = FakeOutputMessage(events)


class FakeInputMessage:
    def __init__(self, telegram_user, events, text):
        self.from_user = telegram_user
        self.text = text
        self.document = None
        self.photo = None
        self.sticker = None
        self.events = events
        self.answers = []

    async def answer(self, text, **kwargs):
        self.events.append("message.answer")
        self.answers.append(text)


class TrackingSession:
    def __init__(self, session, events):
        self._session = session
        self.events = events

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self):
        self.events.append("db.commit")
        await self._session.commit()

    async def rollback(self):
        self.events.append("db.rollback")
        await self._session.rollback()


@pytest.fixture
async def telegram_db(tmp_path):
    database_path = tmp_path / "telegram-m2-description.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


def telegram_user(telegram_id=880001):
    return SimpleNamespace(
        id=telegram_id,
        username=f"client_{telegram_id}",
        full_name="Клиент Telegram",
    )


async def load_flow_entities(session, telegram_id=880001):
    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one()
    case = (
        await session.execute(
            select(Case)
            .where(Case.client_id == user.id)
            .order_by(Case.created_at.desc())
        )
    ).scalars().first()
    consultation = (
        await session.execute(
            select(Consultation)
            .where(Consultation.case_id == case.id)
            .order_by(Consultation.created_at.desc())
        )
    ).scalars().first()
    return user, case, consultation


async def history_count(session, case_id, action=None):
    query = select(func.count(AuditLog.id)).where(AuditLog.entity_id == case_id)
    if action:
        query = query.where(AuditLog.action == action)
    return (await session.execute(query)).scalar_one()


async def start_flow(session, events, telegram_id=880001):
    state = FakeState(events)
    callback = FakeCallback(telegram_user(telegram_id), events)
    await to_m2(callback, state, TrackingSession(session, events))
    return state, callback


def assert_no_internal_statuses(texts):
    joined = " ".join(texts)
    for status in ConsultationStatus:
        assert status.value not in joined
    for status in CaseStatus:
        assert status.value not in joined


@pytest.mark.asyncio
async def test_selecting_m2_creates_description_flow_and_commits(telegram_db):
    async with telegram_db() as session:
        events = []
        state, callback = await start_flow(session, events)
        user, case, consultation = await load_flow_entities(session)

        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert case.client_id == user.id
        assert case.route == RouteCode.M2.value
        assert case.status == CaseStatus.M2_DESCRIPTION_PENDING.value
        assert case.next_action == "Опишите вопрос для юриста"
        assert state.state == ConsultationDescriptionStates.waiting_description
        assert state.data == {
            "case_id": case.id,
            "consultation_id": consultation.id,
        }
        assert "Опишите вашу ситуацию" in callback.message.texts[-1]
        assert "банковских карт" in callback.message.texts[-1]
        assert events.index("db.commit") < events.index("callback.edit_text")
        assert_no_internal_statuses(callback.message.texts)


@pytest.mark.asyncio
async def test_repeated_m2_selection_is_idempotent(telegram_db):
    async with telegram_db() as session:
        first_events = []
        first_state, _ = await start_flow(session, first_events)
        _, case, first_consultation = await load_flow_entities(session)
        history_after_first = await history_count(session, case.id)

        second_events = []
        second_state, second_callback = await start_flow(session, second_events)
        _, repeated_case, repeated_consultation = await load_flow_entities(session)

        assert repeated_case.id == case.id
        assert repeated_consultation.id == first_consultation.id
        assert first_state.state == second_state.state
        assert await history_count(session, case.id) == history_after_first
        assert "Опишите вашу ситуацию" in second_callback.message.texts[-1]
        assert_no_internal_statuses(second_callback.message.texts)


@pytest.mark.parametrize(
    ("callback_data", "reason"),
    [
        ("calc_unknown_price", "Клиент не знает стоимость"),
        ("calc_unknown_date", "Клиент не знает дату передачи"),
    ],
)
@pytest.mark.asyncio
async def test_unknown_calculator_data_preserves_m2_reason(
    telegram_db, callback_data, reason
):
    async with telegram_db() as session:
        events = []
        state = FakeState(events)
        callback = FakeCallback(
            telegram_user(),
            events,
            data=callback_data,
        )

        await unknown_calc_data(
            callback,
            state,
            TrackingSession(session, events),
        )
        _, case, consultation = await load_flow_entities(session)
        transfer_event = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "CASE_TRANSFERRED_TO_M2",
                )
            )
        ).scalar_one()

        assert case.route == RouteCode.M2.value
        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert transfer_event.new_value["reason"] == reason
        assert transfer_event.comment == reason
        assert state.state == ConsultationDescriptionStates.waiting_description
        assert state.data == {
            "case_id": case.id,
            "consultation_id": consultation.id,
        }
        assert "Опишите вашу ситуацию" in callback.message.texts[-1]


@pytest.mark.asyncio
async def test_unknown_legacy_status_after_commit_has_safe_fallback(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        _, _ = await start_flow(session, setup_events)
        _, case, consultation = await load_flow_entities(session)
        consultation.status = "legacy_unknown"
        await session.commit()
        history_before_repeat = await history_count(session, case.id)

        events = []
        state = FakeState(events)
        callback = FakeCallback(telegram_user(), events)
        await to_m2(callback, state, TrackingSession(session, events))

        assert events.count("db.commit") == 1
        assert "db.rollback" not in events
        assert state.state is None
        assert state.data == {}
        assert "Мое дело" in callback.message.texts[-1]
        assert "legacy_unknown" not in callback.message.texts[-1]
        assert await history_count(session, case.id) == history_before_repeat


@pytest.mark.asyncio
async def test_m1_transfer_has_distinct_nonduplicated_history(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        setup_callback = FakeCallback(telegram_user(), setup_events)
        ctx = BotContextService(session)
        user = await ctx.get_user_from_callback(setup_callback)
        case = await ctx.case_service.create_case(
            client=user,
            route=RouteCode.M1.value,
            status=CaseStatus.M1_DOCUMENTS_PENDING.value,
        )
        await session.commit()

        first_events = []
        first_state = FakeState(first_events)
        first_callback = FakeCallback(telegram_user(), first_events)
        await to_m2(
            first_callback,
            first_state,
            TrackingSession(session, first_events),
        )
        consultation = (
            await session.execute(
                select(Consultation).where(Consultation.case_id == case.id)
            )
        ).scalar_one()

        assert case.route == RouteCode.M2.value
        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert await history_count(session, case.id, "CASE_TRANSFERRED_TO_M2") == 1
        assert await history_count(session, case.id, "M2_CONSULTATION_CREATED") == 1
        history_after_first = await history_count(session, case.id)

        second_events = []
        await to_m2(
            FakeCallback(telegram_user(), second_events),
            FakeState(second_events),
            TrackingSession(session, second_events),
        )

        assert await history_count(session, case.id) == history_after_first


@pytest.mark.asyncio
async def test_documents_optional_repeat_does_not_return_to_description(telegram_db):
    async with telegram_db() as session:
        events = []
        state, _ = await start_flow(session, events)
        user, case, consultation = await load_flow_entities(session)
        await ConsultationService(session).save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description="Описание уже было сохранено клиентом.",
        )
        await session.commit()
        history_before_repeat = await history_count(session, case.id)

        repeat_events = []
        repeated_state, callback = await start_flow(session, repeat_events)

        assert repeated_state.state is None
        assert "Описание уже сохранено" in callback.message.texts[-1]
        assert "Опишите вашу ситуацию" not in callback.message.texts[-1]
        assert await history_count(session, case.id) == history_before_repeat
        assert_no_internal_statuses(callback.message.texts)


@pytest.mark.asyncio
async def test_description_handler_saves_domain_state_then_clears_fsm(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        state, _ = await start_flow(session, setup_events)
        user, case, consultation = await load_flow_entities(session)
        events = []
        state.events = events
        message = FakeInputMessage(
            telegram_user(),
            events,
            "  Застройщик нарушил срок передачи, нужна оценка дальнейших действий.  ",
        )

        await save_description(message, state, TrackingSession(session, events))
        await session.refresh(case)
        await session.refresh(consultation)

        assert consultation.client_description == (
            "Застройщик нарушил срок передачи, нужна оценка дальнейших действий."
        )
        assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert case.status == CaseStatus.M2_DOCUMENTS_OPTIONAL.value
        assert case.next_action == "Загрузите документы или пропустите этот шаг"
        assert state.state is None
        assert state.data == {}
        assert message.answers[-1].startswith("Описание сохранено.")
        assert events.index("db.commit") < events.index("state.clear")
        assert events.index("db.commit") < events.index("message.answer")
        assert await history_count(
            session, case.id, "CONSULTATION_DESCRIPTION_SAVED"
        ) == 1
        assert_no_internal_statuses(message.answers)


@pytest.mark.parametrize("text", [None, "", "   "])
@pytest.mark.asyncio
async def test_non_text_or_empty_description_is_rejected(telegram_db, text):
    async with telegram_db() as session:
        setup_events = []
        state, _ = await start_flow(session, setup_events)
        events = []
        state.events = events
        message = FakeInputMessage(telegram_user(), events, text)

        await save_description(message, state, TrackingSession(session, events))
        _, case, consultation = await load_flow_entities(session)

        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert consultation.client_description is None
        assert state.state == ConsultationDescriptionStates.waiting_description
        assert "текстовым сообщением" in message.answers[-1]
        assert "db.commit" not in events


@pytest.mark.asyncio
async def test_domain_error_keeps_fsm_active_and_rolls_back(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        state, _ = await start_flow(session, setup_events)
        events = []
        state.events = events
        message = FakeInputMessage(telegram_user(), events, "x" * 4001)

        await save_description(message, state, TrackingSession(session, events))

        assert state.state == ConsultationDescriptionStates.waiting_description
        assert "db.rollback" in events
        assert "Проверьте текст" in message.answers[-1]
        assert "x" * 100 not in message.answers[-1]


@pytest.mark.parametrize("tampered_key", ["case_id", "consultation_id"])
@pytest.mark.asyncio
async def test_foreign_fsm_identifiers_cannot_change_consultation(
    telegram_db, tampered_key
):
    async with telegram_db() as session:
        setup_events = []
        state, _ = await start_flow(session, setup_events)
        _, case, consultation = await load_flow_entities(session)
        state.data[tampered_key] += 9999
        events = []
        state.events = events
        message = FakeInputMessage(
            telegram_user(),
            events,
            "Этот текст не должен быть сохранён в чужой объект.",
        )

        await save_description(message, state, TrackingSession(session, events))
        await session.refresh(consultation)
        await session.refresh(case)

        assert consultation.client_description is None
        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert case.client_id == (
            await session.execute(select(User.id).where(User.telegram_id == 880001))
        ).scalar_one()
        assert state.state == ConsultationDescriptionStates.waiting_description
        assert "db.rollback" in events
        assert "Не удалось продолжить" in message.answers[-1]


@pytest.mark.asyncio
async def test_foreign_telegram_user_cannot_change_consultation(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        state, _ = await start_flow(session, setup_events)
        _, case, consultation = await load_flow_entities(session)
        events = []
        state.events = events
        message = FakeInputMessage(
            telegram_user(telegram_id=990002),
            events,
            "Этот текст отправил другой Telegram-пользователь.",
        )

        await save_description(message, state, TrackingSession(session, events))
        await session.refresh(consultation)
        await session.refresh(case)

        assert consultation.client_description is None
        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert state.state == ConsultationDescriptionStates.waiting_description
        assert "db.rollback" in events
        assert "Не удалось продолжить" in message.answers[-1]


@pytest.mark.asyncio
async def test_replayed_same_text_does_not_duplicate_history(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        state, _ = await start_flow(session, setup_events)
        _, case, consultation = await load_flow_entities(session)
        text = "Повторно доставленное описание той же консультации."
        first_message = FakeInputMessage(telegram_user(), [], text)
        await save_description(first_message, state, TrackingSession(session, []))
        history_after_first = await history_count(
            session, case.id, "CONSULTATION_DESCRIPTION_SAVED"
        )

        replay_events = []
        replay_state = FakeState(replay_events)
        replay_state.data = {
            "case_id": case.id,
            "consultation_id": consultation.id,
        }
        replay_state.state = ConsultationDescriptionStates.waiting_description
        replay_message = FakeInputMessage(telegram_user(), replay_events, text)
        await save_description(
            replay_message,
            replay_state,
            TrackingSession(session, replay_events),
        )

        assert await history_count(
            session, case.id, "CONSULTATION_DESCRIPTION_SAVED"
        ) == history_after_first == 1
        assert replay_state.state is None
        assert "Описание сохранено" in replay_message.answers[-1]


@pytest.mark.asyncio
async def test_existing_booked_subject_flow_keeps_legacy_contract(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        _, _ = await start_flow(session, setup_events)
        _, _, consultation = await load_flow_entities(session)
        consultation.status = ConsultationStatus.BOOKED.value
        await session.commit()

        events = []
        state = FakeState(events)
        callback = FakeCallback(
            telegram_user(),
            events,
            data="consult_subject_start",
        )
        await subject_start(callback, session, state)
        await subject_new_case(callback, state)
        message = FakeInputMessage(
            telegram_user(),
            events,
            "Вопрос к уже оформленной консультации.",
        )
        await save_description(message, state, TrackingSession(session, events))
        await session.refresh(consultation)

        assert consultation.status == ConsultationStatus.BOOKED.value
        assert consultation.client_description == (
            "Вопрос к уже оформленной консультации."
        )
        assert state.state is None
        assert "Описание сохранено" in message.answers[-1]


@pytest.mark.asyncio
async def test_existing_case_selection_keeps_legacy_subject_data(telegram_db):
    async with telegram_db() as session:
        setup_events = []
        _, _ = await start_flow(session, setup_events)
        _, case, consultation = await load_flow_entities(session)
        consultation.status = ConsultationStatus.BOOKED.value
        original_case_status = case.status
        original_route = case.route
        await session.commit()

        events = []
        state = FakeState(events)
        callback = FakeCallback(
            telegram_user(),
            events,
            data="consult_subject_start",
        )
        await subject_start(callback, session, state)
        callback.data = f"consult_subject_case:{case.id}"
        await subject_existing_case(callback, state)
        message = FakeInputMessage(
            telegram_user(),
            events,
            "Вопрос по выбранному существующему делу.",
        )
        await save_description(message, state, TrackingSession(session, events))
        await session.refresh(consultation)
        await session.refresh(case)

        assert consultation.status == ConsultationStatus.BOOKED.value
        assert consultation.subject_type == "existing_case"
        assert consultation.related_case_id == case.id
        assert case.status == original_case_status
        assert case.route == original_route
        assert await history_count(
            session, case.id, "CONSULTATION_DESCRIPTION_SAVED"
        ) == 1


@pytest.mark.parametrize(
    "terminal_status",
    [ConsultationStatus.CANCELLED, ConsultationStatus.DONE],
)
@pytest.mark.asyncio
async def test_legacy_subject_flow_ignores_non_booked_consultation(
    telegram_db, terminal_status
):
    async with telegram_db() as session:
        setup_events = []
        _, _ = await start_flow(session, setup_events)
        _, _, consultation = await load_flow_entities(session)
        consultation.status = terminal_status.value
        await session.commit()

        events = []
        state = FakeState(events)
        callback = FakeCallback(
            telegram_user(),
            events,
            data="consult_subject_start",
        )
        await subject_start(callback, session, state)

        assert state.state is None
        assert state.data == {"stale": "discard-me"}
        assert "Активная консультация не найдена" in callback.message.texts[-1]
        assert_no_internal_statuses(callback.message.texts)
