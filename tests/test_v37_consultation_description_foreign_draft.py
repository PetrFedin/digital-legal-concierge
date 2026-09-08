from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_description_entry_discards_draft_from_another_case_before_handler():
    source = read("app/bot/consultation_description_provenance.py")

    helper = source.split("async def _discard_foreign_entry_draft", 1)[1].split(
        "async def _validate_snapshot", 1
    )[0]
    assert 'snapshot.get("consult_description_case_id")' in helper
    assert 'snapshot.get("description_draft")' in helper
    assert "previous_case_id != target" in helper
    assert "await state.clear()" in helper

    entry = source.split("if isinstance(event, CallbackQuery) and entry_action is not None:", 1)[1].split(
        "if isinstance(event, CallbackQuery) and _is_description_flow_callback", 1
    )[0]
    assert entry.index("_discard_foreign_entry_draft") < entry.index(
        "result = await handler(event, data)"
    )
