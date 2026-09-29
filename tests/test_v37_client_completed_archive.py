from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_completed_case_query_supports_count_and_deterministic_pagination():
    source = read("app/domain/cases/client_case_scope.py")

    assert "async def completed_case_count_for_user" in source
    assert "select(func.count(Case.id))" in source
    assert "offset: int = 0" in source
    assert ".offset(bounded_offset)" in source
    assert ".order_by(*_completed_ordering())" in source
    assert '"completed_case_count_for_user"' in source


def test_client_archive_selector_is_compact_paginated_and_exact_case_bound():
    source = read("app/bot/screens/client_archive.py")

    assert "_ARCHIVE_CASE_PAGE_SIZE = 6" in source
    assert "completed_case_count_for_user" in source
    assert "my_case_archive_page:v2:" in source
    assert 'f"my_case_archive:v2:{int(case.id)}"' in source
    assert 'f"client_archive_documents:v2:{case_id}:0"' in source
    assert 'f"client_archive_payments:v2:{case_id}:0"' in source
    assert 'f"message_history:v2:{case_id}:0"' in source
    assert 'f"case_history_open:v2:{case_id}"' in source
    assert "Просмотр архива не меняет выбранное активное дело" in source


def test_terminal_archive_never_becomes_selected_active_case():
    source = read("app/bot/screens/client_archive.py")

    assert "get_case_for_user(" in source
    assert "str(case.status) not in _COMPLETED_VALUES" in source
    assert "select_case_for_user(" not in source
    assert "change_status(" not in source
    assert "create_document(" not in source
    assert "get_or_create_payment(" not in source


def test_archive_documents_and_payments_paginate_without_mutation_controls():
    source = read("app/bot/screens/client_archive.py")

    assert "_ARCHIVE_DOCUMENT_PAGE_SIZE = 8" in source
    assert "_ARCHIVE_PAYMENT_PAGE_SIZE = 6" in source
    assert "client_archive_documents:v2:{case_id}:{page - 1}" in source
    assert "client_archive_documents:v2:{case_id}:{page + 1}" in source
    assert "client_archive_payments:v2:{case_id}:{page - 1}" in source
    assert "client_archive_payments:v2:{case_id}:{page + 1}" in source
    assert "pay_fake_success:" not in source
    assert "Перейти к оплате" not in source
    assert "Загрузка, замена и повторная передача юристу для закрытого дела недоступны" in source


def test_navigation_history_delegates_my_case_to_archive_resolver_before_legacy_renderers():
    navigation = read("app/bot/screens/navigation_history_guard.py")
    bot = read("app/bot/bot.py")

    assert "await client_archive.route_my_case_or_archive(callback, db)" in navigation
    assert "lambda: client_archive.route_my_case_or_archive(callback, db)" in navigation
    assert bot.index("navigation_history_guard.router,") < bot.index("client_archive.router,")
    assert bot.index("client_archive.router,") < bot.index("reply_menu_direct.router,")
    assert bot.index("client_archive.router,") < bot.index("my_case.router,")


def test_archive_result_is_exact_case_and_never_falls_back_to_another_result():
    source = read("app/bot/screens/client_archive.py")

    result_block = source.split("async def open_exact_archive_consultation_result", 1)[1]
    assert "case_id=case_id" in result_block
    assert "Результат другого дела не подставляется" in result_block
    assert 'f"client_archive_result:v2:{case_id}"' in source


def test_read_only_message_history_keeps_exact_archive_case_across_navigation_and_errors():
    source = read("app/bot/screens/message_history_guard.py")

    assert '("🕘 История этого обращения", f"case_history_open:v2:{case_id}")' in source
    assert '("🗄 Архив обращения", f"my_case_archive:v2:{case_id}")' in source
    assert "def _history_error_keyboard" in source
    assert 'f"message_history:v2:{case_id}:{max(0, page)}"' in source
    assert "Read tracking is a mutation" in source
    assert "visible_team_ids and not read_only and selected_same_case" in source


def test_completed_timeline_returns_to_exact_archive_not_whichever_case_is_active():
    source = read("app/bot/screens/history.py")

    completed_block = source.split("if completed:", 1)[1].split("elif selected_same_case:", 1)[0]
    assert '("🗄 Архив обращения", f"my_case_archive:v2:{case_id}")' in completed_block
    assert '("📁 Активное дело", "my_case_open")' in completed_block
    assert 'f"case_history_before:v2:{case_id}:{int(next_before_id)}"' in source
    assert 'f"case_history_open:v2:{case_id}"' in source


def test_completed_payment_detail_is_intercepted_before_live_payment_logic_and_keeps_exact_case():
    source = read("app/bot/screens/client_archive_payment_guard.py")
    bot = read("app/bot/bot.py")

    assert "str(case.status) not in _COMPLETED_VALUES" in source
    assert 'f"client_archive_payments:v2:{case_id}:0"' in source
    assert 'f"case_history_open:v2:{case_id}"' in source
    assert 'f"my_case_archive:v2:{case_id}"' in source
    assert "create_payment_link(" not in source
    assert "reconcile(" not in source
    assert "await payment_archive_guard.guard_archived_payment_open" in source
    assert "await payment_archive_guard.guard_archived_fake_success" in source
    assert bot.index("client_archive_payment_guard.router,") < bot.index(
        "payment_archive_guard.router,"
    )


def test_archive_payment_guard_is_physically_imported_and_precedes_legacy_payment_router():
    bot = read("app/bot/bot.py")

    assert "client_archive_payment_guard," in bot
    assert bot.index("client_archive_payment_guard.router,") < bot.index("payments.router,")


def test_self_filing_archive_keeps_service_label_in_selector_and_buttons():
    source = read("app/bot/screens/client_archive.py")

    assert "def _archive_route_label(case)" in source
    assert '"SELF_FILING_PACKAGE"' in source
    assert '"Пакет для самостоятельной подачи"' in source
    assert 'f"{case.case_number} · {_archive_route_label(case)}"' in source
    assert 'f"🗄 {case.case_number} · {_archive_route_label(case)}"' in source
