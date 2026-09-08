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


def test_case_activity_consultation_details_use_same_business_timezone_as_history_heading():
    source = read("app/domain/cases/case_activity.py")

    assert "from app.presentation_time import format_business_datetime" in source
    helper = source.split("def _format_datetime", 1)[1].split(
        "def _document_label", 1
    )[0]
    assert "format_business_datetime(" in helper
    assert 'pattern="%d.%m.%Y в %H:%M"' in helper
    assert 'return parsed.strftime("%d.%m.%Y в %H:%M")' not in helper
    assert '"CONSULTATION_SLOT_HELD"' in source
    assert '"CONSULTATION_RESCHEDULED"' in source
    assert 'return f"Дата и время: {scheduled}."' in source
    assert 'return f"Новое время: {scheduled}."' in source


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


def test_completed_case_scope_exposes_ordered_archive_without_selecting_terminal_case():
    scope = read("app/domain/cases/client_case_scope.py")
    archive = read("app/bot/screens/client_archive.py")

    assert "async def completed_cases_for_user" in scope
    completed = scope.split("async def completed_cases_for_user", 1)[1].split(
        "async def latest_completed_case_for_user", 1
    )[0]
    assert "Case.status.in_(CLIENT_COMPLETED_CASE_STATUSES)" in completed
    assert "order_by(*_completed_ordering())" in completed
    assert "limit(bounded_limit)" in completed
    assert '"completed_cases_for_user"' in scope
    assert "select_case_for_user(" not in archive
    assert "Просмотр архива не меняет выбранное активное дело" in archive


def test_multi_completed_archive_selector_uses_exact_case_callbacks():
    archive = read("app/bot/screens/client_archive.py")

    selector = archive.split("def _archive_selector", 1)[1].split(
        "async def _archive_case_projection", 1
    )[0]
    assert "Завершённых обращений" in selector
    assert "Выберите точное обращение" in selector
    assert 'f"my_case_archive:v2:{int(case.id)}"' in selector
    assert '"🗄 Архив обращений"' in archive
    assert 'c.data == "my_case_archive_open"' in archive
    assert 'startswith("my_case_archive:v2:")' in archive


def test_exact_archive_case_links_every_read_surface_to_same_case_id():
    archive = read("app/bot/screens/client_archive.py")

    buttons = archive.split("def _archive_case_buttons", 1)[1].split(
        "async def _archive_case_text_buttons", 1
    )[0]
    assert 'f"client_archive_result:v2:{case_id}"' in buttons
    assert 'f"client_archive_documents:v2:{case_id}:0"' in buttons
    assert 'f"client_archive_payments:v2:{case_id}"' in buttons
    assert 'f"message_history:v2:{case_id}:0"' in buttons
    assert 'f"case_history_open:v2:{case_id}"' in buttons
    assert '"calc_start"' in buttons


def test_exact_archive_reads_verify_owned_terminal_case_before_rendering():
    archive = read("app/bot/screens/client_archive.py")

    owned = archive.split("async def _owned_completed_case", 1)[1].split(
        "def _archive_selector", 1
    )[0]
    assert "get_case_for_user(" in owned
    assert "str(case.status) not in _COMPLETED_VALUES" in owned
    assert "select_case_for_user(" not in archive

    for handler in (
        "open_exact_archive_case",
        "open_exact_archive_documents",
        "open_exact_archive_payments",
        "open_exact_archive_consultation_result",
    ):
        body = archive.split(f"async def {handler}", 1)[1]
        assert "_owned_completed_case(" in body


def test_archive_documents_and_payments_are_explicitly_read_only():
    archive = read("app/bot/screens/client_archive.py")

    docs = archive.split("async def open_exact_archive_documents", 1)[1].split(
        "async def open_exact_archive_payments", 1
    )[0]
    assert "DocumentService(db).list_case_documents(case_id)" in docs
    assert "Загрузка, замена и повторная передача юристу" in docs
    assert "document_upload_start" not in docs
    assert "document_replace" not in docs

    payment = archive.split("async def open_exact_archive_payments", 1)[1].split(
        "async def open_exact_archive_consultation_result", 1
    )[0]
    assert "PaymentService(db).list_case_payments(case_id)" in payment
    assert "только для просмотра" in payment
    assert 'f"pay_open:{int(item.id)}"' in payment
    assert "pay_fake_success" not in payment
    assert "consult_pay" not in payment


def test_exact_archive_consultation_result_never_falls_back_to_another_case():
    archive = read("app/bot/screens/client_archive.py")

    result = archive.split("async def open_exact_archive_consultation_result", 1)[1].split(
        "__all__", 1
    )[0]
    assert "latest_terminal_client_consultation(" in result
    assert "case_id=case_id" in result
    assert "Результат другого дела не подставляется" in result
    assert "format_business_datetime(consultation.scheduled_at)" in result


def test_archive_is_mounted_after_wording_patch_and_before_reply_menu_owner():
    bot = read("app/bot/bot.py")

    assert "client_archive.install_archive_button()" in bot
    assert bot.index("install_client_wording()") < bot.index(
        "client_archive.install_archive_button()"
    )
    assert bot.index("client_archive.router,") < bot.index("reply_menu_direct.router,")


def test_logical_my_case_and_back_use_archive_aware_resolver():
    navigation = read("app/bot/screens/navigation_history_guard.py")

    assert "client_archive" in navigation
    target = navigation.split("async def _render_target", 1)[1].split(
        "async def _record_after", 1
    )[0]
    assert 'if target == "my_case_open":' in target
    assert "client_archive.route_my_case_or_archive(callback, db)" in target

    handler = navigation.split("async def logical_my_case", 1)[1].split(
        "async def logical_documents", 1
    )[0]
    assert "client_archive.route_my_case_or_archive(callback, db)" in handler
    back = navigation.split("async def logical_back", 1)[1].split(
        "async def guarded_case_selection", 1
    )[0]
    assert "client_archive.route_my_case_or_archive(callback, db)" in back


def test_exact_archive_message_history_is_read_only_and_never_marks_foreign_context_read():
    history_guard = read("app/bot/screens/message_history_guard.py")

    assert 'MESSAGE_HISTORY_EXACT_PREFIX = "message_history:v2:"' in history_guard
    assert "target_case_id" in history_guard
    assert "selected_same_case = selected_case_id == case_id" in history_guard
    assert "read_only = str(case.status) in _COMPLETED_STATUS_VALUES" in history_guard
    assert "if visible_team_ids and not read_only and selected_same_case:" in history_guard
    assert "mark_lawyer_messages_read" in history_guard
