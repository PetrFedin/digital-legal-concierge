"""Regression: a 'final acceptance' command cannot write to configured live data."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import final_acceptance as acceptance


def _fresh_sqlite_url() -> str:
    location = Path(tempfile.gettempdir()) / f"dlc-acceptance-{uuid4().hex}.db"
    assert not location.exists()
    return f"sqlite+aiosqlite:///{location}"


def _safe_synthetic_environment(monkeypatch):
    values = {
        "DLC_ALLOW_DISPOSABLE_E2E": "YES",
        "APP_ENV": "test",
        "RUN_BOT": "false",
        "RUN_SCHEDULER": "false",
        "PAYMENT_PROVIDER": "fake",
        "DATABASE_URL": _fresh_sqlite_url(),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def test_default_acceptance_is_only_read_only_file_preflight(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://live.invalid/not_a_test")
    calls = []
    monkeypatch.setattr(acceptance.compileall, "compile_dir", lambda *a, **k: True)
    monkeypatch.setattr(
        acceptance,
        "run",
        lambda title, cmd: (calls.append(tuple(cmd)), True)[1],
    )

    assert acceptance.main([]) == 0
    assert calls == [command for _, command in acceptance.STATIC_CHECKS]
    assert not any(
        command in calls for _, command in acceptance.SYNTHETIC_WRITES
    )
    output = capsys.readouterr().out
    assert "SOURCE PREFLIGHT OK" in output
    assert "НЕ проверяются" in output
    assert "ГОТОВО К ТЕСТОВОМУ ЗАПУСКУ" not in output


@pytest.mark.parametrize(
    "invalid",
    [
        {"DLC_ALLOW_DISPOSABLE_E2E": "NO"},
        {"APP_ENV": "production"},
        {"RUN_BOT": "true"},
        {"RUN_SCHEDULER": "true"},
        {"PAYMENT_PROVIDER": "yookassa"},
        {"DATABASE_URL": "postgresql+asyncpg://live.invalid/customer"},
        {"DATABASE_URL": "sqlite+aiosqlite:///./app.db"},
    ],
)
def test_synthetic_acceptance_does_not_start_with_unsafe_environment(
    monkeypatch, invalid, capsys,
):
    _safe_synthetic_environment(monkeypatch)
    for name, value in invalid.items():
        monkeypatch.setenv(name, value)
    commands = []
    monkeypatch.setattr(
        acceptance, "run",
        lambda title, cmd: (commands.append(cmd), True)[1],
    )
    assert acceptance.main(["--synthetic-e2e"]) == 2
    assert commands == []
    assert "ОТКАЗАНО" in capsys.readouterr().out


def test_existing_disposable_database_is_rejected(monkeypatch):
    _safe_synthetic_environment(monkeypatch)
    temp_dir = Path(tempfile.gettempdir())
    file_path = temp_dir / f"dlc-acceptance-{uuid4().hex}.db"
    try:
        file_path.write_text("existing data must never be replaced")
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{file_path}")
        assert any(
            "НЕСУЩЕСТВУЮЩИЙ" in item
            for item in acceptance.synthetic_environment_errors(os.environ)
        )
        assert file_path.read_text() == "existing data must never be replaced"
    finally:
        file_path.unlink(missing_ok=True)


def test_isolated_opt_in_runs_synthetic_flow_without_claiming_live_proof(
    monkeypatch, capsys,
):
    _safe_synthetic_environment(monkeypatch)
    calls = []
    monkeypatch.setattr(acceptance.compileall, "compile_dir", lambda *a, **k: True)
    monkeypatch.setattr(
        acceptance,
        "run",
        lambda title, cmd: (calls.append(tuple(cmd)), True)[1],
    )

    assert acceptance.main(["--synthetic-e2e"]) == 0
    assert calls == [
        command
        for _, command in acceptance.STATIC_CHECKS + acceptance.SYNTHETIC_WRITES
    ]
    output = capsys.readouterr().out
    assert "SYNTHETIC TEST ONLY" in output
    assert "НЕ ПРОДАКШЕН И НЕ ПРИЁМКА" in output


@pytest.mark.parametrize(
    "entrypoint",
    ("scripts/e2e_smoke.py", "scripts/e2e_m1_m2_full.py"),
)
def test_direct_synthetic_entrypoints_require_the_same_disposable_guard(entrypoint):
    source = (acceptance.ROOT / entrypoint).read_text(encoding="utf-8")
    main_block = source.split('if __name__ == "__main__":', 1)
    assert len(main_block) == 2
    block = main_block[1]
    assert "synthetic_environment_errors(os.environ)" in block
    assert block.index("synthetic_environment_errors(os.environ)") < block.index("asyncio.run(main())")
    assert "raise SystemExit(2)" in block
