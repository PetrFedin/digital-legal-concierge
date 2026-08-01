from __future__ import annotations

import inspect
import re

import pytest

from app.api.sla_center import (
    SLA_CENTER_HTML,
    acknowledge_sla,
    run_sla_check,
)
from app.domain.cases.sla_service import CaseSLAService


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function(name: str) -> str:
    marker = f"async function {name}("
    start = SLA_CENTER_HTML.index(marker)
    end = SLA_CENTER_HTML.find("\nasync function ", start + 1)
    if end < 0:
        end = SLA_CENTER_HTML.index("\nboot();", start)
    return SLA_CENTER_HTML[start:end]


def test_sla_writes_are_single_flight_for_run_and_case_actions():
    compact = _compact(SLA_CENTER_HTML)

    assert "letrunPending=false" in compact
    assert "constpendingCases=newSet()" in compact
    assert "asyncfunctionwithRunAction(button,work)" in compact
    assert "asyncfunctionwithCaseAction(id,button,work)" in compact
    assert "if(runPending)return" in compact
    assert "pendingCases.has(id)" in compact
    assert "pendingCases.add(id)" in compact
    assert "pendingCases.delete(id)" in compact
    assert compact.count("finally") >= 3
    assert ".disabled=true" in compact
    assert ".disabled=false" in compact
    assert "data-sla-run" in compact
    assert "data-case-id=" in compact
    assert "data-sla-status=" in compact
    assert "data-escalation-level=" in compact
    assert "runCheck(this)" in compact
    assert "ack(${x.case_id},this)" in compact


def test_sla_filter_loads_abort_stale_requests_and_restore_controls():
    compact = _compact(SLA_CENTER_HTML)
    function = _function("load")

    assert "letloadController=null" in compact
    assert "newAbortController()" in compact
    assert "loadController.abort()" in compact
    assert "signal:controller.signal" in compact
    assert "if(loadController!==controller)" in compact
    assert "e.name==='AbortError'" in compact
    assert "data-sla-filter" in compact
    assert "loadController=null" in compact
    assert "finally" in function


def test_manual_sla_run_confirms_and_reports_examined_and_escalated_counts():
    function = _function("runCheck")

    assert function.index("confirm(") < function.index("withRunAction(")
    assert function.index("withRunAction(") < function.index("await api(")
    assert "result.examined" in function
    assert "result.escalated_count" in function
    assert "Проверка SLA не выполнена" in function
    assert "refreshAfter(" in function


def test_acknowledgement_validates_confirms_and_sends_displayed_snapshot():
    function = _function("ack")
    compact = _compact(function)

    assert "button.dataset.slaStatus" in function
    assert "button.dataset.escalationLevel" in function
    assert "Number.isInteger(expectedLevel)" in function
    assert "Snapshot SLA отсутствует или повреждён" in function
    assert "comment.trim().length<5" in compact
    assert function.index("confirm(") < function.index("withCaseAction(")
    assert function.index("withCaseAction(") < function.index("await api(")
    assert "expected_sla_status:expectedStatus" in compact
    assert "expected_escalation_level:expectedLevel" in compact
    assert "Исходное нарушение останется в аудите" in function
    assert "новый срок" in function
    assert "не подтверждён" in function
    assert "refreshAfter(" in function


def test_sla_feedback_is_accessible_and_transport_is_not_cached():
    compact = _compact(SLA_CENTER_HTML)

    assert 'role="status"' in SLA_CENTER_HTML
    assert 'aria-live="polite"' in SLA_CENTER_HTML
    assert "credentials:'same-origin'" in SLA_CENTER_HTML
    assert "cache:'no-store'" in SLA_CENTER_HTML
    assert "if(!r.ok)throw" in compact
    assert "alert(" not in SLA_CENTER_HTML
    assert "SLA Center не загружен" in SLA_CENTER_HTML
    assert "Изменение сохранено, но список не обновился" in SLA_CENTER_HTML


def test_acknowledgement_service_locks_and_rejects_stale_snapshots():
    lock_source = inspect.getsource(CaseSLAService._lock_case)
    source = inspect.getsource(CaseSLAService.acknowledge_overdue)
    compact = _compact(source)

    assert ".with_for_update()" in lock_source
    assert "expected_sla_status" in source
    assert "expected_escalation_level" in source
    assert "normalized_expected_status!=str(case.sla_status).upper()" in compact
    assert "normalized_expected_level!=int(case.escalation_levelor0)" in compact
    assert "SLA изменился после загрузки экрана" in source
    assert "Уровень эскалации изменился после загрузки экрана" in source
    assert "case.sla_statusnotin{" in compact
    assert "SLA_FIRST_RESPONSE_OVERDUE" in source
    assert "SLA_ACTION_OVERDUE" in source
    assert "case.escalation_level=0" in compact
    assert 'action="CASE_SLA_ACKNOWLEDGED"' in source
    assert 'event_code="CASE_SLA_ACKNOWLEDGED"' in source


def test_acknowledgement_endpoint_forwards_snapshot_and_rolls_back_all_failures():
    source = inspect.getsource(acknowledge_sla)

    assert 'expected_sla_status=payload.get("expected_sla_status")' in source
    assert 'payload.get(\n                "expected_escalation_level"' in source
    assert "except LookupError" in source
    assert "except CaseSLAError" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 3


def test_escalation_uses_skip_locked_and_deduplicated_notifications():
    source = inspect.getsource(CaseSLAService.escalate_overdue_cases)
    compact = _compact(source)

    assert ".with_for_update(skip_locked=True)" in source
    assert "case.escalation_level=int(case.escalation_levelor0)+1" in compact
    assert "case.sla_due_at=now+timedelta(hours=repeat_hours)" in compact
    assert "CASE_SLA_FIRST_RESPONSE_OVERDUE" in source
    assert "CASE_SLA_ACTION_OVERDUE" in source
    assert "dedupe_key=" in source
    assert "case.escalation_level" in source
    assert "overdue_due_at.timestamp()" in source
    assert '"examined": len(cases)' in source
    assert '"escalated_count": len(escalated)' in source


def test_manual_sla_run_rolls_back_expected_and_unexpected_failures():
    source = inspect.getsource(run_sla_check)

    assert "except CaseSLAError" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 2
    assert "await db.commit()" in source


@pytest.mark.parametrize("endpoint", (acknowledge_sla, run_sla_check))
def test_sla_write_endpoints_commit_only_after_service_success(endpoint):
    source = inspect.getsource(endpoint)
    service_call = source.index("CaseSLAService(db)")
    commit = source.index("await db.commit()")

    assert service_call < commit
