from __future__ import annotations

import ast

import pytest

from scripts.architecture_check import ROOT, _legacy_assignment_import_lines


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        ("app/api/example.py", "import app.domain.assignment\n"),
        (
            "app/api/example.py",
            "import app.domain.assignment.assignment_engine as legacy\n",
        ),
        (
            "app/api/example.py",
            "from app.domain.assignment import AssignmentEngine\n",
        ),
        ("app/api/example.py", "from app.domain import assignment\n"),
        (
            "app/domain/cases/example.py",
            "from ..assignment import AssignmentEngine\n",
        ),
        ("app/domain/example.py", "from . import assignment\n"),
        (
            "app/api/example.py",
            "import importlib\nlegacy = importlib.import_module('app.domain.assignment')\n",
        ),
        (
            "app/api/example.py",
            "import importlib as il\nlegacy = il.import_module('app.domain.assignment.workload_service')\n",
        ),
        (
            "app/api/example.py",
            "from importlib import import_module as load\nlegacy = load('app.domain.assignment')\n",
        ),
        (
            "app/api/example.py",
            "legacy = __import__('app.domain.assignment.assignment_engine')\n",
        ),
        (
            "app/domain/cases/example.py",
            "from importlib import import_module\nlegacy = import_module('..assignment', package=__package__)\n",
        ),
        (
            "app/domain/cases/example.py",
            "legacy = __import__('assignment', globals(), locals(), [], 2)\n",
        ),
    ],
)
def test_legacy_assignment_guard_rejects_absolute_relative_and_dynamic_imports(
    relative_path: str,
    source: str,
) -> None:
    path = ROOT / relative_path
    tree = ast.parse(source)

    assert _legacy_assignment_import_lines(path, tree)


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (
            "app/api/example.py",
            "from app.domain.cases.assignment_service import CaseAssignmentService\n",
        ),
        (
            "app/domain/example.py",
            "from .cases import assignment_service\n",
        ),
        (
            "app/api/example.py",
            "import importlib\nservice = importlib.import_module('app.domain.cases.assignment_service')\n",
        ),
        (
            "app/domain/cases/example.py",
            "from importlib import import_module\nservice = import_module('.assignment_service', package=__package__)\n",
        ),
    ],
)
def test_legacy_assignment_guard_allows_canonical_assignment_service(
    relative_path: str,
    source: str,
) -> None:
    path = ROOT / relative_path
    tree = ast.parse(source)

    assert _legacy_assignment_import_lines(path, tree) == []
