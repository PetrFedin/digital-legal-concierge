from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.bot.client_activity import context_free_activity


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_home_is_neutral_and_requires_explicit_resume():
    common = read("app/bot/screens/common.py")

    start = common.index('@router.message(lambda m: m.text in ["/start", "/menu", "🏠 Главная"])')
    end = common.index('@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")', start)
    handler = common[start:end]

    assert "_fresh_start_state" in handler
    assert "_fresh_start_text" in handler
    assert "_fresh_start_markup" in handler
    assert "_home_text" not in handler
    assert '"🧮 Быстрый расчёт без сохранения", "preview_calc_start"' in common
    assert '"▶️ Продолжить общение", "my_cases_open"' in common
    assert '"▶️ Продолжить общение", "my_case_open"' in common
    assert "Я не открываю их автоматически" in common


def test_preview_steps_are_isolated_from_durable_case_calculator():
    preview = read("app/bot/screens/calculator_preview.py")
    states = read("app/bot/states.py")
    bot = read("app/bot/bot.py")

    assert "class PreviewCalculatorStates" in states
    assert "calculator_preview.router" in bot
    assert bot.index("calculator_preview.router") < bot.index("common.router")
    assert "👀 БЫСТРЫЙ РАСЧЁТ · БЕЗ СОХРАНЕНИЯ" in preview

    pre_save = preview[: preview.index('@router.callback_query(lambda c: c.data == "preview_calc_save")')]
    # Reading the current production rule is allowed. Persistent Case/intake/result
    # materialization is deliberately owned only by preview_save below this split.
    assert "create_case_from_callback(" not in pre_save
    assert "calculate_and_save(" not in pre_save
    assert "sync_from_draft(" not in pre_save
    assert "CaseCreationRequest(" not in pre_save


def test_preview_save_is_explicit_idempotent_materialization_bound_to_exact_rules():
    preview = read("app/bot/screens/calculator_preview.py")
    calculator = read("app/domain/calculator/calculator_service.py")

    assert 'purpose="calculator_preview_save"' in preview
    assert "CaseCreationRequest.client_id" in preview
    assert 'return f"calculator_preview_save:{clean}"' in preview
    assert 'f"preview_calc_save:v2:{preview_id}"' in preview
    assert "operation_key=_preview_operation_key(preview_id)" in preview
    assert 'f"telegram_callback:{callback_id}"' not in preview
    assert "expected_rule_revision_key=preview_key" in preview
    assert "expected_rule_snapshot_sha256=preview_sha" in preview
    assert "Версия юридических правил изменилась после предварительного просмотра" in calculator
    assert "Содержимое юридических правил изменилось после предварительного просмотра" in calculator
    assert "result_for_case" in calculator


def test_distinct_callback_ids_from_one_preview_share_one_materialization_key():
    preview = read("app/bot/screens/calculator_preview.py")

    save = preview.split("async def preview_save", 1)[1]
    assert "callback.id" not in save
    assert "callback_preview_id = _preview_id_from_callback(callback.data)" in save
    assert "state_preview_id = _valid_preview_id" in save
    assert "preview_id = callback_preview_id or state_preview_id" in save
    assert "existing_case = await _existing_materialized_case" in save
    assert "operation_key=_preview_operation_key(preview_id)" in save
    assert "state_preview_id != preview_id" in save


def test_preview_commit_precedes_fsm_clear_so_failed_commit_keeps_unsaved_draft():
    preview = read("app/bot/screens/calculator_preview.py")
    present = preview.split("async def _present_saved", 1)[1].split(
        "@router.callback_query", 1
    )[0]

    assert present.index("await db.commit()") < present.index("await state.clear()")


def test_canonical_new_calculation_buttons_use_preview_not_case_creation():
    keyboard = read("app/bot/keyboards.py")
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '"preview_calc_start"' in keyboard
    assert 'secondary("🧮 Рассчитать неустойку", "preview_calc_start")' in keyboard
    assert 'secondary("🧮 Новый расчёт", "preview_calc_start")' in keyboard
    assert '"▶️ Начать быстрый расчёт", "preview_calc_start"' in direct

    direct_start = direct.index('@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")')
    direct_end = direct.index('@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})', direct_start)
    direct_handler = direct[direct_start:direct_end]
    assert "create_case" not in direct_handler
    assert "_home_text" not in direct_handler


def test_neutral_home_and_preview_do_not_refresh_selected_case_activity():
    assert context_free_activity(
        SimpleNamespace(text="/start", data=None),
        state_name=None,
    )
    assert context_free_activity(
        SimpleNamespace(text="🏠 Главная", data=None),
        state_name=None,
    )
    assert context_free_activity(
        SimpleNamespace(text=None, data="preview_calc_start"),
        state_name=None,
    )
    assert context_free_activity(
        SimpleNamespace(text="8500000", data=None),
        state_name="PreviewCalculatorStates:waiting_contract_price",
    )
    assert not context_free_activity(
        SimpleNamespace(text=None, data="preview_calc_save"),
        state_name=None,
    )
    assert not context_free_activity(
        SimpleNamespace(
            text=None,
            data="preview_calc_save:v2:0123456789abcdef0123456789abcdef",
        ),
        state_name=None,
    )


def test_preview_unknown_legal_facts_fail_closed_without_creating_case():
    preview = read("app/bot/screens/calculator_preview.py")

    assert "preview_client_unknown" in preview
    assert "preview_unique_unknown" in preview
    assert "бот не создаёт обращение и не угадывает ответ" in preview
    assert "Бот не подставляет «нет» по умолчанию" in preview


def test_preview_result_m1_action_remains_eligibility_gated():
    preview = read("app/bot/screens/calculator_preview.py")

    assert "int(result.delay_days or 0) > 0" in preview
    assert "Decimal(result.penalty_amount or 0) > 0" in preview
    assert 'f"calc_continue_m1:v2:{case_id}"' in preview
