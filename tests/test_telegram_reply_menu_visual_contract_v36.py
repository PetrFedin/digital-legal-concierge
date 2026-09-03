from app.bot.keyboards import reply_main_menu


def _texts(markup):
    return [[button.text for button in row] for row in markup.keyboard]


def _flat(markup):
    return sum(_texts(markup), [])


EXPECTED_CANONICAL_REPLY_MENU = [
    ["🏠 Главная", "🧮 Рассчитать неустойку"],
    ["📁 Моё дело", "📄 Документы"],
    ["💬 Связаться с юристом"],
]


def test_reply_menu_is_stable_for_new_active_and_completed_contexts():
    # Persistent navigation must not jump around as the Case changes stage.
    # Destination screens explain availability and ambiguity; the keyboard stays
    # compact and predictable for the client.
    assert _texts(reply_main_menu(False)) == EXPECTED_CANONICAL_REPLY_MENU
    assert _texts(reply_main_menu(True)) == EXPECTED_CANONICAL_REPLY_MENU
    assert (
        _texts(reply_main_menu(False, completed_case=True))
        == EXPECTED_CANONICAL_REPLY_MENU
    )


def test_reply_menu_keeps_only_live_global_navigation_actions():
    menu = reply_main_menu(True)
    labels = _flat(menu)

    assert labels == [
        "🏠 Главная",
        "🧮 Рассчитать неустойку",
        "📁 Моё дело",
        "📄 Документы",
        "💬 Связаться с юристом",
    ]
    # Payments, history and message-composer actions are contextual inline
    # actions. Keeping them out of the persistent keyboard avoids stale Case
    # mutations and keeps the global navigation compact.
    assert "💳 Оплаты" not in labels
    assert "✉️ Новый вопрос" not in labels
    assert "💬 Переписка" not in labels


def test_completed_reply_menu_reuses_live_calculator_trigger():
    menu = reply_main_menu(False, completed_case=True)
    labels = _flat(menu)

    # The calculator label intentionally matches the live message handler, while
    # the destination screen explains that a new calculation creates a separate
    # matter and leaves the archive unchanged.
    assert "🧮 Рассчитать неустойку" in labels
    assert "🧮 Новое обращение" not in labels
