from collections import Counter

import pytest
from fastapi.routing import iter_route_contexts

from app.api.operator import operator_page, operator_status
from app.bot.keyboards import main_menu, reply_main_menu
from app.main import app


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_reply_menu_prioritizes_calculation_and_keeps_clear_labels():
    markup = reply_main_menu()
    assert markup.is_persistent is True
    assert markup.keyboard[0][0].text == "🧮 Рассчитать неустойку"
    assert "📁 Моё дело" in [button.text for row in markup.keyboard for button in row]
    assert markup.input_field_placeholder == "Выберите: расчёт, дело или помощь"


def test_inline_menu_hides_disabled_payments_and_parallel_calculation():
    markup = main_menu(case_exists=True, payments_enabled=False)
    assert "payments_open" not in _callbacks(markup)
    assert "calc_start" not in _callbacks(markup)
    assert _callbacks(markup) == [
        "my_case_open",
        "documents_open",
        "message_history",
        "message_create",
        "contact_lawyer",
    ]
    assert [len(row) for row in markup.inline_keyboard] == [2, 2, 1]


def test_inline_menu_shows_payments_when_provider_is_enabled():
    markup = main_menu(case_exists=True, payments_enabled=True)
    assert "💳 Оплаты" in _texts(markup)
    assert "payments_open" in _callbacks(markup)
    assert "calc_start" not in _callbacks(markup)
    assert [len(row) for row in markup.inline_keyboard] == [2, 2, 1, 1]


@pytest.mark.asyncio
async def test_operator_page_is_role_aware_and_does_not_show_secrets():
    response = await operator_page()
    body = response.body.decode("utf-8")

    assert "Рабочее пространство" in body
    assert "Ежедневная работа" in body
    assert "Контроль и настройка" in body
    assert "Рабочие разделы показываются в соответствии с вашей ролью" in body
    assert "fetch('/auth/session'" in body
    assert "const isLawyer=roles.includes('lawyer')" in body
    assert "isAdmin=roles.includes('admin')||roles.includes('superadmin')" in body
    assert "systemSection.hidden=true" in body
    assert "link('/lawyer/workspace/ui'" in body
    assert "link('/lawyer/consultation-desk/ui'" in body
    assert "link('/admin/workdesk/ui'" in body
    assert "link('/message-center/ui'" in body
    assert "BOT_TOKEN" not in body
    assert "ADMIN_PASSWORD" not in body


@pytest.mark.asyncio
async def test_operator_status_publishes_primary_and_compatibility_workspaces():
    status = await operator_status()
    workspaces = status["workspaces"]

    assert workspaces["admin"] == "/admin/workdesk/ui"
    assert workspaces["lawyer"] == "/lawyer/workspace/ui"
    assert workspaces["lawyer_consultations"] == "/lawyer/consultation-desk/ui"
    assert workspaces["document_review"] == "/document-access/review/ui"
    assert workspaces["messages"] == "/message-center/ui"
    assert workspaces["telegram_delivery"] == "/admin/notification-delivery/ui"
    assert workspaces["admin_legacy"] == "/admin-ui"
    assert workspaces["lawyer_legacy"] == "/lawyer/ui"


def test_primary_staff_workspace_links_are_registered_once():
    counts = Counter(
        context.path
        for context in iter_route_contexts(app.routes)
        if "GET" in (context.methods or ())
    )
    required = {
        "/operator",
        "/admin/workdesk/ui",
        "/lawyer/workspace/ui",
        "/lawyer/consultation-desk/ui",
        "/document-access/review/ui",
        "/message-center/ui",
        "/admin/sla/ui",
        "/admin/consultation-outcomes/ui",
        "/admin/notification-delivery/ui",
    }

    missing = sorted(path for path in required if counts[path] == 0)
    duplicated = {path: counts[path] for path in required if counts[path] > 1}

    assert missing == [], f"operator links contain unregistered workspaces: {missing}"
    assert duplicated == {}, f"operator links are registered more than once: {duplicated}"
