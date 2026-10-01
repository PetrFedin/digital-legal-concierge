from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.security.document_encryption import is_encrypted_file
from app.security.file_uploads import UploadSecurityError
from app.security.malware_scanning import (
    ClamAVScanner,
    MalwareScanResult,
    MalwareScanStatus,
)
from app.storage import LocalStorageService


DOCUMENT_KEY = "dlc-int-00-document-key-" + "x" * 40


def valid_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


class FakeBot:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def get_file(self, telegram_file_id: str):
        return SimpleNamespace(file_path=f"telegram/{telegram_file_id}")

    async def download_file(self, file_path: str, destination: Path):
        Path(destination).write_bytes(self.payload)


class CleanScanner:
    async def scan(self, path: Path) -> MalwareScanResult:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return MalwareScanResult(
            status=MalwareScanStatus.CLEAN,
            engine="test-scanner",
            engine_version="1",
            signature_info="fixture",
            sha256=digest,
            scanned_at=datetime.now(timezone.utc),
        )


class InfectedScanner:
    async def scan(self, path: Path) -> MalwareScanResult:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        raise UploadSecurityError(
            "malware_detected",
            "Файл отклонён проверкой безопасности.",
            technical_message="test malware fixture",
            sha256=digest,
        )


class WrongHashScanner:
    async def scan(self, path: Path) -> MalwareScanResult:
        return MalwareScanResult(
            status=MalwareScanStatus.CLEAN,
            engine="test-scanner",
            engine_version="1",
            signature_info="fixture",
            sha256="0" * 64,
            scanned_at=datetime.now(timezone.utc),
        )


def configure_keys(monkeypatch, *, app_env: str = "production") -> None:
    monkeypatch.setattr(settings, "app_env", app_env)
    monkeypatch.setattr(settings, "document_encryption_key_id", "documents-dlc-int-00")
    monkeypatch.setattr(settings, "document_encryption_key", DOCUMENT_KEY)
    monkeypatch.setattr(settings, "document_encryption_previous_keys", "")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "max_document_upload_mb", 20)
    monkeypatch.setattr(settings, "quarantine_rejected_uploads", True)


@pytest.mark.asyncio
async def test_clean_malware_verdict_precedes_structural_admission_and_encryption(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    payload = valid_pdf()
    storage = LocalStorageService(
        str(tmp_path / "storage"),
        malware_scanner=CleanScanner(),
    )

    stored = await storage.save_telegram_file(
        bot=FakeBot(payload),
        telegram_file_id="clean",
        case_id=11,
        original_name="contract.pdf",
        mime_type="application/pdf",
        file_size=len(payload),
    )

    assert stored.security_status == "VERIFIED"
    assert stored.security_reason == (
        "malware=CLEAN;engine=test-scanner;version=1;signatures=fixture"
    )
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    encrypted = storage.resolve_storage_path(
        stored.storage_path,
        expected_case_id=11,
    )
    assert is_encrypted_file(encrypted)
    assert encrypted.read_bytes() != payload
    assert not any((tmp_path / "storage" / ".incoming").iterdir())


@pytest.mark.asyncio
async def test_infected_file_never_reaches_structural_parser_or_case_storage(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    payload = valid_pdf()
    storage_root = tmp_path / "storage"

    def parser_must_not_run(*args, **kwargs):
        raise AssertionError("structural parser ran before malware admission")

    monkeypatch.setattr("app.storage.inspect_upload", parser_must_not_run)

    with pytest.raises(UploadSecurityError) as error:
        await LocalStorageService(
            str(storage_root),
            malware_scanner=InfectedScanner(),
        ).save_telegram_file(
            bot=FakeBot(payload),
            telegram_file_id="infected",
            case_id=12,
            original_name="contract.pdf",
            mime_type="application/pdf",
            file_size=len(payload),
        )

    assert error.value.code == "malware_detected"
    assert error.value.quarantine_path
    quarantine = Path(error.value.quarantine_path)
    assert is_encrypted_file(quarantine)
    assert quarantine.read_bytes() != payload
    assert not (storage_root / "cases" / "12").exists()
    assert not any((storage_root / ".incoming").iterdir())


@pytest.mark.asyncio
async def test_production_disabled_scanner_is_fail_closed(tmp_path, monkeypatch):
    configure_keys(monkeypatch, app_env="production")
    monkeypatch.setattr(settings, "document_malware_scanner", "disabled")
    payload = valid_pdf()

    with pytest.raises(UploadSecurityError) as error:
        await LocalStorageService(str(tmp_path / "storage")).save_telegram_file(
            bot=FakeBot(payload),
            telegram_file_id="unscanned",
            case_id=13,
            original_name="contract.pdf",
            mime_type="application/pdf",
            file_size=len(payload),
        )

    assert error.value.code == "malware_scanner_not_configured"
    assert not (tmp_path / "storage" / "cases" / "13").exists()


@pytest.mark.asyncio
async def test_hash_change_between_malware_and_structural_admission_is_rejected(
    tmp_path,
    monkeypatch,
):
    configure_keys(monkeypatch)
    payload = valid_pdf()
    storage_root = tmp_path / "storage"

    with pytest.raises(UploadSecurityError) as error:
        await LocalStorageService(
            str(storage_root),
            malware_scanner=WrongHashScanner(),
        ).save_telegram_file(
            bot=FakeBot(payload),
            telegram_file_id="hash-race",
            case_id=14,
            original_name="contract.pdf",
            mime_type="application/pdf",
            file_size=len(payload),
        )

    assert error.value.code == "malware_scan_hash_mismatch"
    assert not (storage_root / "cases" / "14").exists()
    assert is_encrypted_file(Path(error.value.quarantine_path))


class _FakeConnection:
    def __init__(self, response: bytes):
        self.response = response
        self.sent: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def settimeout(self, value):
        return None

    def sendall(self, value: bytes):
        self.sent.append(bytes(value))

    def recv(self, size: int) -> bytes:
        response, self.response = self.response, b""
        return response[:size]


def test_clamav_client_uses_version_and_nul_framed_instream(tmp_path, monkeypatch):
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"hello secure upload")

    version = _FakeConnection(b"ClamAV 1.5.4/27890/Wed Oct 1 2026\0")
    scan = _FakeConnection(b"stream: OK\0")
    connections = iter([version, scan])

    def fake_create_connection(address, timeout):
        assert address == ("clamav", 3310)
        assert timeout == 5
        return next(connections)

    monkeypatch.setattr(
        "app.security.malware_scanning.socket.create_connection",
        fake_create_connection,
    )

    result = ClamAVScanner(
        host="clamav",
        port=3310,
        timeout_seconds=5,
        chunk_bytes=4096,
    )._scan_sync(sample)

    assert result.status == MalwareScanStatus.CLEAN
    assert result.engine == "clamav/clamd"
    assert result.engine_version == "ClamAV 1.5.4"
    assert result.signature_info == "27890"
    assert version.sent == [b"zVERSION\0"]
    assert scan.sent[0] == b"zINSTREAM\0"
    assert scan.sent[-1] == b"\x00\x00\x00\x00"
    assert result.sha256 == hashlib.sha256(sample.read_bytes()).hexdigest()
