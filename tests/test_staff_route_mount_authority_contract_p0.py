"""Verify the exact web-server route contract using a fresh Python process.

Pytest suites can mutate application global state in test fixtures; auditing
an old in-process app object does not prove what starts in the server process.
The script fails closed and reports exact unguarded method/path pairs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_server_startup_staff_route_authority():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_staff_routing.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Fresh ASGI staff route security check FAILED:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["expected_router_families"] == 8
    assert report["tested_staff_endpoints"] >= 20
    assert report["failures"] == []
