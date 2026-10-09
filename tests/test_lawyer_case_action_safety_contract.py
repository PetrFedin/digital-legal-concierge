from __future__ import annotations

import inspect
import re

import pytest

from app.api.lawyer import (
    LAWYER_HTML,
    accept,
    assigned_case,
    assert_case_snapshot,
    lawyer_cases,
    record_enforcement_receipt,
    request_docs,
    transfer_to_m2,
    update_enforcement,
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _action_function(name: str) -> str:
    start = LAWYER_HTML.index(f"async function {name}(")
    end = LAWYER_HTML.find("\nasync function ", start + 1)
    if end < 0:
        end = LAWYER_HTML.index("\nboot();", start)
    return LAWYER_HTML[start:end]


def test_lawyer_ui_exposes_assigned_cases_and_locks_each_case_action():
    compact = _compact(LAWYER_HTML)

    assert 'id="casesContent"' in LAWYER_HTML
    assert "api('/lawyer/cases'" in LAWYER_HTML
    assert "constpendingConsultations=newSet(),pendingCases=newSet()" in compact
    assert "asyncfunctionwithCaseAction(id,button,work)" in compact
    assert "pendingCases.has(id)" in compact
    assert "pendingCases.add(id)" in compact
    assert "pendingCases.delete(id)" in compact
    assert "data-expected-status=" in LAWYER_HTML
    assert "data-expected-updated-at=" in LAWYER_HTML
    assert 'constattrs=`data-case-id="${x.case_id}"' in compact
    assert "acceptCase(${x.case_id},this)" in LAWYER_HTML
    assert "requestDocuments(${x.case_id},this)" in LAWYER_HTML
    assert "transferToM2(${x.case_id},this)" in LAWYER_HTML
    assert "updateEnforcement(${x.case_id},this)" in LAWYER_HTML
    assert "recordReceipt(${x.case_id},this,false)" in LAWYER_HTML
    assert "recordReceipt(${x.case_id},this,true)" in LAWYER_HTML


@pytest.mark.parametrize(
    (
        "function_name",
        "path_suffix",
        "minimum_length",
        "success_marker",
        "failure_marker",
    ),
    (
        ("acceptCase", "/accept", 5, "Дело #", "не принято"),
        (
            "requestDocuments",
            "/request-documents",
            5,
            "Запрос документов",
            "не сохранён",
        ),
        (
            "transferToM2",
            "/transfer-to-m2",
            10,
            "переведено в маршрут",
            "не переведено",
        ),
    ),
)
def test_case_actions_validate_confirm_send_snapshot_and_report_result(
    function_name: str,
    path_suffix: str,
    minimum_length: int,
    success_marker: str,
    failure_marker: str,
):
    function = _action_function(function_name)
    compact = _compact(function)

    assert f".trim().length<{minimum_length}" in compact
    assert function.index("confirm(") < function.index("withCaseAction(")
    assert function.index("withCaseAction(") < function.index("await api(")
    assert path_suffix in function
    assert "expected_status:expectedStatus" in compact
    assert "expected_updated_at:expectedUpdatedAt" in compact
    assert success_marker in function
    assert failure_marker in function
    assert "список не обновился" in function


def test_case_reads_are_personal_and_action_availability_matches_transition_policy():
    source = inspect.getsource(lawyer_cases)
    compact = _compact(source)

    assert "Case.assigned_lawyer_id==actor.lawyer.id" in compact
    assert "CaseStatus.M1_LAWYER_REVIEW" in source
    assert "CaseStatus.M1_DOCUMENTS_RECEIVED" in source
    assert '"can_accept"' in source
    assert '"can_request_documents"' in source
    assert '"can_transfer_to_m2"' in source
    assert "CaseStatus.M1_DOCUMENTS_PENDING" in source
    assert "CaseStatus.M1_DOCS_REQUESTED" in source
    assert '"updated_at"' in source
    assert '"can_update_enforcement"' in source
    assert '"can_record_money_received"' in source
    assert '"received_amount"' in source


def test_assigned_case_supports_row_lock_before_assignment_check():
    source = inspect.getsource(assigned_case)

    assert ".with_for_update()" in source
    assert source.index("with_for_update") < source.index("assigned_lawyer_id")
    assert "for_update: bool = False" in source


def test_snapshot_rejects_changed_status_or_row_version():
    source = inspect.getsource(assert_case_snapshot)

    assert "expected_status" in source
    assert "expected_updated_at" in source
    assert source.count("status_code=409") == 2
    assert "case.updated_at.isoformat()" in source


@pytest.mark.parametrize(
    "endpoint",
    (
        accept,
        request_docs,
        transfer_to_m2,
        update_enforcement,
        record_enforcement_receipt,
    ),
)
def test_case_action_endpoints_lock_recheck_and_rollback_every_failure(endpoint):
    source = inspect.getsource(endpoint)
    compact = _compact(source)

    assert "for_update=True" in compact
    assert "assert_case_snapshot(" in source
    assert 'get("expected_status")' in source
    assert 'get("expected_updated_at")' in source
    assert "except HTTPException" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 3


def test_enforcement_ui_requires_confirmation_snapshot_and_explicit_finality():
    update = _action_function("updateEnforcement")
    receipt = _action_function("recordReceipt")
    compact_update = _compact(update)
    compact_receipt = _compact(receipt)

    assert "/enforcement" in update
    assert "expected_status:expectedStatus" in compact_update
    assert "expected_updated_at:expectedUpdatedAt" in compact_update
    assert "confirm(" in update

    assert "/enforcement/receipt" in receipt
    assert "expected_status:expectedStatus" in compact_receipt
    assert "expected_updated_at:expectedUpdatedAt" in compact_receipt
    assert "final:final" in compact_receipt
    assert "Сумма этого поступления" in receipt
    assert "confirm(" in receipt


def test_lawyer_transport_and_reload_are_observable_and_non_cached():
    compact = _compact(LAWYER_HTML)

    assert "credentials:'same-origin'" in compact
    assert "cache:'no-store'" in compact
    assert "AbortController" in LAWYER_HTML
    assert 'role="status"' in LAWYER_HTML
    assert 'aria-live="polite"' in LAWYER_HTML
    assert "if(!r.ok)throw" in compact
