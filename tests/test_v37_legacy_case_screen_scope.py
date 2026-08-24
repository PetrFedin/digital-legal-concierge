from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_case_scope_recognizes_canonical_case_number_on_legacy_bot_screen():
    source = read("app/bot/case_callback_scope.py")

    assert '_CASE_NUMBER_PATTERN = re.compile(r"\\bDLC-\\d{4}-\\d{6}\\b")' in source
    assert "def _legacy_message_case_numbers" in source
    assert "_CASE_NUMBER_PATTERN.findall" in source


def test_legacy_screen_for_another_case_fails_closed_even_with_one_active_case():
    source = read("app/bot/case_callback_scope.py")

    resolver = source.split("async def resolve_case_callback_scope", 1)[1]
    conflict_index = resolver.index("if legacy_message_conflict:")
    multi_case_index = resolver.index("if legacy_unbound and len(active_cases) > 1")
    assert conflict_index < multi_case_index
    assert "_legacy_message_names_other_case(callback, selected_case)" in resolver
    assert "старый экран не может быть перенесён в новый контекст автоматически" in resolver
    assert '"my_case_open"' in resolver
    assert "return None" in resolver[conflict_index:multi_case_index]


def test_message_entry_is_part_of_case_bound_mutating_actions():
    source = read("app/bot/case_callback_scope.py")

    actions = source.split("CASE_BOUND_MUTATING_ACTIONS", 1)[1].split(")", 1)[0]
    assert '"message_create"' in actions
