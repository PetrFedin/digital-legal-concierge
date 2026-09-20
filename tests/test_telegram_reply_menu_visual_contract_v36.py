from app.bot.keyboards import reply_main_menu


def _texts(markup):
    return [[button.text for button in row] for row in markup.keyboard]


def _flat(markup):
    return sum(_texts(markup), [])


EXPECTED_NEW_REPLY_MENU = [
    ["🏠 Главная", "🧮 Рассчитать неустойку"],
    ["💬 Связаться с юристом"],
]
EXPECTED_ACTIVE_REPLY_MENU = [
    ["🏠 Главная", "🧮 Рассчитать неустойку"],
    ["📁 Моё дело", "📄 Документы"],
    ["💬 Связаться с юристом"],
]
EXPECTED_COMPLETED_REPLY_MENU = [
    ["🏠 Главная", "🧮 Рассчитать неустойку"],
    ["📁 Моё дело"],
    ["💬 Связаться с юристом"],
]


def test_reply_menu_visibility_tracks_client_context():
    # The approved UX contract keeps Home/Calculator available globally,
    # exposes My Case/Documents only after a Case exists, keeps the read-only
    # archive after completion, and preserves Contact Lawyer as the global M2/help entry.
    assert _texts(reply_main_menu(False)) == EXPECTED_NEW_REPLY_MENU
    assert _texts(reply_main_menu(True)) == EXPECTED_ACTIVE_REPLY_MENU
    assert (
        _texts(reply_main_menu(False, completed_case=True))
        == EXPECTED_COMPLETED_REPLY_MENU
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
