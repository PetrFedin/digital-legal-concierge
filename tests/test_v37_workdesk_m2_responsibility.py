from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workdesk_m2_projections_use_effective_slot_responsibility():
    assignment = read("app/api/assignment_queue.py")
    assignment_product = read("app/api/assignment_queue_product.py")
    workdesk_product = read("app/api/workdesk_product.py")
    projections = read("app/api/workdesk_projections.py")

    assert "effective_lawyer_ids_for_cases" in assignment
    assert "async def _m2_responsibility_by_case" in assignment
    assert "async def workdesk_case_responsibility" in assignment
    assert "async def consultation_queue_with_slot_lawyer" in assignment
    assert '"/admin/workdesk/cases/{case_id}/responsibility"' in assignment_product
    assert '"/admin/work-queues/consultations"' in assignment_product
    assert "guarded_workdesk_attention" in workdesk_product
    assert "effective_lawyer_ids_for_cases" in projections
    assert "async def _m2_responsibility_by_case" in projections


def test_m2_case_workspace_is_corrected_by_exact_responsibility_projection():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")

    assert "responsibility=await api('/admin/workdesk/cases/'+id+'/responsibility')" in runtime_ui
    assert "if(selected!==id||responsibility.route!=='M2')" in runtime_ui
    assert "'Юрист консультации'" in runtime_ui
    assert "'Контроль консультации'" in runtime_ui
    assert "'Время консультации'" in runtime_ui
    assert "responsibility.lawyer_name||'будет определён выбранным слотом'" in runtime_ui


def test_workdesk_ui_removes_m1_assignment_sla_shortcut_for_m2_without_literal_replacement():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")

    assert "if(selected!==id||responsibility.route!=='M2')" in runtime_ui
    assert "if(label==='SLA'||label==='Назначить перед SLA')node.remove()" in runtime_ui
    assert "_M1_SLA_SHORTCUT" not in runtime_ui
    assert "_ROUTE_AWARE_SLA_SHORTCUT" not in runtime_ui
    assert "WORKDESK_HTML.replace(" not in runtime_ui


def test_base_admin_case_workspace_also_uses_effective_m2_responsibility():
    source = read("app/api/web_admin.py")
    ui = read("app/api/workdesk_ui.py")

    assert "effective_lawyer_id_for_case" in source
    assert "effective_lawyer_ids_for_cases" in source
    assert '"responsibility_label": (' in source
    assert '"Ответственный по выбранному слоту"' in source
    assert 'select(Consultation)' in source
    assert '"consultation": (' in source
    assert "CONSULTATION_STATUS_LABELS" in source

    assert "consult=d.consultation||null" in ui
    assert "Контроль консультации" in ui
    assert "Время консультации" in ui
    assert "consult?.status_label" in ui
    assert "consult?.scheduled_at" in ui


def test_runtime_m2_patch_updates_route_aware_cells_without_reintroducing_assignment_semantics():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")

    assert "caseCell('Контроль консультации')||caseCell('SLA')" in runtime_ui
    assert "caseCell('Время консультации')||caseCell('Срок')" in runtime_ui
    assert "responsibility.lawyer_name||'будет определён выбранным слотом'" in runtime_ui
