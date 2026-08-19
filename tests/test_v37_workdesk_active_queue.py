from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_active_metric_opens_matching_active_queue_projection():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")
    product = read("app/api/workdesk_product.py")

    assert "titles.active='Все активные дела'" in runtime_ui
    assert "activeMetric.onclick=()=>openQueue('active',null)" in runtime_ui
    assert "if(queue==='active')return''" in runtime_ui
    assert '"/admin/work-queues/active"' in product
    assert "guarded_active_work_queue" in product
    assert "_ACTIVE_METRIC_SOURCE" not in runtime_ui
    assert "_ACTIVE_METRIC_TARGET" not in runtime_ui


def test_active_queue_is_real_and_excludes_closed_cases():
    implementation = read("app/api/workdesk_projections.py")
    product = read("app/api/workdesk_product.py")

    assert "async def guarded_active_work_queue(" in implementation
    assert "Case.status.notin_(_CLOSED_CASE_STATUSES)" in implementation
    assert '"queue": "active"' in implementation
    assert '"/admin/work-queues/active"' in product
    assert "guarded_active_work_queue" in product


def test_active_queue_does_not_recommend_assignment_for_client_owned_or_m2_states():
    source = read("app/api/workdesk_projections.py")
    block = source.split("async def guarded_active_work_queue", 1)[1].split(
        "async def guarded_workdesk_attention", 1
    )[0]

    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in block
    assert "Ожидать следующий шаг клиента" in block
    assert "_m2_responsibility_by_case" in block
    assert "Ожидать выбора клиентом даты и времени" in block
