from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.context import BotContextService
from app.bot.screens.consultations import save_description as save_m2_description
from app.bot.screens.documents import (
    docs,
    finish,
    finish_m2_documents,
    open_m2_documents,
    skip_m2_documents,
    upload,
)
from app.bot.states import ConsultationDescriptionStates, DocumentUploadStates
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.documents.document_service import DocumentService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.consultation import Consultation
from app.models.document import Document
from app.storage import StoredFile


class FakeState:
    def __init__(self, events):
        self.events = events
        self.data = {}
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
        self.markups = []

    async def edit_text(self, text, **kwargs):
        self.events.append("callback.edit_text")
        self.texts.append(text)
        self.markups.append(kwargs.get("reply_markup"))


class FakeCallback:
    def __init__(self, telegram_user, events, data):
        self.from_user = telegram_user
        self.data = data
        self.message = FakeOutputMessage(events)


class FakeInputMessage:
    def __init__(
        self,
        telegram_user,
        events,
        *,
        text=None,
        document=None,
        photo=None,
    ):
        self.from_user = telegram_user
        self.text = text
        self.document = document
        self.photo = photo
        self.sticker = None
        self.voice = None
        self.video_note = None
        self.location = None
        self.contact = None
        self.bot = object()
        self.events = events
        self.answers = []
        self.markups = []

    async def answer(self, text, **kwargs):
        self.events.append("message.answer")
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))


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
    database_path = tmp_path / "telegram-m2-documents.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.fixture
def storage_stub(monkeypatch, tmp_path):
    calls = []

    class StubStorage:
        async def save_telegram_file(
            self,
            *,
            bot,
            telegram_file_id,
            case_id,
            original_name,
            mime_type,
            file_size,
        ):
            target = tmp_path / f"stored_{len(calls)}_{original_name}"
            target.write_bytes(f"content:{telegram_file_id}".encode())
            calls.append(
                {
                    "file_id": telegram_file_id,
                    "case_id": case_id,
                    "path": target,
                }
            )
            return StoredFile(
                original_name=original_name,
                storage_path=str(target),
                mime_type=mime_type,
                file_size=file_size,
            )

    monkeypatch.setattr(
        "app.bot.screens.documents.LocalStorageService",
        StubStorage,
    )
    return calls


def telegram_user(telegram_id=730001):
    return SimpleNamespace(
        id=telegram_id,
        username=f"documents_{telegram_id}",
        full_name="Клиент с документами",
    )


def telegram_document(file_id="telegram-file-1", name="evidence.pdf"):
    return SimpleNamespace(
        file_id=file_id,
        file_unique_id=f"unique-{file_id}" if file_id else None,
        file_name=name,
        mime_type="application/pdf",
        file_size=2048,
    )


def callback_names(markup):
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    }


def assert_no_machine_status(texts):
    joined = " ".join(texts)
    for status in ConsultationStatus:
        assert status.value not in joined
    for status in CaseStatus:
        assert status.value not in joined


async def prepare_m2(session, *, description_saved=True):
    events = []
    callback = FakeCallback(telegram_user(), events, "setup")
    ctx = BotContextService(session)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.create_case(
        client=user,
        route=RouteCode.M2.value,
        status=CaseStatus.M2_DESCRIPTION_PENDING.value,
    )
    consultation = await ConsultationService(session).create_or_get_m2_consultation(
        case=case,
        actor_type="client",
        actor_id=user.id,
        source="telegram",
    )
    if description_saved:
        await ConsultationService(session).save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description="Описание для перехода к необязательным документам.",
        )
    await session.commit()
    return user, case, consultation


async def open_document_state(session, events):
    state = FakeState(events)
    callback = FakeCallback(telegram_user(), events, "m2_documents_open")
    await open_m2_documents(callback, state, TrackingSession(session, events))
    return state, callback


async def document_count(session, case_id):
    return (
        await session.execute(
            select(func.count(Document.id)).where(Document.case_id == case_id)
        )
    ).scalar_one()


async def history_count(session, case_id, action):
    return (
        await session.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.entity_id == case_id,
                AuditLog.action == action,
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_description_success_shows_working_document_actions(telegram_db):
    async with telegram_db() as session:
        user, case, consultation = await prepare_m2(
            session,
            description_saved=False,
        )
        events = []
        state = FakeState(events)
        state.data = {
            "case_id": case.id,
            "consultation_id": consultation.id,
        }
        state.state = ConsultationDescriptionStates.waiting_description
        message = FakeInputMessage(
            telegram_user(),
            events,
            text="Подробное описание ситуации для консультации.",
        )

        await save_m2_description(
            message,
            state,
            TrackingSession(session, events),
        )

        assert callback_names(message.markups[-1]) == {
            "m2_documents_open",
            "m2_documents_skip",
            "nav_home",
        }
        assert events.index("db.commit") < events.index("message.answer")
        assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert case.client_id == user.id


@pytest.mark.asyncio
async def test_open_documents_verifies_entities_and_sets_m2_fsm(telegram_db):
    async with telegram_db() as session:
        user, case, consultation = await prepare_m2(session)
        events = []
        state, callback = await open_document_state(session, events)

        assert case.client_id == user.id
        assert consultation.case_id == case.id
        assert state.state == DocumentUploadStates.waiting_file
        assert state.data == {
            "case_id": case.id,
            "consultation_id": consultation.id,
            "m2_documents_flow": True,
            "document_type": "OTHER",
            "processed_file_keys": [],
        }
        assert "несколько файлов" in callback.message.texts[-1]
        assert "секретные сведения" in callback.message.texts[-1]
        assert_no_machine_status(callback.message.texts)


@pytest.mark.asyncio
async def test_documents_open_routes_m2_to_strict_flow(telegram_db):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        events = []
        state = FakeState(events)
        callback = FakeCallback(telegram_user(), events, "documents_open")

        await docs(callback, TrackingSession(session, events), state)

        assert state.data["case_id"] == case.id
        assert state.data["consultation_id"] == consultation.id
        assert "doc_finish_upload" not in callback_names(callback.message.markups[-1])


@pytest.mark.asyncio
async def test_open_documents_before_description_does_not_activate_fsm(telegram_db):
    async with telegram_db() as session:
        _, _, consultation = await prepare_m2(session, description_saved=False)
        events = []
        state = FakeState(events)
        state.data = {"description": "still-active"}
        state.state = ConsultationDescriptionStates.waiting_description
        callback = FakeCallback(telegram_user(), events, "m2_documents_open")

        await open_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )

        assert consultation.status == ConsultationStatus.DESCRIPTION_PENDING.value
        assert state.state == ConsultationDescriptionStates.waiting_description
        assert state.data == {"description": "still-active"}
        assert "Сначала сохраните описание" in callback.message.texts[-1]
        assert "db.commit" not in events
        assert_no_machine_status(callback.message.texts)


@pytest.mark.asyncio
async def test_open_documents_after_completion_does_not_reactivate_fsm(telegram_db):
    async with telegram_db() as session:
        user, case, consultation = await prepare_m2(session)
        await ConsultationService(session).complete_documents_step(
            consultation=consultation,
            case=case,
            documents_uploaded=False,
            actor_id=user.id,
        )
        await session.commit()
        events = []
        state = FakeState(events)
        callback = FakeCallback(telegram_user(), events, "m2_documents_open")

        await open_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )

        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert state.state is None
        assert "уже завершён" in callback.message.texts[-1]
        assert "db.commit" not in events
        assert_no_machine_status(callback.message.texts)


@pytest.mark.asyncio
async def test_foreign_user_cannot_open_m2_documents(telegram_db):
    async with telegram_db() as session:
        await prepare_m2(session)
        events = []
        state = FakeState(events)
        state.data = {"keep": "active"}
        callback = FakeCallback(
            telegram_user(telegram_id=730999),
            events,
            "m2_documents_open",
        )

        await open_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )

        assert state.data == {"keep": "active"}
        assert "db.rollback" in events
        assert "Не удалось обработать" in callback.message.texts[-1]
        assert_no_machine_status(callback.message.texts)


@pytest.mark.parametrize("tampered_key", ["case_id", "consultation_id"])
@pytest.mark.asyncio
async def test_tampered_m2_fsm_cannot_upload(
    telegram_db,
    tampered_key,
):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        case_id = case.id
        consultation_id = consultation.id
        events = []
        state, _ = await open_document_state(session, events)
        state.data[tampered_key] += 9999
        events.clear()
        message = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document(),
        )

        await upload(message, state, TrackingSession(session, events))

        assert await document_count(session, case_id) == 0
        persisted_status = (
            await session.execute(
                select(Consultation.status).where(
                    Consultation.id == consultation_id
                )
            )
        ).scalar_one()
        assert persisted_status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert state.state == DocumentUploadStates.waiting_file
        assert "db.rollback" in events
        assert "Не удалось обработать" in message.answers[-1]


@pytest.mark.asyncio
async def test_multiple_files_are_saved_and_step_stays_optional(
    telegram_db,
    storage_stub,
):
    async with telegram_db() as session:
        user, case, consultation = await prepare_m2(session)
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()

        first = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document("telegram-file-1", "same-name.pdf"),
        )
        await upload(first, state, TrackingSession(session, events))
        second = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document("telegram-file-2", "same-name.pdf"),
        )
        await upload(second, state, TrackingSession(session, events))
        documents = (
            await session.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .order_by(Document.id)
            )
        ).scalars().all()

        assert len(documents) == 2
        stored_paths = [Path(document.file_path) for document in documents]
        assert all(path.exists() for path in stored_paths)
        assert all(path.parent == storage_stub[0]["path"].parent for path in stored_paths)
        assert len(set(stored_paths)) == 2
        assert [document.file_name for document in documents] == [
            "same-name.pdf",
            "same-name.pdf",
        ]
        assert all(document.uploaded_by_user_id == user.id for document in documents)
        assert all(document.status == DocumentStatus.UPLOADED.value for document in documents)
        assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert state.state == DocumentUploadStates.waiting_file
        assert callback_names(first.markups[-1]) == {
            "m2_documents_open",
            "m2_documents_finish",
            "nav_home",
        }
        assert first.answers[-1] == second.answers[-1] == "Документ сохранён."
        upload_events = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "DOCUMENT_UPLOADED",
                )
            )
        ).scalars().all()
        assert len(upload_events) == 2
        assert all("file" not in str(event.new_value).lower() for event in upload_events)


@pytest.mark.asyncio
async def test_repeated_file_delivery_is_idempotent(telegram_db, storage_stub):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        events = []
        state, _ = await open_document_state(session, events)
        document = telegram_document("same-file-id", "first-name.pdf")

        await upload(
            FakeInputMessage(telegram_user(), events, document=document),
            state,
            TrackingSession(session, events),
        )
        replay = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document("same-file-id", "second-name.pdf"),
        )
        await upload(replay, state, TrackingSession(session, events))

        assert await document_count(session, case.id) == 1
        assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert len(storage_stub) == 1
        assert await history_count(session, case.id, "DOCUMENT_UPLOADED") == 1
        assert "уже сохранён" in replay.answers[-1]
        assert state.data["processed_file_keys"] == ["unique-same-file-id"]


@pytest.mark.asyncio
async def test_document_without_name_and_photo_use_local_storage(
    telegram_db,
    storage_stub,
):
    async with telegram_db() as session:
        _, case, _ = await prepare_m2(session)
        case_id = case.id
        events = []
        state, _ = await open_document_state(session, events)
        unnamed = telegram_document("unnamed-file", None)
        photo = SimpleNamespace(
            file_id="photo-file-id",
            file_unique_id="photo-unique-id",
            file_size=512,
        )

        await upload(
            FakeInputMessage(telegram_user(), events, document=unnamed),
            state,
            TrackingSession(session, events),
        )
        await upload(
            FakeInputMessage(telegram_user(), events, photo=[photo]),
            state,
            TrackingSession(session, events),
        )
        documents = (
            await session.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .order_by(Document.id)
            )
        ).scalars().all()

        assert [document.file_name for document in documents] == [
            "document.bin",
            "photo_photo-unique-id.jpg",
        ]
        assert all(Path(document.file_path).exists() for document in documents)
        assert len(storage_stub) == 2


@pytest.mark.parametrize(
    ("text", "photo"),
    [("Это обычный текст, а не документ.", None), (None, [])],
)
@pytest.mark.asyncio
async def test_unsupported_message_keeps_fsm_and_does_not_commit(
    telegram_db,
    text,
    photo,
):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        case_id = case.id
        consultation_id = consultation.id
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()
        message = FakeInputMessage(
            telegram_user(),
            events,
            text=text,
            photo=photo,
        )

        await upload(message, state, TrackingSession(session, events))

        assert await document_count(session, case_id) == 0
        persisted_status = (
            await session.execute(
                select(Consultation.status).where(
                    Consultation.id == consultation_id
                )
            )
        ).scalar_one()
        assert persisted_status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert state.state == DocumentUploadStates.waiting_file
        assert "db.commit" not in events
        assert "Отправьте документ файлом" in message.answers[-1]


@pytest.mark.asyncio
async def test_missing_file_id_is_rejected_without_storage(telegram_db):
    async with telegram_db() as session:
        _, case, _ = await prepare_m2(session)
        case_id = case.id
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()
        message = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document(file_id=None),
        )

        await upload(message, state, TrackingSession(session, events))

        assert await document_count(session, case_id) == 0
        assert state.state == DocumentUploadStates.waiting_file
        assert "db.rollback" in events
        assert "Не удалось обработать" in message.answers[-1]


@pytest.mark.asyncio
async def test_finish_without_documents_does_not_advance(telegram_db):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()
        callback = FakeCallback(telegram_user(), events, "m2_documents_finish")

        await finish_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )

        assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert case.status == CaseStatus.M2_DOCUMENTS_OPTIONAL.value
        assert state.state == DocumentUploadStates.waiting_file
        assert "db.commit" not in events
        assert "хотя бы один документ" in callback.message.texts[-1]


@pytest.mark.parametrize("ineligible_kind", ["other_user", "archived"])
@pytest.mark.asyncio
async def test_ineligible_document_does_not_allow_finish(
    telegram_db,
    ineligible_kind,
):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        ctx = BotContextService(session)
        other_user = await ctx.get_user_from_callback(
            FakeCallback(telegram_user(telegram_id=731111), [], "setup")
        )
        document = await DocumentService(session).create_document(
            case=case,
            uploaded_by_user_id=(
                other_user.id if ineligible_kind == "other_user" else case.client_id
            ),
            document_type="OTHER",
            file_name="foreign.pdf",
            file_path="/tmp/foreign.pdf",
            mime_type="application/pdf",
            file_size=10,
        )
        if ineligible_kind == "archived":
            document.status = DocumentStatus.ARCHIVED.value
        await session.commit()
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()
        callback = FakeCallback(telegram_user(), events, "m2_documents_finish")

        await finish_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )

        assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert case.status == CaseStatus.M2_DOCUMENTS_OPTIONAL.value
        assert "db.commit" not in events
        assert "хотя бы один документ" in callback.message.texts[-1]


@pytest.mark.asyncio
async def test_finish_after_upload_advances_once_after_commit(
    telegram_db,
    storage_stub,
):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        setup_events = []
        state, _ = await open_document_state(session, setup_events)
        await upload(
            FakeInputMessage(
                telegram_user(),
                setup_events,
                document=telegram_document(),
            ),
            state,
            TrackingSession(session, setup_events),
        )
        events = []
        state.events = events
        callback = FakeCallback(telegram_user(), events, "m2_documents_finish")

        await finish_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )
        await session.refresh(consultation)
        await session.refresh(case)

        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert case.next_action == "Выберите удобное время консультации"
        assert state.state is None
        assert state.data == {}
        assert events.index("db.commit") < events.index("state.clear")
        assert events.index("db.commit") < events.index("callback.edit_text")
        assert "Документы добавлены" in callback.message.texts[-1]
        assert "consult_slot_open" not in callback_names(callback.message.markups[-1])
        assert await history_count(
            session,
            case.id,
            "CONSULTATION_DOCUMENTS_COMPLETED",
        ) == 1
        assert_no_machine_status(callback.message.texts)


@pytest.mark.asyncio
async def test_skip_creates_no_document_and_advances_once(telegram_db):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        events = []
        state = FakeState(events)
        callback = FakeCallback(telegram_user(), events, "m2_documents_skip")

        await skip_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )
        await session.refresh(consultation)
        await session.refresh(case)

        assert await document_count(session, case.id) == 0
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert case.status == CaseStatus.M2_SLOT_PENDING.value
        assert case.next_action == "Выберите удобное время консультации"
        assert state.state is None
        assert events.index("db.commit") < events.index("state.clear")
        assert events.index("db.commit") < events.index("callback.edit_text")
        assert "пропущен" in callback.message.texts[-1]
        assert await history_count(
            session,
            case.id,
            "CONSULTATION_DOCUMENTS_SKIPPED",
        ) == 1
        assert_no_machine_status(callback.message.texts)


@pytest.mark.parametrize("action", ["skip", "finish"])
@pytest.mark.asyncio
async def test_repeated_completion_is_idempotent(
    telegram_db,
    action,
    storage_stub,
):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        events = []
        state, _ = await open_document_state(session, events)
        if action == "finish":
            await upload(
                FakeInputMessage(
                    telegram_user(),
                    events,
                    document=telegram_document(),
                ),
                state,
                TrackingSession(session, events),
            )
            handler = finish_m2_documents
            callback_data = "m2_documents_finish"
            history_action = "CONSULTATION_DOCUMENTS_COMPLETED"
        else:
            handler = skip_m2_documents
            callback_data = "m2_documents_skip"
            history_action = "CONSULTATION_DOCUMENTS_SKIPPED"

        await handler(
            FakeCallback(telegram_user(), events, callback_data),
            state,
            TrackingSession(session, events),
        )
        first_history_count = await history_count(session, case.id, history_action)
        repeat_events = []
        repeat_callback = FakeCallback(
            telegram_user(),
            repeat_events,
            callback_data,
        )
        await handler(
            repeat_callback,
            FakeState(repeat_events),
            TrackingSession(session, repeat_events),
        )

        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        assert await history_count(session, case.id, history_action) == 1
        assert first_history_count == 1
        assert "уже завершён" in repeat_callback.message.texts[-1]
        assert "db.commit" not in repeat_events
        assert_no_machine_status(repeat_callback.message.texts)


@pytest.mark.asyncio
async def test_repeated_skip_does_not_clear_unrelated_fsm(telegram_db):
    async with telegram_db() as session:
        _, _, consultation = await prepare_m2(session)
        await skip_m2_documents(
            FakeCallback(telegram_user(), [], "m2_documents_skip"),
            FakeState([]),
            session,
        )
        assert consultation.status == ConsultationStatus.SLOT_PENDING.value
        events = []
        unrelated_state = FakeState(events)
        unrelated_state.data = {"calculator": "active"}
        unrelated_state.state = ConsultationDescriptionStates.waiting_description
        callback = FakeCallback(telegram_user(), events, "m2_documents_skip")

        await skip_m2_documents(
            callback,
            unrelated_state,
            TrackingSession(session, events),
        )

        assert unrelated_state.data == {"calculator": "active"}
        assert unrelated_state.state == ConsultationDescriptionStates.waiting_description
        assert "уже завершён" in callback.message.texts[-1]
        assert "db.commit" not in events


@pytest.mark.asyncio
async def test_unknown_consultation_status_is_handled_safely(telegram_db):
    async with telegram_db() as session:
        _, _, consultation = await prepare_m2(session)
        consultation_id = consultation.id
        consultation.status = "LEGACY_DOCUMENT_STATUS"
        await session.commit()
        events = []
        state = FakeState(events)
        state.data = {"keep": "active"}
        callback = FakeCallback(telegram_user(), events, "m2_documents_open")

        await open_m2_documents(
            callback,
            state,
            TrackingSession(session, events),
        )
        persisted_status = (
            await session.execute(
                select(Consultation.status).where(
                    Consultation.id == consultation_id
                )
            )
        ).scalar_one()

        assert persisted_status == "LEGACY_DOCUMENT_STATUS"
        assert state.data == {"keep": "active"}
        assert "db.rollback" in events
        assert "Не удалось обработать" in callback.message.texts[-1]
        assert "LEGACY_DOCUMENT_STATUS" not in callback.message.texts[-1]
        assert_no_machine_status(callback.message.texts)


@pytest.mark.asyncio
async def test_document_service_error_removes_file_and_keeps_fsm(
    telegram_db,
    monkeypatch,
    storage_stub,
):
    async with telegram_db() as session:
        _, case, consultation = await prepare_m2(session)
        case_id = case.id
        consultation_id = consultation.id
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()

        async def fail_create_document(*args, **kwargs):
            raise RuntimeError("simulated storage failure")

        monkeypatch.setattr(
            DocumentService,
            "create_document",
            fail_create_document,
        )
        message = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document(),
        )

        await upload(message, state, TrackingSession(session, events))

        assert await document_count(session, case_id) == 0
        persisted_status = (
            await session.execute(
                select(Consultation.status).where(
                    Consultation.id == consultation_id
                )
            )
        ).scalar_one()
        assert persisted_status == ConsultationStatus.DOCUMENTS_OPTIONAL.value
        assert state.state == DocumentUploadStates.waiting_file
        assert "db.rollback" in events
        assert "db.commit" not in events
        assert "Не удалось обработать" in message.answers[-1]
        assert len(storage_stub) == 1
        assert not storage_stub[0]["path"].exists()


@pytest.mark.asyncio
async def test_storage_failure_rolls_back_and_keeps_fsm(
    telegram_db,
    monkeypatch,
):
    class FailingStorage:
        async def save_telegram_file(self, **kwargs):
            raise OSError("simulated download failure")

    monkeypatch.setattr(
        "app.bot.screens.documents.LocalStorageService",
        FailingStorage,
    )
    async with telegram_db() as session:
        _, case, _ = await prepare_m2(session)
        case_id = case.id
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()
        message = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document(),
        )

        await upload(message, state, TrackingSession(session, events))

        assert await document_count(session, case_id) == 0
        assert state.state == DocumentUploadStates.waiting_file
        assert "db.rollback" in events
        assert "db.commit" not in events
        assert "Не удалось обработать" in message.answers[-1]


@pytest.mark.asyncio
async def test_commit_failure_removes_file_and_rolls_back_document(
    telegram_db,
    storage_stub,
):
    class FailingCommitSession(TrackingSession):
        async def commit(self):
            self.events.append("db.commit")
            raise RuntimeError("simulated commit failure")

    async with telegram_db() as session:
        _, case, _ = await prepare_m2(session)
        case_id = case.id
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()
        message = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document(),
        )

        await upload(message, state, FailingCommitSession(session, events))

        assert await document_count(session, case_id) == 0
        assert state.state == DocumentUploadStates.waiting_file
        assert "db.commit" in events
        assert "db.rollback" in events
        assert len(storage_stub) == 1
        assert not storage_stub[0]["path"].exists()
        assert "Не удалось обработать" in message.answers[-1]


@pytest.mark.asyncio
async def test_state_update_failure_after_commit_keeps_document_and_file(
    telegram_db,
    storage_stub,
):
    async with telegram_db() as session:
        _, case, _ = await prepare_m2(session)
        case_id = case.id
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()

        async def fail_update_data(**values):
            events.append("state.update.failed")
            raise RuntimeError("simulated FSM storage failure")

        state.update_data = fail_update_data
        message = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document(),
        )

        await upload(message, state, TrackingSession(session, events))

        documents = (
            await session.execute(
                select(Document).where(Document.case_id == case_id)
            )
        ).scalars().all()
        assert len(documents) == 1
        assert Path(documents[0].file_path).is_file()
        assert len(storage_stub) == 1
        assert storage_stub[0]["path"].is_file()
        assert "db.commit" in events
        assert "state.update.failed" in events
        assert "db.rollback" not in events
        assert message.answers == ["Документ сохранён."]
        assert all("Не удалось обработать" not in text for text in message.answers)
        assert state.data["processed_file_keys"] == []


@pytest.mark.asyncio
async def test_answer_failure_after_commit_keeps_document_and_file(
    telegram_db,
    storage_stub,
):
    async with telegram_db() as session:
        _, case, _ = await prepare_m2(session)
        case_id = case.id
        events = []
        state, _ = await open_document_state(session, events)
        events.clear()
        message = FakeInputMessage(
            telegram_user(),
            events,
            document=telegram_document(),
        )

        async def fail_answer(text, **kwargs):
            events.append("message.answer.failed")
            raise RuntimeError("simulated Telegram response failure")

        message.answer = fail_answer

        await upload(message, state, TrackingSession(session, events))

        documents = (
            await session.execute(
                select(Document).where(Document.case_id == case_id)
            )
        ).scalars().all()
        assert len(documents) == 1
        assert Path(documents[0].file_path).is_file()
        assert len(storage_stub) == 1
        assert storage_stub[0]["path"].is_file()
        assert "db.commit" in events
        assert "state.update" in events
        assert "message.answer.failed" in events
        assert "db.rollback" not in events
        assert message.answers == []


@pytest.mark.asyncio
async def test_m1_document_finish_keeps_existing_review_flow(telegram_db):
    async with telegram_db() as session:
        events = []
        callback = FakeCallback(telegram_user(), events, "setup")
        ctx = BotContextService(session)
        user = await ctx.get_user_from_callback(callback)
        case = await ctx.case_service.create_case(
            client=user,
            route=RouteCode.M1.value,
            status=CaseStatus.M1_DOCUMENTS_PENDING.value,
        )
        document = await DocumentService(session).create_document(
            case=case,
            uploaded_by_user_id=user.id,
            document_type="DDU",
            file_name="ddu.pdf",
            file_path="telegram-m1-file",
            mime_type="application/pdf",
            file_size=1024,
        )
        await session.commit()
        state = FakeState(events)
        finish_callback = FakeCallback(
            telegram_user(),
            events,
            "doc_finish_upload",
        )

        await finish(
            finish_callback,
            TrackingSession(session, events),
            state,
        )
        await session.refresh(case)
        await session.refresh(document)

        assert case.route == RouteCode.M1.value
        assert case.status == CaseStatus.M1_LAWYER_REVIEW.value
        assert document.status == DocumentStatus.ON_REVIEW.value
        assert "переданы на проверку" in finish_callback.message.texts[-1]
