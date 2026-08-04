from types import SimpleNamespace

from app.bot.screens.documents import (
    _active_documents,
    _archived_documents,
    _client_document_comment,
    _client_document_status,
    _document_counts,
    _paginate,
    _recommended_step,
)
from app.domain.statuses.case_statuses import CaseStatus


def document(status: str, *, comment: str | None = None, version: int = 1):
    return SimpleNamespace(
        id=version,
        title="ДДУ",
        version=version,
        status=status,
        lawyer_comment=comment,
    )


def case(route: str = "M1", status=CaseStatus.M1_DOCUMENTS_PENDING):
    return SimpleNamespace(route=route, status=status)


def test_client_document_groups_hide_archived_versions_from_current_flow():
    current = document("APPROVED", version=3)
    archived = document("ARCHIVED", version=2)
    items = [current, archived]

    assert _active_documents(items) == [current]
    assert _archived_documents(items) == [archived]
    assert _client_document_status(archived) == "Предыдущая версия"


def test_reupload_reason_has_priority_over_other_document_actions():
    replacement = document(
        "NEEDS_REUPLOAD",
        comment="Добавьте все страницы и подпись",
    )
    approved = document("APPROVED", version=2)

    text, buttons = _recommended_step(case(), [replacement, approved])

    assert "новую версию" in text.lower()
    assert buttons[0] == (
        "🔁 Загрузить новую версию",
        "documents_upload_open",
    )
    assert _client_document_comment(replacement) == (
        "Что исправить: Добавьте все страницы и подпись"
    )


def test_long_lawyer_comment_is_bounded_for_telegram_message():
    replacement = document(
        "NEEDS_REUPLOAD",
        comment="слово " * 100,
    )

    visible = _client_document_comment(replacement)

    assert visible is not None
    assert visible.startswith("Что исправить: ")
    assert visible.endswith("…")
    assert len(visible) <= len("Что исправить: ") + 240


def test_new_upload_is_the_only_document_submission_trigger():
    new = document("UPLOADED")
    review = document("ON_REVIEW", version=2)

    text, buttons = _recommended_step(case(), [new, review])
    counts = _document_counts([new, review])

    assert counts == {"new": 1, "review": 1, "approved": 0, "replacement": 0}
    assert "Передайте новые файлы" in text
    assert buttons[0][1] == "doc_finish_upload"


def test_waiting_for_lawyer_has_refresh_instead_of_false_completion():
    text, buttons = _recommended_step(case(), [document("ON_REVIEW")])

    assert "Действий не требуется" in text
    assert buttons == [("🔄 Обновить статусы", "documents_open")]


def test_m2_without_documents_can_continue_without_dead_end():
    m2_case = case("M2", CaseStatus.M2_DOCUMENTS_OPTIONAL)

    text, buttons = _recommended_step(m2_case, [])

    assert "Документы необязательны" in text
    assert buttons[0] == ("Продолжить без документов", "doc_skip_m2")
    assert ("➕ Добавить документ", "documents_upload_open") in buttons


def test_current_and_history_lists_have_bounded_pages():
    items = [document("APPROVED", version=index) for index in range(1, 20)]

    first, page, total = _paginate(items, 0)
    last, last_page, last_total = _paginate(items, 99)

    assert len(first) == 8
    assert page == 0
    assert total == 3
    assert len(last) == 3
    assert last_page == 2
    assert last_total == 3


def test_source_keeps_legacy_callbacks_and_blocks_empty_submission():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app/bot/screens/documents.py"
    ).read_text(encoding="utf-8")

    assert 'c.data == "documents_open"' in source
    assert 'c.data == "documents_list_open"' in source
    assert 'c.data == "doc_finish_upload"' in source
    assert 'c.data == "documents_history_open"' in source
    assert 'c.data == "documents_upload_open"' in source
    assert 'c.data.startswith("documents_current_page:")' in source
    assert 'c.data.startswith("documents_history_page:")' in source
    assert 'new_uploads = [item for item in active if _status(item) == "UPLOADED"]' in source
    assert "if not new_uploads:" in source
    assert source.index("if not new_uploads:") < source.index(
        "send_documents_to_review("
    )
    assert "Все новые файлы уже переданы юристу" in source
    assert "Эти файлы сохранены в истории, но больше не участвуют" in source
    assert "Следующий шаг:" in source


def test_security_metadata_stays_internal_and_refresh_is_idempotent():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app/bot/screens/documents.py"
    ).read_text(encoding="utf-8")

    # Technical metadata is required by the secure document domain service.
    assert "security_status=stored.security_status" in source
    assert "encryption_status=stored.encryption_status" in source
    # It must never be rendered as a client-facing status label.
    assert "Статус безопасности" not in source
    assert "Статус шифрования" not in source
    assert "def _safe_edit" in source
    assert "message is not modified" in source
    assert "Статусы пока не изменились." in source
