from __future__ import annotations

import pytest

from app.bot.screens.consultation_hold import _slot_id_from_callback


@pytest.mark.parametrize(
    "callback_data,expected",
    [
        ("consult_select:slot:1", 1),
        ("consult_select:slot:9223372036854775807", 9223372036854775807),
        ("consult_select:slot:42:stale-extra-data", 42),
        ("consult_slot_select:7", 7),
        ("consult_slot_select:99:legacy-extra-data", 99),
    ],
)
def test_slot_callback_parser_accepts_current_and_historical_formats(
    callback_data,
    expected,
):
    assert _slot_id_from_callback(callback_data) == expected


@pytest.mark.parametrize(
    "callback_data",
    [
        "consult_select:slot:",
        "consult_select:slot:0",
        "consult_select:slot:-1",
        "consult_select:slot:not-a-number",
        "consult_slot_select:",
        "other:slot:1",
    ],
)
def test_slot_callback_parser_rejects_invalid_references(callback_data):
    with pytest.raises(ValueError):
        _slot_id_from_callback(callback_data)
