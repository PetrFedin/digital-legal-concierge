from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.live_required_evidence import (
    DEFAULT_REQUIRED_COMPONENTS,
    aggregate_evidence,
    write_component_evidence,
)


SHA = "a" * 40


def _set_run_env(monkeypatch: pytest.MonkeyPatch, *, run_id: str = "12345") -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "PetrFedin/digital-legal-concierge")
    monkeypatch.setenv("GITHUB_WORKFLOW", "LIVE_REQUIRED Release Matrix")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/release-candidate")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")


def _write_all(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    sha: str = SHA,
    run_id: str = "12345",
) -> None:
    _set_run_env(monkeypatch, run_id=run_id)
    for component in DEFAULT_REQUIRED_COMPONENTS:
        write_component_evidence(
            component=component,
            sha=sha,
            output=root / f"{component}.json",
            detail=f"{component} passed",
        )


def test_component_evidence_records_exact_sha_run_and_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_run_env(monkeypatch)
    output = tmp_path / "postgres.json"

    payload = write_component_evidence(
        component="postgres",
        sha=SHA,
        output=output,
        detail="PostgreSQL contracts passed",
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == payload
    assert payload["kind"] == "live_required_component"
    assert payload["component"] == "postgres"
    assert payload["status"] == "success"
    assert payload["sha"] == SHA
    assert payload["run_id"] == "12345"
    assert payload["run_url"].endswith("/actions/runs/12345")
    assert payload["recorded_at"].endswith("+00:00")


def test_aggregate_builds_one_live_pass_manifest_for_exact_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = tmp_path / "components"
    _write_all(components, monkeypatch)
    output = tmp_path / "LIVE_REQUIRED_MANIFEST.json"

    manifest = aggregate_evidence(
        input_dir=components,
        sha=SHA,
        output=output,
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == manifest
    assert manifest["kind"] == "live_required_manifest"
    assert manifest["status"] == "LIVE_PASS"
    assert manifest["sha"] == SHA
    assert tuple(manifest["required_components"]) == DEFAULT_REQUIRED_COMPONENTS
    assert set(manifest["components"]) == set(DEFAULT_REQUIRED_COMPONENTS)
    assert {item["run_id"] for item in manifest["components"].values()} == {"12345"}


def test_aggregate_fails_closed_when_component_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = tmp_path / "components"
    _write_all(components, monkeypatch)
    (components / "telegram.json").unlink()

    with pytest.raises(RuntimeError, match="Missing LIVE_REQUIRED component evidence: telegram"):
        aggregate_evidence(
            input_dir=components,
            sha=SHA,
            output=tmp_path / "manifest.json",
        )


def test_aggregate_rejects_component_from_different_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = tmp_path / "components"
    _write_all(components, monkeypatch)
    write_component_evidence(
        component="provider-sandbox",
        sha="b" * 40,
        output=components / "provider-sandbox.json",
        detail="wrong SHA",
    )

    with pytest.raises(RuntimeError, match="belongs to SHA"):
        aggregate_evidence(
            input_dir=components,
            sha=SHA,
            output=tmp_path / "manifest.json",
        )


def test_aggregate_rejects_component_from_different_workflow_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = tmp_path / "components"
    _write_all(components, monkeypatch)
    _set_run_env(monkeypatch, run_id="99999")
    write_component_evidence(
        component="redis",
        sha=SHA,
        output=components / "redis.json",
        detail="different run",
    )
    _set_run_env(monkeypatch, run_id="12345")

    with pytest.raises(RuntimeError, match="belongs to workflow run"):
        aggregate_evidence(
            input_dir=components,
            sha=SHA,
            output=tmp_path / "manifest.json",
        )


def test_aggregate_rejects_duplicate_component_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = tmp_path / "components"
    _write_all(components, monkeypatch)
    duplicate_dir = components / "duplicate"
    write_component_evidence(
        component="postgres",
        sha=SHA,
        output=duplicate_dir / "postgres-copy.json",
        detail="duplicate evidence",
    )

    with pytest.raises(RuntimeError, match="Duplicate LIVE_REQUIRED evidence"):
        aggregate_evidence(
            input_dir=components,
            sha=SHA,
            output=tmp_path / "manifest.json",
        )
