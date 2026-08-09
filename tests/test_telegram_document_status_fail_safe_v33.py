from types import SimpleNamespace

from app.bot.screens.document_action_center import _document_line, _next_action


def test_unknown_document_status_never_recommends_duplicate_upload():
    case = SimpleNamespace(route="M1", status="M1_DOCUMENTS_RECEIVED")
    future_document = SimpleNamespace(
        id=10,
        title="ДДУ",
        version=4,
        status="FUTURE_DOCUMENT_STATUS",
        lawyer_comment=None,
    )

    text, buttons = _next_action(case, [future_document])

    assert "пока не поддерживается ботом" in text
    assert "Не загружайте дубликат" in text
    assert buttons == [("📁 К актуальному шагу дела", "my_case_open")]


def test_required_document_is_explicitly_uploadable():
    case = SimpleNamespace(route="M1", status="M1_DOCUMENTS_PENDING")
    required = SimpleNamespace(
        id=11,
        title="ДДУ",
        version=None,
        status="REQUIRED",
        lawyer_comment=None,
    )

    text, buttons = _next_action(case, [required])

    assert text == "Загрузить обязательный документ «ДДУ»."
    assert buttons == [("➕ Загрузить документ", "documents_upload_open")]
    assert _document_line(required) == "• ДДУ — требуется загрузить"
