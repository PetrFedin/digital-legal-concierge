from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_auto_assignment_retry_uses_durable_latest_assignment_provenance():
    source = read("app/domain/cases/assignment_service.py")
    latest = source.split("async def _latest_assignment_mutation", 1)[1].split(
        "async def _is_exact_auto_assign_retry", 1
    )[0]
    exact = source.split("async def _is_exact_auto_assign_retry", 1)[1].split(
        "async def assign_case", 1
    )[0]

    assert ".limit(" not in latest
    assert "AuditLog.action.in_(ASSIGNMENT_MUTATION_ACTIONS)" in latest
    assert ".order_by(AuditLog.id.desc())" in latest
    assert 'event.action != "case_lawyer_assigned"' in exact
    assert "event.actor_type == actor_type" in exact
    assert "event.actor_id == actor_id" in exact
    assert "event.comment == comment" in exact
    assert 'old_value.get("assigned_lawyer_id") is None' in exact
    assert "recorded_lawyer_id == int(case.assigned_lawyer_id)" in exact


def test_auto_assignment_stale_snapshot_cannot_be_misreported_as_success():
    source = read("app/domain/cases/assignment_service.py")
    auto = source.split("async def auto_assign_case", 1)[1].split(
        "async def assign_queue", 1
    )[0]

    assert "expected_status is not None" in auto
    assert "str(case.status) != str(expected_status)" in auto
    assert "normalized_expected is None" in auto
    assert "case.assigned_lawyer_id is not None" in auto
    assert "await self._is_exact_auto_assign_retry(" in auto
    assert "Назначение дела изменилось после загрузки экрана" in auto
    assert auto.index("await self._is_exact_auto_assign_retry(") < auto.index(
        "self._assert_expected_snapshot("
    )
    assert auto.index("self._assert_expected_snapshot(") < auto.index(
        "await self._locked_active_lawyers()"
    )


def test_admin_auto_assignment_command_supplies_actor_and_expected_snapshot():
    source = read("app/api/admin_impl.py")
    endpoint = source.split('@router.post("/cases/{case_id}/auto-assign")', 1)[1].split(
        '@router.post("/scheduler/run-once")', 1
    )[0]

    assert "actor = require_admin(x_admin_token)" in endpoint
    assert "actor_id=actor_id_from_token(actor)" in endpoint
    assert 'comment="Автоматическое назначение из административной панели"' in endpoint
    assert 'expected_lawyer_id=snapshot.get("expected_lawyer_id")' in endpoint
    assert 'expected_status=snapshot.get("expected_status")' in endpoint
    assert "raise HTTPException(status_code=409" in endpoint


def test_workdesk_auto_assignment_sends_unassigned_and_status_snapshot():
    source = read("app/api/workdesk_ui.py")
    assign = source.split("async function assign(id,b)", 1)[1].split(
        "async function reload(b)", 1
    )[0]

    assert "await api('/admin/case-workspace/'+id)" in assign
    assert "expected_lawyer_id:null" in assign
    assert "expected_status:cached?.status||d.case.status" in assign
    assert "await openCase(id)" in assign
