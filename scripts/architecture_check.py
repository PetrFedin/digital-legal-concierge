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
    workload/capacity and SLA/audit semantics in ``CaseAssignmentService``.  The
    legacy modules remain importable for compatibility while their safe removal
    is audited, but no production module may start depending on them again.
    """

    errors: list[str] = []
    legacy_root = APP / "domain" / "assignment"
    forbidden_module = "app.domain.assignment"

    for path in python_files():
        if legacy_root in path.parents:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as error:
            errors.append(f"{path.relative_to(ROOT)}: syntax error: {error}")
            continue

        for node in ast.walk(tree):
            violation = False
            if isinstance(node, ast.Import):
                violation = any(
                    alias.name == forbidden_module
                    or alias.name.startswith(forbidden_module + ".")
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                violation = (
                    module == forbidden_module
                    or module.startswith(forbidden_module + ".")
                    or (
                        module == "app.domain"
                        and any(alias.name == "assignment" for alias in node.names)
                    )
                )

            if violation:
                errors.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
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
