from pathlib import Path
from types import SimpleNamespace

from app.bot.document_replacement_protection import replacement_snapshot_matches


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
