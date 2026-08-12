from app.bot.keyboards import main_menu


def _rows(markup):
    return [
        [(button.text, button.callback_data) for button in row]
        for row in markup.inline_keyboard
    ]


def _callbacks(markup):
    return [callback for row in _rows(markup) for _text, callback in row]


def test_active_inline_menu_groups_context_communication_and_help():
    assert _rows(main_menu(True)) == [
        [
            ("📁 Моё дело", "my_case_open"),
            ("📄 Документы", "documents_open"),
        ],
        [
            ("💬 Переписка", "message_history"),
            ("💳 Оплаты", "payments_open"),
        ],
        [
            ("✉️ Новый вопрос", "message_create"),
            ("⚖️ Юрист / консультация", "contact_lawyer"),
        ],
    ]


def test_primary_action_stays_single_and_visually_first():
    markup = main_menu(
        True,
        primary_action=("▶️ Продолжить", "next_action:v2:42:abc"),
    )
    rows = _rows(markup)
    assert rows[0] == [("▶️ Продолжить", "next_action:v2:42:abc")]
    assert _callbacks(markup).count("next_action:v2:42:abc") == 1
    assert rows[1][0] == ("📁 Моё дело", "my_case_open")


def test_specialized_screen_can_hide_payment_shortcut_without_breaking_navigation():
    markup = main_menu(True, payments_enabled=False)
    rows = _rows(markup)
    assert "payments_open" not in _callbacks(markup)
    assert rows == [
        [
            ("📁 Моё дело", "my_case_open"),
            ("📄 Документы", "documents_open"),
        ],
        [("💬 Переписка", "message_history")],
        [
            ("✉️ Новый вопрос", "message_create"),
            ("⚖️ Юрист / консультация", "contact_lawyer"),
        ],
    ]
