from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class ArchitectureCheckError(RuntimeError):
    pass


def python_files():
    yield from APP.rglob("*.py")


def check_case_status_writes() -> list[str]:
    errors = []
    allowed = APP / "domain" / "cases" / "case_service.py"
    for path in python_files():
        if path == allowed:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            errors.append(f"{path.relative_to(ROOT)}: syntax error: {error}")
            continue
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                if isinstance(node, ast.Assign):
                    targets = node.targets
                else:
                    targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "status"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "case"
                ):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        "case.status must be changed through CaseService"
                    )
    return errors


def check_forced_transition_bypasses() -> list[str]:
    errors = []
    allowed = APP / "api" / "admin.py"
    for path in python_files():
        if path == allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "force"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        "force=True bypass is reserved for admin correction"
                    )
    return errors


def check_dead_bot_callbacks() -> list[str]:
    errors = []
    for path in (APP / "bot" / "screens").glob("*.py"):
        if path.name == "common.py":
            continue
        text = path.read_text(encoding="utf-8")
        if '"noop"' in text or "'noop'" in text:
            errors.append(
                f"{path.relative_to(ROOT)}: dead noop callback exposed to a user"
            )
    return errors


def check_calculator_clock_boundary() -> list[str]:
    path = APP / "domain" / "calculator" / "penalty_calculator.py"
    text = path.read_text(encoding="utf-8")
    return (
        [f"{path.relative_to(ROOT)}: calculator domain must not call date.today()"]
        if "date.today()" in text
        else []
    )


def check_runtime_routes() -> list[str]:
    from app.main import create_app

    seen = defaultdict(list)
    for route in create_app().routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            seen[(method, path)].append(getattr(route, "name", "<unnamed>"))
    return [
        f"duplicate route {method} {path}: {', '.join(names)}"
        for (method, path), names in sorted(seen.items())
        if len(names) > 1
    ]


def main() -> None:
    from app.domain.cases.case_transition_policy import assert_policy_complete

    assert_policy_complete()
    errors = [
        *check_case_status_writes(),
        *check_forced_transition_bypasses(),
        *check_dead_bot_callbacks(),
        *check_calculator_clock_boundary(),
        *check_runtime_routes(),
    ]
    if errors:
        raise ArchitectureCheckError("\n".join(errors))
    print("architecture quality gate: OK")


if __name__ == "__main__":
    main()
