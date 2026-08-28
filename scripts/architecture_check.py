from __future__ import annotations

import ast
import warnings
from collections import defaultdict
from pathlib import Path

from sqlalchemy.exc import SAWarning


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class ArchitectureCheckError(RuntimeError):
    pass


def python_files():
    yield from APP.rglob("*.py")


def _assignment_targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return [node.target]
    return []


def _root_name(node: ast.AST | None) -> str | None:
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _contains_name(node: ast.AST, value: str) -> bool:
    return any(isinstance(item, ast.Name) and item.id == value for item in ast.walk(node))


def _package_name_for_path(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    else:
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative_module(*, package: str, module: str, level: int) -> str:
    if level <= 0:
        return module
    package_parts = [part for part in package.split(".") if part]
    ascend = level - 1
    if ascend > len(package_parts):
        return ""
    resolved = package_parts[: len(package_parts) - ascend]
    if module:
        resolved.extend(part for part in module.split(".") if part)
    return ".".join(resolved)


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    module = node.module or ""
    if node.level <= 0:
        return module
    return _resolve_relative_module(
        package=_package_name_for_path(path),
        module=module,
        level=node.level,
    )


def _is_legacy_assignment_module(module: str) -> bool:
    forbidden = "app.domain.assignment"
    return module == forbidden or module.startswith(forbidden + ".")


def _constant_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _constant_int(node: ast.AST | None) -> int | None:
    value = node.value if isinstance(node, ast.Constant) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _importlib_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    module_aliases: set[str] = set()
    function_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    function_aliases.add(alias.asname or alias.name)
    return module_aliases, function_aliases


def _dynamic_import_target(
    *,
    path: Path,
    call: ast.Call,
    importlib_module_aliases: set[str],
    import_module_aliases: set[str],
) -> str | None:
    if not call.args:
        return None

    target = _constant_string(call.args[0])
    if target is None:
        return None

    is_builtin_import = isinstance(call.func, ast.Name) and call.func.id == "__import__"
    is_direct_import_module = (
        isinstance(call.func, ast.Name) and call.func.id in import_module_aliases
    )
    is_module_import_module = (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "import_module"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in importlib_module_aliases
    )

    if is_builtin_import:
        level_node = _keyword(call, "level")
        if level_node is None and len(call.args) >= 5:
            level_node = call.args[4]
        level = _constant_int(level_node) or 0
        if level <= 0:
            return target
        return _resolve_relative_module(
            package=_package_name_for_path(path),
            module=target,
            level=level,
        )

    if not (is_direct_import_module or is_module_import_module):
        return None

    if not target.startswith("."):
        return target

    package_node = _keyword(call, "package")
    if package_node is None and len(call.args) >= 2:
        package_node = call.args[1]
    package = _constant_string(package_node)
    if package is None and isinstance(package_node, ast.Name) and package_node.id == "__package__":
        package = _package_name_for_path(path)
    if not package:
        return None

    level = len(target) - len(target.lstrip("."))
    module = target[level:]
    return _resolve_relative_module(package=package, module=module, level=level)


def _legacy_assignment_import_lines(path: Path, tree: ast.AST) -> list[int]:
    lines: set[int] = set()
    importlib_module_aliases, import_module_aliases = _importlib_aliases(tree)

    for node in ast.walk(tree):
        violation = False
        if isinstance(node, ast.Import):
            violation = any(_is_legacy_assignment_module(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(path, node)
            violation = _is_legacy_assignment_module(module)
            if not violation:
                violation = any(
                    _is_legacy_assignment_module(
                        f"{module}.{alias.name}" if module else alias.name
                    )
                    for alias in node.names
                )
        elif isinstance(node, ast.Call):
            target = _dynamic_import_target(
                path=path,
                call=node,
                importlib_module_aliases=importlib_module_aliases,
                import_module_aliases=import_module_aliases,
            )
            violation = target is not None and _is_legacy_assignment_module(target)

        if violation and getattr(node, "lineno", None) is not None:
            lines.add(int(node.lineno))

    return sorted(lines)


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
            for target in _assignment_targets(node):
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


def check_payment_status_writes() -> list[str]:
    """Financial status writes must pass one lifecycle boundary.

    Timestamps, PaymentEvent projection and future reconciliation rules all rely
    on observing the same transition. Product code therefore may not mutate a
    Payment status directly or through ``update(Payment).values(status=...)``.
    Creation-time ``Payment(status=...)`` is intentionally allowed because that
    is not a transition of an existing financial record.
    """

    errors: list[str] = []
    allowed = APP / "domain" / "payments" / "payment_lifecycle.py"
    for path in python_files():
        if path == allowed:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            errors.append(f"{path.relative_to(ROOT)}: syntax error: {error}")
            continue

        for node in ast.walk(tree):
            for target in _assignment_targets(node):
                if not isinstance(target, ast.Attribute) or target.attr != "status":
                    continue
                owner = (_root_name(target.value) or "").lower()
                if "payment" in owner:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        "Payment.status must be changed through PaymentLifecycleService"
                    )

            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "values":
                continue
            if not any(keyword.arg == "status" for keyword in node.keywords):
                continue
            if _contains_name(node.func.value, "Payment"):
                errors.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    "bulk Payment status updates must use PaymentLifecycleService"
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


def check_legacy_assignment_imports() -> list[str]:
    """Keep the hardened CaseAssignmentService as the only product assignment path.

    ``app.domain.assignment`` is a historical package whose AssignmentEngine and
    WorkloadService predate the Case-row/candidate locking, active staff identity,
    workload/capacity and SLA/audit semantics in ``CaseAssignmentService``. The
    legacy modules remain importable for compatibility while their safe removal
    is audited, but production modules may not depend on them through absolute,
    relative or literal dynamic imports.
    """

    errors: list[str] = []
    legacy_root = APP / "domain" / "assignment"

    for path in python_files():
        if legacy_root in path.parents:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            errors.append(f"{path.relative_to(ROOT)}: syntax error: {error}")
            continue

        for lineno in _legacy_assignment_import_lines(path, tree):
            errors.append(
                f"{path.relative_to(ROOT)}:{lineno}: "
                "legacy app.domain.assignment is isolated; use "
                "app.domain.cases.assignment_service.CaseAssignmentService"
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


def check_metadata_dependency_cycles() -> list[str]:
    """Reject table cycles that make Alembic silently skip FK comparison."""

    from app.models import Base

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        list(Base.metadata.sorted_tables)
    return [
        "SQLAlchemy metadata contains an unresolved table dependency cycle: "
        + str(warning.message)
        for warning in caught
        if issubclass(warning.category, SAWarning)
        and "unresolvable cycles" in str(warning.message).lower()
    ]


def main() -> None:
    from app.domain.cases.case_transition_policy import assert_policy_complete

    assert_policy_complete()
    errors = [
        *check_case_status_writes(),
        *check_payment_status_writes(),
        *check_forced_transition_bypasses(),
        *check_legacy_assignment_imports(),
        *check_dead_bot_callbacks(),
        *check_calculator_clock_boundary(),
        *check_runtime_routes(),
        *check_metadata_dependency_cycles(),
    ]
    if errors:
        raise ArchitectureCheckError("\n".join(errors))
    print("architecture quality gate: OK")


if __name__ == "__main__":
    main()
