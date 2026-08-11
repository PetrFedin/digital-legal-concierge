from app.bot.keyboards import reply_main_menu


def _texts(markup):
    return [[button.text for button in row] for row in markup.keyboard]


def _flat(markup):
    return sum(_texts(markup), [])


def test_active_reply_menu_has_no_dead_payment_button():
    assert _texts(reply_main_menu(True)) == [
        ["📁 Моё дело", "📄 Документы"],
        ["💬 Переписка", "✉️ Новый вопрос"],
        ["🏠 Главная"],
    ]
    assert "💳 Оплаты" not in _flat(reply_main_menu(True))


def test_completed_reply_menu_is_read_only_and_has_no_dead_actions():
    menu = reply_main_menu(False, completed_case=True)
    assert _texts(menu) == [
        ["📁 Моё дело", "🧮 Новое обращение"],
        ["🏠 Главная"],
    ]
    assert "📄 Документы" not in _flat(menu)
    assert "💬 Переписка" not in _flat(menu)
    assert "💳 Оплаты" not in _flat(menu)
