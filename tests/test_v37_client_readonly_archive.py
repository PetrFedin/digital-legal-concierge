from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_client_case_history_uses_business_timezone_and_exact_case_pagination():
    source = read("app/bot/screens/history.py")

    assert "format_business_datetime(" in source
    assert 'pattern="%d.%m.%Y · %H:%M"' in source
    assert 'HISTORY_OPEN_PREFIX = "case_history_open:v2:"' in source
    assert 'f"case_history_before:v2:{case_id}:{int(next_before_id)}"' in source
    assert 'bound_case_callback("message_create", case_id)' in source


def test_legacy_history_prefers_visible_completed_case_before_latest_fallback():
    source = read("app/bot/screens/history.py")

    assert "def _legacy_visible_case_number" in source
    assert "async def _visible_completed_case" in source
    assert "Case.case_number == case_number" in source
    assert "str(case.status) not in _COMPLETED_STATUS_VALUES" in source
    render = source.split("async def _render_history", 1)[1].split(
        "async def case_history", 1
    )[0]
    assert "case = await _visible_completed_case(" in render
    assert render.index("case = await _visible_completed_case(") < render.index(
        "case = await latest_completed_case_for_user("
    )


def test_completed_document_reads_bypass_active_only_action_center():
    guard = read("app/bot/screens/document_read_scope_guard.py")
    bot = read("app/bot/bot.py")

    assert 'c.data == "documents_open"' in guard
    assert 'c.data == "documents_list_open"' in guard
    assert "get_selected_case_for_user(" in guard
    assert "get_active_cases_for_user" in guard
    assert "await document_action_center._render_home" in guard
    assert "await documents._render_documents_home" in guard
    assert "await documents._render_current_documents" in guard
    assert bot.index("document_read_scope_guard.router,") < bot.index(
        "document_action_center.router,"
    )
    assert bot.index("document_read_scope_guard.router,") < bot.index(
        "documents.router,"
    )


def test_completed_documents_renderer_is_read_only_for_m1_and_m2_archive_scope():
    documents = read("app/bot/screens/documents.py")
    scope = read("app/domain/cases/client_case_scope.py")

    assert "active_or_latest_completed_m1_case_for_user(" in documents
    assert "if completed:" in documents
    assert "загрузка, замена и повторная передача юристу недоступны" in documents
    assert "latest_completed_m1_case_for_user" in scope
    alias = scope.split("async def latest_completed_m1_case_for_user", 1)[1].split(
        "async def active_or_latest_completed_m1_case_for_user", 1
    )[0]
    assert "return await latest_completed_case_for_user" in alias


def test_payment_archive_compatibility_alias_is_route_complete_not_m1_only():
    payments = read("app/bot/screens/payments.py")
    scope = read("app/domain/cases/client_case_scope.py")

    assert "active_or_latest_completed_m1_case_for_user" in payments
    alias = scope.split("async def active_or_latest_completed_m1_case_for_user", 1)[1].split(
        "__all__", 1
    )[0]
    assert "return await active_or_latest_completed_case_for_user(" in alias
    assert "CaseStatus.M2_CLOSED" in scope
