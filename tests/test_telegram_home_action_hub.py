import asyncio
from pathlib import Path

from app.bot.keyboards import main_menu, reply_main_menu
from app.bot.screens.common import _has_unsent_message_draft


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


def reply_texts(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


class DraftState:
    def __init__(self, data: dict[str, object]):
        self.data = data

    async def get_data(self) -> dict[str, object]:
        return self.data


def test_new_client_home_has_two_clear_entry_points():
    markup = main_menu(case_exists=False, payments_enabled=False)

    assert callbacks(markup) == ["calc_start", "contact_lawyer"]
    assert texts(markup) == [
        "🧮 Рассчитать неустойку",
        "💬 Связаться с юристом",
    ]
    assert [len(row) for row in markup.inline_keyboard] == [1, 1]


def test_new_client_persistent_menu_has_no_dead_case_sections():
    markup = reply_main_menu(False)

    assert reply_texts(markup) == [
        "🧮 Рассчитать неустойку",
        "💬 Связаться с юристом",
        "🏠 Главная",
    ]
    assert "📁 Моё дело" not in reply_texts(markup)
    assert "📄 Документы" not in reply_texts(markup)
    assert "💬 Переписка" not in reply_texts(markup)
    assert "✉️ Новый вопрос" not in reply_texts(markup)
    assert markup.input_field_placeholder == "Выберите: расчёт или помощь юриста"


def test_active_client_persistent_menu_exposes_only_case_work():
    markup = reply_main_menu(True)

    assert reply_texts(markup) == [
        "📁 Моё дело",
        "📄 Документы",
        "💬 Переписка",
        "✉️ Новый вопрос",
        "🏠 Главная",
    ]
    assert "🧮 Рассчитать неустойку" not in reply_texts(markup)
    assert markup.input_field_placeholder == "Выберите: дело, документы или переписка"


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
    assert texts(markup)[1] == "📁 Моё дело"
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


def test_unsent_question_draft_is_detected_before_global_navigation():
    assert asyncio.run(_has_unsent_message_draft(DraftState({"draft_text": "Важный вопрос"})))
    assert not asyncio.run(_has_unsent_message_draft(DraftState({"draft_text": "  "})))
    assert not asyncio.run(_has_unsent_message_draft(DraftState({})))

    source = read("app/bot/screens/common.py")
    start_handler = source[
        source.index("async def start"): source.index("async def menu_calc")
    ]
    home_handler = source[
        source.index("async def home("): source.index("async def noop")
    ]
    assert start_handler.index("_guard_message_draft") < start_handler.index("state.clear")
    assert home_handler.index("_guard_callback_draft") < home_handler.index("state.clear")
    assert source.count("if await _guard_message_draft(message, state):") >= 7
    assert source.count("if await _guard_callback_draft(callback, state):") >= 4
    assert "Я не закрываю его автоматически" in source
    assert '("↩️ Вернуться к черновику", "message_review_return")' in source
    assert '("✖️ Отменить черновик", "message_discard_confirm")' in source


def test_my_case_is_visual_action_hub_and_no_case_recovers_via_contact_router():
    source = read("app/bot/screens/my_case.py")

    assert "📁 МОЁ ДЕЛО" in source
    assert "СЕЙЧАС" in source
    assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in source
    assert "⚠️ ЧТО МЕШАЕТ ПРОДОЛЖИТЬ" in source
    assert "ГОТОВНОСТЬ" in source
    assert "Первая кнопка ниже — самое актуальное безопасное действие." in source
    assert '("💬 Связаться с юристом", "contact_lawyer")' in source
    assert '("💬 Записаться на консультацию", "calc_to_m2")' not in source
    assert 'f"next_action:v2:{view.case_id}:{view.action_key}"' in source


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
