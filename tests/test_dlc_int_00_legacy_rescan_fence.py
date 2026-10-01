from pathlib import Path

import pytest

from app.security.document_scanning import rescan_legacy_documents


@pytest.mark.asyncio
async def test_historical_rescan_is_fail_closed_until_controlled_readmission():
    result = await rescan_legacy_documents(None, limit=50)  # type: ignore[arg-type]

    assert result == {
        "verified": 0,
        "quarantined": 0,
        "missing": 0,
        "duplicate": 0,
        "scan_error": 0,
        "blocked_pending_controlled_readmission": 1,
    }


def test_historical_rescan_contains_no_structural_parser_or_filesystem_dereference():
    source = Path("app/security/document_scanning.py").read_text(encoding="utf-8")

    assert "inspect_upload" not in source
    assert "Path(document.file_path)" not in source
    assert "malware admission" in source
    assert "Issue #172" in source
