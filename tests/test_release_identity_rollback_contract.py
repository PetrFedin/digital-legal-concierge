from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.release import expected_migration_heads, release_metadata


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_release_metadata_exposes_only_non_sensitive_build_identity(monkeypatch):
    monkeypatch.setenv("APP_RELEASE", "release-20260803")
    monkeypatch.setenv("GIT_COMMIT_SHA", "a" * 40)
    monkeypatch.setenv("BUILD_TIMESTAMP", "2026-08-03T16:00:00Z")
    monkeypatch.setenv("APP_IMAGE_REPOSITORY", "digital-legal-concierge")
    monkeypatch.setenv("APP_IMAGE_TAG", "aaaaaaaaaaaa")

    payload = release_metadata()

    assert payload == {
        "application_version": "1.0.0-v45",
        "release": "release-20260803",
        "git_commit": "a" * 40,
        "build_timestamp": "2026-08-03T16:00:00Z",
        "image_repository": "digital-legal-concierge",
        "image_tag": "aaaaaaaaaaaa",
        "migration_heads": list(expected_migration_heads()),
    }
    serialized = str(payload).lower()
    for forbidden in ("token", "password", "secret", "database_url"):
        assert forbidden not in serialized


def test_runtime_release_endpoint_matches_environment(monkeypatch):
    monkeypatch.setenv("APP_RELEASE", "release-contract")
    monkeypatch.setenv("GIT_COMMIT_SHA", "b" * 40)
    monkeypatch.setenv("APP_IMAGE_TAG", "bbbbbbbbbbbb")

    response = TestClient(create_app()).get("/runtime/release")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["release"] == "release-contract"
    assert payload["git_commit"] == "b" * 40
    assert payload["image_tag"] == "bbbbbbbbbbbb"
    assert payload["migration_heads"]


def test_production_image_has_oci_release_identity():
    source = read("Dockerfile")

    for build_argument in (
        "APP_RELEASE",
        "GIT_COMMIT_SHA",
        "BUILD_TIMESTAMP",
        "APP_IMAGE_REPOSITORY",
        "APP_IMAGE_TAG",
    ):
        assert f"ARG {build_argument}" in source
        assert f"{build_argument}=${{{build_argument}}}" in source
    assert "org.opencontainers.image.version" in source
    assert "org.opencontainers.image.revision" in source
    assert "org.opencontainers.image.created" in source


def test_compose_uses_immutable_image_tag_and_build_metadata():
    for path in ("docker-compose.yml", "docker-compose.timeweb.yml"):
        source = read(path)
        assert "APP_IMAGE_REPOSITORY" in source
        assert "APP_IMAGE_TAG" in source
        assert "APP_RELEASE:" in source
        assert "GIT_COMMIT_SHA:" in source
        assert "BUILD_TIMESTAMP:" in source
        assert "APP_IMAGE_REPOSITORY:" in source
        assert "APP_IMAGE_TAG:" in source


def test_deploy_is_clean_commit_backup_first_and_identity_checked():
    source = read("deploy.sh")

    assert "git status --porcelain --untracked-files=no" in source
    assert "git rev-parse --verify HEAD" in source
    assert 'APP_IMAGE_TAG="${RELEASE_TAG:-${GIT_COMMIT_SHA:0:12}}"' in source
    assert "org.opencontainers.image.revision" in source
    assert "/runtime/release" in source
    assert "payload.get(\"git_commit\") == expected" in source
    assert "scripts/release_metadata.py --field migration_heads" in source
    assert "Создание зашифрованной резервной копии перед обновлением" in source
    assert source.index("backup_cli create") < source.index("dc up -d --remove-orphans")
    assert "docker compose down -v" not in source


def test_automatic_rollback_requires_the_same_migration_head():
    source = read("deploy.sh")

    assert '[ "$old_heads" = "unknown" ]' in source
    assert '[ "$old_heads" != "$new_migration_heads" ]' in source
    assert "Автоматический rollback заблокирован" in source
    assert "--no-build --force-recreate app" in source
    assert "Предыдущий image восстановлен" in source


def test_manual_rollback_is_backup_first_schema_guarded_and_reversible():
    source = read("rollback.sh")

    assert '[ "$current_heads" != "$previous_heads" ]' in source
    assert "Rollback заблокирован: Alembic-head отличается" in source
    assert source.index("backup_cli create") < source.index("--no-build --force-recreate app")
    assert source.count("--no-build --force-recreate app") == 2
    assert "Возвращаю исходный current image" in source
    assert "state-swap.env" in source
    assert "docker compose down -v" not in source


def test_operator_scripts_are_valid_bash():
    for path in (
        "deploy.sh",
        "rollback.sh",
        "status.sh",
        "bot-control.sh",
        "timeweb-deploy.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"


def test_release_state_is_not_committed_or_sent_to_docker():
    assert ".release/" in read(".gitignore")
    assert ".release/" in read(".dockerignore")
    assert "COPY rollback.sh" not in read("Dockerfile")
    assert "rollback.sh" in read("Dockerfile.test")
