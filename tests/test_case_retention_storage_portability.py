from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.domain.retention.case_retention_service import (
    CaseRetentionError,
    CaseRetentionService,
)


CIPHERTEXT = "a" * 32 + ".dlcenc"


def _document(file_path: str, *, case_id: int = 42):
    return SimpleNamespace(file_path=file_path, case_id=case_id)


def _stored_file(root: Path, *, case_id: int = 42) -> Path:
    path = root / "cases" / str(case_id) / CIPHERTEXT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"encrypted-retention-test")
    return path


def _service(monkeypatch: pytest.MonkeyPatch, storage_root: Path) -> CaseRetentionService:
    monkeypatch.setattr(settings, "storage_dir", str(storage_root))
    return CaseRetentionService(None)  # type: ignore[arg-type]


def test_retention_deletes_canonical_relative_document_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "storage"
    target = _stored_file(root)
    document = _document(f"cases/42/{CIPHERTEXT}")
    service = _service(monkeypatch, root)

    service._preflight_documents([document])
    service._delete_document_files([document])

    assert not target.exists()


def test_retention_legacy_absolute_path_deletes_only_restored_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source-storage"
    restored_root = tmp_path / "restored-storage"
    source_file = _stored_file(source_root)
    restored_file = _stored_file(restored_root)
    source_file.write_bytes(b"source-copy")
    restored_file.write_bytes(b"restored-copy")
    service = _service(monkeypatch, restored_root)
    document = _document(str(source_file))

    service._preflight_documents([document])
    service._delete_document_files([document])

    assert source_file.exists()
    assert source_file.read_bytes() == b"source-copy"
    assert not restored_file.exists()


def test_retention_rejects_prefixed_relative_document_key_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "storage"
    target = _stored_file(root)
    service = _service(monkeypatch, root)
    document = _document(f"stale-prefix/cases/42/{CIPHERTEXT}")

    with pytest.raises(CaseRetentionError, match="Небезопасный путь документа"):
        service._preflight_documents([document])

    assert target.exists()


def test_retention_rejects_wrong_case_storage_key_before_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "storage"
    wrong_case_file = _stored_file(root, case_id=43)
    service = _service(monkeypatch, root)
    document = _document(f"cases/43/{CIPHERTEXT}", case_id=42)

    with pytest.raises(CaseRetentionError, match="Небезопасный путь документа"):
        service._preflight_documents([document])

    assert wrong_case_file.exists()


def test_retention_missing_file_delete_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "storage"
    service = _service(monkeypatch, root)
    document = _document(f"cases/42/{CIPHERTEXT}")

    service._preflight_documents([document])
    service._delete_document_files([document])
    service._delete_document_files([document])
