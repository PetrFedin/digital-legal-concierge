from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _parsed(path: str) -> ast.Module:
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def test_app_main_does_not_start_unmanaged_background_tasks():
    tree = _parsed("app/main.py")

    create_task_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
        and node.func.attr == "create_task"
    ]

    assert create_task_calls == []


def test_app_main_does_not_construct_a_second_uvicorn_server():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")

    assert "import uvicorn" not in source
    assert "uvicorn.Config" not in source
    assert "uvicorn.Server" not in source


@pytest.mark.asyncio
async def test_legacy_main_delegates_to_canonical_supervisor(monkeypatch):
    import app.process as process
    from app import main as main_module

    called = 0

    async def fake_supervised_main() -> str:
        nonlocal called
        called += 1
        return "supervised"

    monkeypatch.setattr(process, "main", fake_supervised_main)

    assert await main_module.main() == "supervised"
    assert called == 1


def test_docker_and_legacy_module_share_one_runtime_implementation():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    main_source = (ROOT / "app/main.py").read_text(encoding="utf-8")

    assert "exec python -m app.process" in dockerfile
    assert "from app.process import main as run_supervised_process" in main_source
    assert "return await run_supervised_process()" in main_source
