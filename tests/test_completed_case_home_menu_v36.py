from pathlib import Path

from app.bot.keyboards import main_menu, reply_main_menu


def reply_button_texts(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def inline_button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def inline_callback_data(markup) -> list[str | None]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_completed_reply_menu_is_read_only_archive_navigation():
    markup = reply_main_menu(False, completed_case=True)
    texts = reply_button_texts(markup)

    assert "📁 Моё дело" in texts
    assert "🧮 Рассчитать неустойку" in texts
    assert "🏠 Главная" in texts
    assert "📄 Документы" not in texts
    assert "💬 Переписка" not in texts
    assert "✉️ Новый вопрос" not in texts
    assert "💬 Связаться с юристом" not in texts


def test_completed_inline_home_has_archive_and_new_case_without_active_actions():
    markup = main_menu(
        False,
        completed_case=True,
        primary_action=("👨‍⚖ Открыть итог консультации", "consultation_result_open"),
    )
    texts = inline_button_texts(markup)

    assert "👨‍⚖ Открыть итог консультации" in texts
    assert "📁 Моё дело" in texts
    assert "🧮 Новое обращение" in texts
    assert "📄 Документы" not in texts
    assert "✉️ Задать вопрос по делу" not in texts
    assert "⚖️ Связь и помощь" not in texts


def test_active_inline_home_keeps_payment_history_visible_by_default():
    markup = main_menu(True)
    assert "💳 Оплаты" in inline_button_texts(markup)
    assert "payments_open" in inline_callback_data(markup)

    specialized = main_menu(True, payments_enabled=False)
    assert "💳 Оплаты" not in inline_button_texts(specialized)
    assert "payments_open" not in inline_callback_data(specialized)

    source = Path("app/bot/keyboards.py").read_text(encoding="utf-8")
    assert "payment provider mode" in source.lower()
    assert "settings.payment_provider" not in source


def test_common_home_copy_is_route_aware_for_completed_m1_and_m2():
    source = Path("app/bot/screens/common.py").read_text(encoding="utf-8")

    assert "latest_completed_case_for_user" in source
    assert "✅ Последняя консультация завершена" in source
    assert "Консультационный маршрут завершён" in source
    assert "Финальный платёж подтверждён, финансовый этап завершён" in source
    assert "completed_case=completed_case" in source
    assert "COMPLETED_M1_ACTION" not in source
