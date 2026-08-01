from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = (ROOT / "app", ROOT / "scripts", ROOT / "tests")
LEGACY_MODULES = {"fastapi.testclient", "starlette.testclient"}


def _python_files():
    for directory in SCANNED_DIRS:
        if directory.exists():
            yield from sorted(directory.rglob("*.py"))


def test_repository_does_not_use_deprecated_sync_testclient():
    legacy_imports: list[str] = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in LEGACY_MODULES:
                imported = ", ".join(alias.name for alias in node.names)
                legacy_imports.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    f"from {node.module} import {imported}"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in LEGACY_MODULES:
                        legacy_imports.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: import {alias.name}"
                        )

    assert legacy_imports == [], (
        "deprecated TestClient imports must be replaced with "
        "httpx.AsyncClient + httpx.ASGITransport:\n"
        + "\n".join(legacy_imports)
    )
