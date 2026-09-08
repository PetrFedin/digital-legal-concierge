from pathlib import Path


def test_authorized_document_download_binds_storage_to_authorized_case() -> None:
    source = Path("app/api/document_access.py").read_text(encoding="utf-8")

    call_anchor = "content = LocalStorageService().read_document_bytes("
    assert call_anchor in source
    call = source.split(call_anchor, 1)[1].split(")\n", 1)[0]

    assert "document.file_path" in call
    assert "expected_case_id=int(case.id)" in call
    assert "expected_sha256=document.sha256" in call
    assert "encrypted_data_key=document.encrypted_data_key" in call
    assert "encrypted_data_key_nonce=document.encrypted_data_key_nonce" in call
