from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot.document_replacement_protection import (
    DocumentReplacementUploadProtectionMiddleware,
    replacement_snapshot_matches,
)
from app.bot.states import DocumentUploadStates


ROOT = Path(__file__).resolve().parents[1]


def _document(**overrides):
    values = {
        "id": 42,
        "case_id": 9,
        "document_type": "DDU",
        "version": 3,
        "status": "NEEDS_REUPLOAD",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_current_replacement_snapshot_matches_at_file_receipt():
    document = _document()

    assert replacement_snapshot_matches(
        document,
        document,
        case_id=9,
        document_type="DDU",
        document_id=42,
        expected_version=3,
    )


def test_replacement_snapshot_fails_if_lawyer_decision_changed():
    approved = _document(status="APPROVED")

    assert not replacement_snapshot_matches(
        approved,
        approved,
        case_id=9,
        document_type="DDU",
        document_id=42,
        expected_version=3,
    )


def test_replacement_snapshot_fails_if_newer_active_version_exists():
    requested = _document()
    newer = _document(id=43, version=4, status="UPLOADED")

    assert not replacement_snapshot_matches(
        requested,
        newer,
        case_id=9,
        document_type="DDU",
        document_id=42,
        expected_version=3,
    )


def test_replacement_snapshot_fails_cross_case_or_wrong_type():
    document = _document()

    assert not replacement_snapshot_matches(
        document,
        document,
        case_id=10,
        document_type="DDU",
        document_id=42,
        expected_version=3,
    )
    assert not replacement_snapshot_matches(
        document,
        document,
        case_id=9,
        document_type="APPENDIX",
        document_id=42,
        expected_version=3,
    )


class _FakeState:
    def __init__(self, data):
        self.data = dict(data)
        self.cleared = False

    async def get_state(self):
        return DocumentUploadStates.waiting_file.state

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared = True
        self.data.clear()


class _FakeMessage:
    def __init__(self):
        self.document = object()
        self.photo = None
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_regular_document_upload_bypasses_replacement_guard():
    state = _FakeState({"document_type": "DDU"})
    event = _FakeMessage()
    called = []

    async def handler(message, data):
        called.append((message, data))
        return "handled"

    result = await DocumentReplacementUploadProtectionMiddleware()(
        handler,
        event,
        {"state": state},
    )

    assert result == "handled"
    assert called
    assert state.cleared is False
    assert event.answers == []


@pytest.mark.asyncio
async def test_malformed_replacement_snapshot_fails_closed_before_file_processing():
    state = _FakeState(
        {
            "document_type": "DDU",
            "replacement_document_id": "broken",
            "replacement_expected_version": 3,
        }
    )
    event = _FakeMessage()
    called = []

    async def handler(message, data):
        called.append((message, data))
        return "unsafe"

    result = await DocumentReplacementUploadProtectionMiddleware()(
        handler,
        event,
        {"state": state},
    )

    assert result is None
    assert called == []
    assert state.cleared is True
    assert event.answers
    assert "Файл не обрабатывался" in event.answers[0][0]


def test_file_receipt_guard_is_registered_before_upload_handler_and_flood_gate():
    guard_source = (
        ROOT / "app/bot/document_replacement_protection.py"
    ).read_text(encoding="utf-8")
    bot_source = (ROOT / "app/bot/bot.py").read_text(encoding="utf-8")

    assert "DocumentUploadStates.waiting_file.state" in guard_source
    assert '"replacement_document_id" in state_data' in guard_source
    assert '"replacement_expected_version" in state_data' in guard_source
    assert "Document.status != _ARCHIVED_STATUS" in guard_source
    assert "Document.version.desc(), Document.id.desc()" in guard_source
    assert "Файл не обрабатывался" in guard_source
    assert "return None" in guard_source
    assert "return await handler(event, data)" in guard_source

    guard_registration = bot_source.index(
        "dispatcher.message.middleware(DocumentReplacementUploadProtectionMiddleware())"
    )
    flood_registration = bot_source.index(
        "dispatcher.message.middleware(flood_control)"
    )
    document_router = bot_source.index("documents.router")
    assert guard_registration < flood_registration
    assert guard_registration < document_router
