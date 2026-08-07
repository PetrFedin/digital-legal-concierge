from pathlib import Path

from app.bot.keyboards import main_menu


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_new_client_home_has_two_clear_entry_points():
    markup = main_menu(case_exists=False, payments_enabled=False)

    assert callbacks(markup) == ["calc_start", "contact_lawyer"]
    assert texts(markup) == [
        "🧮 Рассчитать неустойку",
        "💬 Связаться с юристом",
    ]
    assert [len(row) for row in markup.inline_keyboard] == [1, 1]


def test_active_case_home_starts_with_snapshot_safe_primary_action():
    markup = main_menu(
        case_exists=True,
        payments_enabled=False,
        primary_action=(
            "▶️ Передать документы юристу",
            "next_action:v2:17:abc123",
        ),
    )

    assert callbacks(markup) == [
        "next_action:v2:17:abc123",
        "my_case_open",
        "documents_open",
        "message_history",
        "message_create",
        "contact_lawyer",
    ]
    assert texts(markup)[0] == "▶️ Передать документы юристу"
    assert "calc_start" not in callbacks(markup)
    assert [len(row) for row in markup.inline_keyboard] == [1, 2, 2, 1]


def test_home_uses_shared_case_presenter_and_direct_next_action():
    source = read("app/bot/screens/common.py")

    assert "load_client_case_view" in source
    assert "progress_bar(view.progress_percent)" in source
    assert "format_updated_at(view.updated_at)" in source
    assert "📌 Ваш следующий шаг" in source
    assert 'f"next_action:v2:{view.case_id}:{view.action_key}"' in source
    assert "Главная кнопка ниже ведёт к самому актуальному действию" in source
    assert "primary_action=primary_action" in source


def test_reply_calculator_entry_recovers_to_active_case():
    source = read("app/bot/screens/common.py")

    handler = source[source.index("async def menu_calc"): source.index("async def menu_my_case")]
    assert "get_active_case_for_user" in handler
    assert "Чтобы не смешивать расчёты, документы и статусы" in handler
    assert "_result_view_for_case" in handler
    assert "CONSULTATION_RESULT_ACTION if result_view else _primary_action(view)" in handler
    assert "primary_action=primary_action" in handler
    assert "return" in handler


def test_stale_inline_calculator_button_cannot_overwrite_active_case():
    source = read("app/bot/screens/calculator.py")

    handler = source[source.index("async def calc_start"): source.index("async def price")]
    active_case_check = handler.index("get_active_case_for_user")
    state_start = handler.index("set_state(CalculatorStates.waiting_contract_price)")
    assert active_case_check < state_start
    assert "load_client_case_view" in handler
    assert 'f"next_action:v2:{view.case_id}:{view.action_key}"' in handler
    assert "Новый расчёт станет доступен после" in handler
    assert "await state.clear()" in handler
