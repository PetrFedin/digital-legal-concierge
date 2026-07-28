from __future__ import annotations

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "callback_data",
    [
        "consult_slot_open",
        "consult_date:20261231",
        "consult_slot_select:9223372036854775807",
        "consult_reschedule",
        "consult_reschedule_date:20261231",
        "consult_reschedule_slot:9223372036854775807",
        "consult_cancel",
        "consult_cancel_confirm",
        "consultation_booked_open",
        "pay_open:9223372036854775807",
        "my_case_open",
    ],
)
def test_client_callback_contract_fits_telegram_limit(callback_data):
    assert len(callback_data.encode("utf-8")) <= 64


def test_bot_uses_plain_text_without_global_parse_mode():
    source = Path("app/bot/bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    bot_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Bot"
    ]
    assert bot_calls
    assert all(
        all(keyword.arg not in {"parse_mode", "default"} for keyword in call.keywords)
        for call in bot_calls
    )
    assert "DefaultBotProperties" not in source
    assert "ParseMode" not in source
