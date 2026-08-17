from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_active_metric_no_longer_routes_to_unassigned_queue():
    source = read("app/api/workdesk_ui_guard.py")

    assert "_ACTIVE_METRIC_SOURCE" in source
    assert "openQueue('unassigned'" in source  # the old marker is intentionally detected
    assert "_ACTIVE_METRIC_TARGET" in source
    assert "openQueue('active',null)" in source
    assert "html.replace(_ACTIVE_METRIC_SOURCE, _ACTIVE_METRIC_TARGET, 1)" in source


def test_active_queue_is_real_and_excludes_closed_cases():
    source = read("app/api/workdesk_ui_guard.py")

    assert '@router.get("/admin/work-queues/active")' in source
    assert "Case.status.notin_(_CLOSED_CASE_STATUSES)" in source
    assert '"queue": "active"' in source
    assert "active:'Все активные дела'" in source
    assert "if(queue==='active')return''" in source


def test_active_queue_does_not_recommend_assignment_for_client_owned_or_m2_states():
    source = read("app/api/workdesk_ui_guard.py")
    block = source.split("async def guarded_active_work_queue", 1)[1].split(
        '@router.get("/admin/work-queues/unassigned")', 1
    )[0]

    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in block
    assert "Ожидать следующий шаг клиента" in block
    assert "_m2_responsibility_by_case" in block
    assert "Ожидать выбора клиентом даты и времени" in block
