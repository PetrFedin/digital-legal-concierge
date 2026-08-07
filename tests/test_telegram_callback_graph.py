from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_ROOT = ROOT / "app" / "bot"
CALLBACK_VALUE = re.compile(r"^[a-z][a-z0-9_.:-]*$")


@dataclass(frozen=True, order=True)
class CallbackPattern:
    kind: str
    value: str
    source: str


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _contains_data_reference(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr == "data":
            return True
    return False


def _literal_strings(node: ast.AST) -> set[str]:
    values: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.add(child.value)
    return values


def _callback_pattern(node: ast.AST, source: str) -> CallbackPattern | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if CALLBACK_VALUE.fullmatch(node.value):
            return CallbackPattern("exact", node.value, source)
        return None

    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
                continue
            break
        prefix = "".join(parts)
        if prefix and CALLBACK_VALUE.fullmatch(prefix):
            return CallbackPattern("prefix", prefix, source)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            prefix = node.left.value
            if prefix and CALLBACK_VALUE.fullmatch(prefix):
                return CallbackPattern("prefix", prefix, source)

    return None


def _assignment_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets: list[ast.AST]
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    else:
        targets = [node.target]
    names: set[str] = set()
    for target in targets:
        if isinstance(target, ast.Name):
            names.add(target.id)
    return names


def _collect_emitted_callbacks(path: Path) -> set[CallbackPattern]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relative = path.relative_to(ROOT).as_posix()
    emitted: set[CallbackPattern] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg != "callback_data":
                    continue
                pattern = _callback_pattern(
                    keyword.value,
                    f"{relative}:{getattr(node, 'lineno', '?')}",
                )
                if pattern:
                    emitted.add(pattern)

            if _call_name(node.func) == "one":
                for arg in node.args:
                    if not isinstance(arg, ast.Tuple) or len(arg.elts) < 2:
                        continue
                    pattern = _callback_pattern(
                        arg.elts[1],
                        f"{relative}:{getattr(arg, 'lineno', '?')}",
                    )
                    if pattern:
                        emitted.add(pattern)

        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = _assignment_names(node)
            if not any(name.endswith("_ACTION") for name in names):
                continue
            value = node.value
            if isinstance(value, ast.Tuple) and len(value.elts) >= 2:
                pattern = _callback_pattern(
                    value.elts[1],
                    f"{relative}:{getattr(node, 'lineno', '?')}",
                )
                if pattern:
                    emitted.add(pattern)

    return emitted


def _collect_handler_patterns(path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    exact: set[str] = set()
    prefixes: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        callback_decorators = [
            decorator
            for decorator in node.decorator_list
            if any(
                isinstance(child, ast.Attribute) and child.attr == "callback_query"
                for child in ast.walk(decorator)
            )
        ]
        for decorator in callback_decorators:
            for child in ast.walk(decorator):
                if isinstance(child, ast.Compare) and _contains_data_reference(child):
                    for text in _literal_strings(child):
                        if CALLBACK_VALUE.fullmatch(text):
                            exact.add(text)

                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if child.func.attr == "startswith" and _contains_data_reference(child.func.value):
                        for arg in child.args:
                            for text in _literal_strings(arg):
                                if CALLBACK_VALUE.fullmatch(text):
                                    prefixes.add(text)
                    elif child.func.attr in {"in_", "in"} and _contains_data_reference(child.func.value):
                        for arg in child.args:
                            for text in _literal_strings(arg):
                                if CALLBACK_VALUE.fullmatch(text):
                                    exact.add(text)

    return exact, prefixes


def _is_handled(pattern: CallbackPattern, exact: set[str], prefixes: set[str]) -> bool:
    if pattern.kind == "exact":
        return pattern.value in exact or any(pattern.value.startswith(prefix) for prefix in prefixes)
    return any(pattern.value.startswith(prefix) or prefix.startswith(pattern.value) for prefix in prefixes)


def test_every_user_visible_telegram_callback_has_a_registered_route():
    python_files = sorted(BOT_ROOT.rglob("*.py"))
    emitted: set[CallbackPattern] = set()
    exact_handlers: set[str] = set()
    prefix_handlers: set[str] = set()

    for path in python_files:
        emitted.update(_collect_emitted_callbacks(path))
        exact, prefixes = _collect_handler_patterns(path)
        exact_handlers.update(exact)
        prefix_handlers.update(prefixes)

    # Guard against a broken extractor silently turning the quality gate into a no-op.
    assert len(emitted) >= 25, f"Callback extractor found only {len(emitted)} emitted actions"
    assert len(exact_handlers) + len(prefix_handlers) >= 20, "No meaningful callback routes discovered"

    unresolved = sorted(
        pattern
        for pattern in emitted
        if not _is_handled(pattern, exact_handlers, prefix_handlers)
    )
    details = "\n".join(
        f"- {item.kind}:{item.value} emitted at {item.source}"
        for item in unresolved
    )
    assert not unresolved, (
        "Telegram UI exposes callback buttons without a registered exact/prefix route. "
        "Each visible button must finish in a handler or an explicit recovery path.\n"
        f"{details}"
    )
