from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_client_search_pulls_related_cases_into_results():
    source = read("app/api/search_center.py")

    assert "client_ids = [int(item.id) for item in users]" in source
    assert "Case.client_id.in_(client_ids)" in source
    assert "latest_case_by_client" in source
    assert '"latest_case_id": latest_case_by_client.get(int(item.id))' in source


def test_client_rows_have_direct_workdesk_handoff():
    source = read("app/api/search_center.py")

    assert "case_field='latest_case_id'" in source
    assert "target_prefix='/admin/workdesk/ui?case_id='" in source
    assert "Поиск по клиенту сразу показывает связанные дела" in source


def test_search_ui_recovers_role_mismatch_without_raw_json_dead_end():
    source = read("app/api/search_center.py")
    block = source.split('@router.get("/search-center/ui"', 1)[1]

    assert "except DocumentAccessError" in block
    assert "except HTTPException" in block
    assert "error.status_code in {403, 409}" in block
    assert 'RedirectResponse(url="/admin-ui", status_code=303)' in block
