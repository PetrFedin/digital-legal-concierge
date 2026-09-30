import ast
from pathlib import Path


SCREENS_DIR = Path("app/bot/screens")

CRITICAL_EXACT_CALLBACKS = {
    "contact_lawyer",
    "consultation_booked_open",
    "consultation_result_open",
    "message_create",
    "message_history",
    "my_case_open",
    "documents_open",
    "payments_open",
    "case_history_open",
    "preview_calc_start",
}

CRITICAL_PREFIX_CALLBACKS = {
    "preview_calc_save:v2:",
}


def _handlers():
    result: list[tuple[Path, str, str]] = []
    for path in sorted(SCREENS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if getattr(decorator.func, "attr", None) != "callback_query":
                    continue
                segment = ast.get_source_segment(source, decorator) or ""
                result.append((path, node.name, segment))
    return result


def _mentions_exact(segment: str, callback: str) -> bool:
    token = repr(callback)
    return token in segment or f'"{callback}"' in segment or f"'{callback}'" in segment


def _mentions_prefix(segment: str, prefix: str) -> bool:
    return prefix in segment and "startswith" in segment


def test_critical_exact_callbacks_have_single_handler_owner():
    handlers = _handlers()
    ownership: dict[str, list[tuple[Path, str]]] = {callback: [] for callback in CRITICAL_EXACT_CALLBACKS}
    for path, function_name, decorator in handlers:
        for callback in CRITICAL_EXACT_CALLBACKS:
            if _mentions_exact(decorator, callback):
                ownership[callback].append((path, function_name))

    duplicates = {
        callback: [(str(path), function_name) for path, function_name in owners]
        for callback, owners in ownership.items()
        if len(owners) != 1
    }
    assert not duplicates, f"Critical Telegram callback ownership must be unique: {duplicates}"


def test_critical_prefix_callbacks_have_single_handler_owner():
    handlers = _handlers()
    ownership: dict[str, list[tuple[Path, str]]] = {
        prefix: [] for prefix in CRITICAL_PREFIX_CALLBACKS
    }
    for path, function_name, decorator in handlers:
        for prefix in CRITICAL_PREFIX_CALLBACKS:
            if _mentions_prefix(decorator, prefix):
                ownership[prefix].append((path, function_name))

    duplicates = {
        prefix: [(str(path), function_name) for path, function_name in owners]
        for prefix, owners in ownership.items()
        if len(owners) != 1
    }
    assert not duplicates, (
        "Critical Telegram callback-prefix ownership must be unique: "
        f"{duplicates}"
    )
