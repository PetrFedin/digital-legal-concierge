from app.bot.keyboards import (
    ACTIVE_CASE_REPLY_MENU_BUTTONS,
    COMPLETED_CASE_REPLY_MENU_BUTTONS,
    reply_main_menu,
)


def _texts(markup):
    return [[button.text for button in row] for row in markup.keyboard]


def test_active_reply_menu_puts_case_and_payments_first():
    assert _texts(reply_main_menu(True)) == [
        ["📁 Моё дело", "💳 Оплаты"],
        ["📄 Документы", "💬 Переписка"],
        ["✉️ Новый вопрос"],
        ["🏠 Главная"],
    ]
    assert ACTIVE_CASE_REPLY_MENU_BUTTONS == reply_main_menu(True).keyboard


def test_completed_reply_menu_is_read_only_but_keeps_financial_history():
    assert _texts(reply_main_menu(False, completed_case=True)) == [
        ["📁 Моё дело", "💳 Оплаты"],
        ["🧮 Новое обращение"],
        ["🏠 Главная"],
    ]
    assert "📄 Документы" not in sum(_texts(reply_main_menu(False, completed_case=True)), [])
    assert "💬 Переписка" not in sum(_texts(reply_main_menu(False, completed_case=True)), [])
    assert COMPLETED_CASE_REPLY_MENU_BUTTONS == reply_main_menu(
        False,
        completed_case=True,
    ).keyboard
