from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workdesk_m2_projections_use_effective_slot_responsibility():
    source = read("app/api/workdesk_ui_guard.py")

    assert "effective_lawyer_ids_for_cases" in source
    assert "async def _m2_responsibility_by_case" in source
    assert '@router.get("/admin/work-queues/consultations")' in source
    assert '@router.get("/admin/workdesk/attention")' in source
    assert '@router.get("/admin/case-workspace/{case_id}")' in source
    assert 'str(case.route or "") != "M2"' in source
    assert 'item["lawyer_id"] = lawyer_id' in source
    assert 'item["lawyer_name"] = lawyer_name' in source
    assert 'case_payload["lawyer_id"] = lawyer_id' in source
    assert 'case_payload["lawyer_name"] = lawyer_name' in source


def test_m2_case_workspace_does_not_recommend_generic_case_assignment():
    source = read("app/api/workdesk_ui_guard.py")
    block = source.split("async def guarded_case_workspace", 1)[1].split(
        '@router.get("/admin/workdesk/ui"', 1
    )[0]

    assert "case.next_action" in block
    assert "Проверить актуальное состояние консультации" in block
    assert "Ответственный определится после выбора клиентом времени" in block
    assert "Назначить ответственного юриста" not in block


def test_workdesk_ui_hides_m1_assignment_sla_shortcut_for_m2():
    source = read("app/api/workdesk_ui_guard.py")

    assert "_M1_SLA_SHORTCUT" in source
    assert "_ROUTE_AWARE_SLA_SHORTCUT" in source
    assert "d.case.route==='M2'" in source
    assert "M1 SLA здесь не назначается вручную" in source
    assert "html.replace(_M1_SLA_SHORTCUT, _ROUTE_AWARE_SLA_SHORTCUT, 1)" in source
