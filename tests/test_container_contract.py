from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_contains_runtime_migration_assets_and_postgres_client():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml README.md alembic.ini ./" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert "COPY scripts ./scripts" in dockerfile
    assert "postgresql-client" in dockerfile
    assert "python scripts/init_db.py && exec python -m app.process" in dockerfile
    assert "exec python -m app.main" not in dockerfile


def test_docker_context_excludes_secrets_databases_and_legal_documents():
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env" in ignored
    assert ".env.*" in ignored
    assert "!.env.example" in ignored
    assert "backups/" in ignored
    assert "data/" in ignored
    assert "storage/" in ignored
    assert "logs/" in ignored
    assert "*.db" in ignored
    assert "*.sqlite" in ignored
    assert "*.dlcbak" in ignored


def test_dockerfile_does_not_copy_the_entire_repository_context():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY . ." not in dockerfile
    assert "ADD . ." not in dockerfile
    assert "COPY .env " not in dockerfile
