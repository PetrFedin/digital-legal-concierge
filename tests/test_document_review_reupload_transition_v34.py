from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_uploaded_replacement_is_distinct_from_waiting_for_client_file():
    source = read("app/domain/documents/document_review_context.py")

    replacement_branch = source.index("elif replacement:")
    uploaded_branch = source.index("elif uploaded:")
    docs_requested_branch = source.index(
        "elif case_status == CaseStatus.M1_DOCS_REQUESTED:"
    )

    assert replacement_branch < uploaded_branch < docs_requested_branch
    assert 'primary_action = "wait_client_submit"' in source
    assert "Новая версия уже загружена" in source
    assert "ещё не передана в юридическую очередь проверки" in source
