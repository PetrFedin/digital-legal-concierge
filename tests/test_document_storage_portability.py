from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.storage as storage_module
from app.domain.documents import staff_upload_storage
from app.security.document_encryption import DocumentEncryptionError
from app.storage import LocalStorageService


CIPHERTEXT_NAME = "a" * 32 + ".dlcenc"


def _document(root: Path, case_id: int = 42, name: str = CIPHERTEXT_NAME) -> Path:
    path = root / "cases" / str(case_id) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ciphertext")
    return path


def test_portable_storage_key_is_relative_and_case_bound(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    storage = LocalStorageService(str(root))
    document = _document(root)

    key = storage.storage_key_for_case_path(document, case_id=42)

    assert key == f"cases/42/{CIPHERTEXT_NAME}"
    assert not Path(key).is_absolute()
    assert storage.resolve_storage_path(key, expected_case_id=42) == document.resolve()


def test_legacy_absolute_path_is_rebased_to_current_restore_root(tmp_path: Path) -> None:
    old_root = tmp_path / "source-storage"
    new_root = tmp_path / "restored-storage"
    old_document = _document(old_root)
    new_document = _document(new_root)
    old_document.write_bytes(b"source-copy")
    new_document.write_bytes(b"restored-copy")
    storage = LocalStorageService(str(new_root))

    resolved = storage.resolve_storage_path(
        str(old_document),
        expected_case_id=42,
    )

    assert resolved == new_document.resolve()
    assert resolved != old_document.resolve()
    assert resolved.read_bytes() == b"restored-copy"


def test_legacy_absolute_discard_never_deletes_source_storage(tmp_path: Path) -> None:
    old_root = tmp_path / "source-storage"
    new_root = tmp_path / "restored-storage"
    old_document = _document(old_root)
    new_document = _document(new_root)
    storage = LocalStorageService(str(new_root))

    removed = storage.discard_stored_file(
        str(old_document),
        expected_case_id=42,
    )

    assert removed is True
    assert old_document.exists()
    assert not new_document.exists()


def test_legacy_absolute_read_uses_rebased_ciphertext_with_existing_api_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_root = tmp_path / "source-storage"
    new_root = tmp_path / "restored-storage"
    old_document = _document(old_root)
    new_document = _document(new_root)
    storage = LocalStorageService(str(new_root))
    captured: dict[str, Path] = {}

    def fake_decrypt(path, **_kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["path"] = Path(path)
        return b"restored-plaintext", SimpleNamespace()

    monkeypatch.setattr(storage_module, "decrypt_file_bytes", fake_decrypt)

    # document_access.py currently calls read_document_bytes(document.file_path,
    # ...envelope metadata...) without a case-id argument. Legacy rebasing must
    # therefore remain safe and portable for that existing authorized API path.
    plaintext = storage.read_document_bytes(
        str(old_document),
        expected_sha256="b" * 64,
    )

    assert plaintext == b"restored-plaintext"
    assert captured["path"] == new_document.resolve()
    assert captured["path"] != old_document.resolve()


def test_wrong_case_storage_scope_is_rejected(tmp_path: Path) -> None:
    storage = LocalStorageService(str(tmp_path / "storage"))
    document = _document(storage.base_dir, case_id=42)

    with pytest.raises(DocumentEncryptionError, match="другому storage scope"):
        storage.resolve_storage_path(document, expected_case_id=41)


def test_arbitrary_external_absolute_path_is_not_rebased(tmp_path: Path) -> None:
    storage = LocalStorageService(str(tmp_path / "storage"))
    outside = tmp_path / "outside" / "not-a-document.bin"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"external")

    with pytest.raises(DocumentEncryptionError, match="вне защищённого хранилища"):
        storage.resolve_storage_path(outside)


def test_document_storage_key_rejects_traversal_and_invalid_ciphertext_name(
    tmp_path: Path,
) -> None:
    storage = LocalStorageService(str(tmp_path / "storage"))

    with pytest.raises(DocumentEncryptionError, match="Недопустимый путь"):
        storage.resolve_storage_path(
            f"cases/42/../43/{CIPHERTEXT_NAME}",
            expected_case_id=42,
        )
    with pytest.raises(DocumentEncryptionError, match="storage key"):
        storage.resolve_storage_path(
            "cases/42/client-controlled.pdf",
            expected_case_id=42,
        )


def test_symlink_component_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    real_cases = tmp_path / "real-cases"
    real_document = _document(real_cases, case_id=42)
    root.mkdir(parents=True)
    (root / "cases").symlink_to(real_cases / "cases", target_is_directory=True)
    storage = LocalStorageService(str(root))

    with pytest.raises(DocumentEncryptionError, match="Символьные ссылки"):
        storage.resolve_storage_path(
            f"cases/42/{real_document.name}",
            expected_case_id=42,
        )


def test_non_document_utility_path_inside_current_root_remains_supported(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    storage = LocalStorageService(str(root))
    probe = root / "probe.bin"
    probe.write_bytes(b"probe")

    assert storage.resolve_storage_path(probe) == probe.resolve()
    assert storage.resolve_storage_path("probe.bin") == probe.resolve()


def test_client_upload_returns_portable_storage_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorageService(str(tmp_path / "storage"))
    inspected_at = datetime.now(timezone.utc)

    monkeypatch.setattr(
        storage_module,
        "preflight_upload",
        lambda **_kwargs: SimpleNamespace(safe_name="contract.pdf"),
    )
    monkeypatch.setattr(
        storage_module,
        "inspect_upload",
        lambda *_args, **_kwargs: SimpleNamespace(
            safe_name="contract.pdf",
            mime_type="application/pdf",
            size_bytes=4,
            sha256="c" * 64,
            detected_type="pdf",
            scanned_at=inspected_at,
        ),
    )

    def fake_encrypt(source, target, **_kwargs):  # noqa: ANN001, ANN003, ANN202
        Path(target).write_bytes(b"encrypted")
        return SimpleNamespace(
            key_id="doc-v1",
            format_version=2,
            envelope_id="env-1",
            encrypted_data_key="wrapped",
            encrypted_data_key_nonce="nonce",
            encrypted_at=inspected_at,
        )

    monkeypatch.setattr(storage_module, "encrypt_file", fake_encrypt)

    class FakeBot:
        async def get_file(self, _file_id):  # noqa: ANN001, ANN202
            return SimpleNamespace(file_path="remote/file")

        async def download_file(self, _file_path, destination):  # noqa: ANN001, ANN202
            Path(destination).write_bytes(b"data")

    stored = asyncio.run(
        storage.save_telegram_file(
            bot=FakeBot(),
            telegram_file_id="telegram-id",
            case_id=7,
            original_name="contract.pdf",
            mime_type="application/pdf",
            file_size=4,
        )
    )

    assert re.fullmatch(r"cases/7/[0-9a-f]{32}\.dlcenc", stored.storage_path)
    assert not Path(stored.storage_path).is_absolute()
    assert storage.resolve_storage_path(
        stored.storage_path,
        expected_case_id=7,
    ).is_file()


def test_staff_upload_returns_portable_storage_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalStorageService(str(tmp_path / "storage"))
    inspected_at = datetime.now(timezone.utc)
    monkeypatch.setattr(staff_upload_storage, "LocalStorageService", lambda: storage)
    monkeypatch.setattr(
        staff_upload_storage,
        "preflight_upload",
        lambda **_kwargs: SimpleNamespace(safe_name="evidence.pdf"),
    )
    monkeypatch.setattr(
        staff_upload_storage,
        "inspect_upload",
        lambda *_args, **_kwargs: SimpleNamespace(
            safe_name="evidence.pdf",
            mime_type="application/pdf",
            size_bytes=4,
            sha256="d" * 64,
            detected_type="pdf",
            scanned_at=inspected_at,
        ),
    )

    def fake_encrypt(source, target, **_kwargs):  # noqa: ANN001, ANN003, ANN202
        Path(target).write_bytes(b"encrypted")
        return SimpleNamespace(
            key_id="doc-v1",
            envelope_id="env-2",
            encrypted_data_key="wrapped",
            encrypted_data_key_nonce="nonce",
            encrypted_at=inspected_at,
        )

    monkeypatch.setattr(staff_upload_storage, "encrypt_file", fake_encrypt)

    async def chunks():
        yield b"data"

    stored = asyncio.run(
        staff_upload_storage.save_staff_upload(
            chunks=chunks(),
            case_id=8,
            original_name="evidence.pdf",
            mime_type="application/pdf",
            declared_size=4,
        )
    )

    assert re.fullmatch(r"cases/8/[0-9a-f]{32}\.dlcenc", stored.storage_path)
    assert not Path(stored.storage_path).is_absolute()
    assert storage.resolve_storage_path(
        stored.storage_path,
        expected_case_id=8,
    ).is_file()
