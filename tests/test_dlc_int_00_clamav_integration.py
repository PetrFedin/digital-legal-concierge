from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config import settings
from app.security.file_uploads import UploadSecurityError
from app.security.malware_scanning import (
    ClamAVScanner,
    MalwareScanStatus,
    malware_scanner_readiness,
)


pytestmark = pytest.mark.skipif(
    os.getenv("CLAMAV_INTEGRATION") != "1",
    reason="real clamd sidecar is required only by the dedicated integration job",
)


def _scanner() -> ClamAVScanner:
    return ClamAVScanner(
        host=os.getenv("CLAMAV_HOST", "127.0.0.1"),
        port=int(os.getenv("CLAMAV_PORT", "3310")),
        timeout_seconds=float(os.getenv("CLAMAV_TIMEOUT_SECONDS", "15")),
    )


@pytest.mark.asyncio
async def test_live_clamd_accepts_clean_bytes_and_reports_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = tmp_path / "clean.txt"
    sample.write_bytes(b"Digital Legal Concierge clean ClamAV integration probe")

    result = await _scanner().scan(sample)

    assert result.status is MalwareScanStatus.CLEAN
    assert result.engine == "clamav/clamd"
    assert result.sha256
    assert result.scanned_at.tzinfo is not None

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "document_malware_scanner", "clamav")
    monkeypatch.setattr(settings, "clamav_host", os.getenv("CLAMAV_HOST", "127.0.0.1"))
    monkeypatch.setattr(settings, "clamav_port", int(os.getenv("CLAMAV_PORT", "3310")))
    monkeypatch.setattr(
        settings,
        "clamav_timeout_seconds",
        int(os.getenv("CLAMAV_TIMEOUT_SECONDS", "15")),
    )

    readiness = await malware_scanner_readiness()

    assert readiness["required"] is True
    assert readiness["available"] is True
    assert readiness["engine"] == "clamav/clamd"


@pytest.mark.asyncio
async def test_live_clamd_rejects_eicar_test_signature(tmp_path: Path) -> None:
    # EICAR is a harmless antivirus-test signature used to verify detection.
    eicar = (
        b"X5O!P%@AP[4\\PZX54(P^)"
        b"7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )
    sample = tmp_path / "eicar.com"
    sample.write_bytes(eicar)

    with pytest.raises(UploadSecurityError) as error:
        await _scanner().scan(sample)

    assert error.value.code == "malware_detected"
    assert error.value.sha256
    assert error.value.security_reason
    assert "malware=INFECTED" in error.value.security_reason
    assert "engine=clamav/clamd" in error.value.security_reason
