import pytest

from app.api.operator import operator_page
from app.bot.keyboards import main_menu, reply_main_menu


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_reply_menu_prioritizes_calculation_and_keeps_stable_labels():
    markup = reply_main_menu()
    assert markup.is_persistent is True
    assert markup.keyboard[0][0].text == "🧮 Рассчитать неустойку"
    assert "📁 Мое дело" in [button.text for row in markup.keyboard for button in row]
    assert markup.input_field_placeholder == (
        "Выберите: расчет, дело, документы или помощь"
    )


def test_inline_menu_hides_disabled_payments_without_breaking_other_callbacks():
    markup = main_menu(case_exists=True, payments_enabled=False)
    assert "payments_open" not in _callbacks(markup)
    assert _callbacks(markup) == [
        "calc_start",
        "my_case_open",
        "documents_open",
        "contact_lawyer",
    ]
    assert [len(row) for row in markup.inline_keyboard] == [1, 2, 1]


def test_inline_menu_shows_payments_when_provider_is_enabled():
    markup = main_menu(case_exists=True, payments_enabled=True)
    assert "💳 Оплаты" in _texts(markup)
    assert "payments_open" in _callbacks(markup)
    assert [len(row) for row in markup.inline_keyboard] == [1, 2, 1, 1]


@pytest.mark.asyncio
async def test_operator_page_is_grouped_for_daily_work_and_does_not_show_secrets():
    response = await operator_page()
    body = response.body.decode("utf-8")
    assert "Рабочее пространство" in body
    assert "Ежедневная работа" in body
    assert "Контроль и безопасность" in body
    assert "Настройка системы" in body
    assert "Техническое обслуживание" in body
    assert 'href="/admin-ui"' in body
    assert 'href="/lawyer/ui"' in body
    assert "BOT_TOKEN" not in body
    assert "ADMIN_PASSWORD" not in body
