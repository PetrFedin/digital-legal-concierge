from collections import Counter
import inspect

from fastapi.routing import iter_route_contexts

from app.api.operator import operator_page
from app.bot.keyboards import main_menu, reply_main_menu
from app.main import app


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_reply_menu_for_new_client_has_only_valid_entry_points():
    markup = reply_main_menu(False)
    texts = [button.text for row in markup.keyboard for button in row]

    assert markup.is_persistent is True
    assert texts == [
        "🧮 Рассчитать неустойку",
        "💬 Связаться с юристом",
        "🏠 Главная",
    ]
    assert "📁 Моё дело" not in texts
    assert "📄 Документы" not in texts
    assert markup.input_field_placeholder == "Выберите: расчёт или помощь юриста"


def test_reply_menu_for_active_case_exposes_case_workspace():
    markup = reply_main_menu(True)
    texts = [button.text for row in markup.keyboard for button in row]

    assert texts == [
        "📁 Моё дело",
        "📄 Документы",
        "💬 Переписка",
        "✉️ Новый вопрос",
        "🏠 Главная",
    ]
    assert "🧮 Рассчитать неустойку" not in texts
    assert markup.input_field_placeholder == "Выберите: дело, документы или переписка"


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


def test_operator_page_is_authenticated_role_aware_hub_without_secrets():
    source = inspect.getsource(operator_page)

    assert "Рабочее пространство" in source
    assert "Ежедневная работа" in source
    assert "Контроль и настройка" in source
    assert "Рабочие разделы показываются в соответствии с вашей ролью" in source
    assert "fetch('/auth/session'" in source
    assert "const isLawyer=roles.includes('lawyer')" in source
    assert "isAdmin=roles.includes('admin')||roles.includes('superadmin')" in source
    assert "systemSection.hidden=true" in source
    assert "link('/lawyer/workspace/ui'" in source
    assert "link('/lawyer/consultation-desk/ui'" in source
    assert "link('/admin/workdesk/ui'" in source
    assert "link('/message-center/ui'" in source
    assert "BOT_TOKEN" not in source
    assert "ADMIN_PASSWORD" not in source


def test_operator_hub_publishes_primary_staff_workspaces():
    source = inspect.getsource(operator_page)

    for path in (
        "/admin/workdesk/ui",
        "/lawyer/workspace/ui",
        "/lawyer/consultation-desk/ui",
        "/document-access/review/ui",
        "/message-center/ui",
        "/admin/notification-delivery/ui",
    ):
        assert path in source


def test_primary_and_compatibility_staff_workspace_links_are_registered_once():
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
        "/admin-ui",
        "/lawyer/ui",
    }

    missing = sorted(path for path in required if counts[path] == 0)
    duplicated = {path: counts[path] for path in required if counts[path] > 1}

    assert missing == [], f"operator links contain unregistered workspaces: {missing}"
    assert duplicated == {}, f"operator links are registered more than once: {duplicated}"
