from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from app.api.retention_center import (
    RETENTION_HTML,
    _run_and_commit,
    execute_deletion,
    scan_retention,
)
from app.domain.retention.case_retention_service import CaseRetentionService


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function(name: str) -> str:
    marker = f"async function {name}("
    start = RETENTION_HTML.index(marker)
    end = RETENTION_HTML.find("\nasync function ", start + 1)
    if end < 0:
        end = RETENTION_HTML.index("\nboot();", start)
    return RETENTION_HTML[start:end]


def _deletion_pipeline_source() -> str:
    """Read the deployed module, unaffected by runtime test monkeypatches."""
    module = inspect.getmodule(CaseRetentionService)
    assert module is not None and module.__file__
    return Path(module.__file__).read_text(encoding="utf-8")


def test_retention_actions_are_single_flight_per_case_and_scan():
    compact = _compact(RETENTION_HTML)

    assert "letscanPending=false" in compact
    assert "constpendingCases=newSet()" in compact
    assert "asyncfunctionwithScanAction(button,work)" in compact
    assert "asyncfunctionwithCaseAction(caseId,button,work)" in compact
    assert "pendingCases.has(caseId)" in compact
    assert "pendingCases.add(caseId)" in compact
    assert "pendingCases.delete(caseId)" in compact
    assert "scanPending=true" in compact
    assert "scanPending=false" in compact
    assert compact.count("finally") >= 2
    assert ".disabled=true" in compact
    assert ".disabled=false" in compact
    assert "data-case-id=" in compact
    assert "data-scan-action" in compact


@pytest.mark.parametrize(
    ("function_name", "success_marker", "failure_marker"),
    (
        ("hold", "установлен", "не установлен"),
        ("releaseHold", "снят", "не снят"),
        ("requestDelete", "создан", "не создан"),
        ("approve", "одобрено", "не одобрено"),
    ),
)
def test_retention_mutations_validate_confirm_and_report_results(
    function_name: str,
    success_marker: str,
    failure_marker: str,
):
    function = _function(function_name)
    compact = _compact(function)

    assert "validReason(" in function
    assert function.index("confirm(") < function.index("withCaseAction(")
    assert function.index("withCaseAction(") < function.index("await api(")
    assert "this" in RETENTION_HTML
    assert success_marker in function
    assert failure_marker in function
    assert "refreshAfter(" in function
    assert "catch(e)" in compact


def test_execute_deletion_requires_typed_case_number_and_final_confirmation():
    function = _function("executeDelete")

    typed = function.index("prompt(")
    equality = function.index("typed!==expected")
    confirm = function.index("confirm(")
    lock = function.index("withCaseAction(")
    request = function.index("await api(")

    assert typed < equality < confirm < lock < request
    assert "button.dataset.caseNumber" in function
    assert "номер дела введён неверно" in function
    assert "Последнее подтверждение" in function
    assert "Платёжный ledger, номер дела и аудит сохранятся" in function
    assert "documents_deleted" in function
    assert "messages_deleted" in function
    assert "notifications_deleted" in function
    assert "consultations_anonymized" in function
    assert "content_digest" in function


def test_scan_persist_requires_confirmation_but_dry_run_remains_available():
    function = _function("scan")
    compact = _compact(function)

    assert "if(persist&&!confirm(" in compact
    assert "persist" in function
    assert "dry_run" in function
    assert "due_count" in function
    assert "created" in function
    assert "already_tracked" in function
    assert "withScanAction(button" in function


def test_retention_feedback_is_accessible_and_alerts_are_removed():
    compact = _compact(RETENTION_HTML)

    assert 'role="status"' in RETENTION_HTML
    assert 'aria-live="polite"' in RETENTION_HTML
    assert "if(!r.ok)throw" in compact
    assert "credentials:'same-origin'" in RETENTION_HTML
    assert "cache:'no-store'" in RETENTION_HTML
    assert "alert(" not in RETENTION_HTML
    assert "try{awaitload()}catch(e)" in compact
    assert "Изменение сохранено, но список не обновился" in RETENTION_HTML


def test_legal_hold_invalidates_stale_request_and_approval():
    source = inspect.getsource(CaseRetentionService.set_legal_hold)

    assert "record.status = STATUS_DISCOVERED" in source
    assert "record.requested_at = None" in source
    assert "record.requested_by = None" in source
    assert "record.request_reason = None" in source
    assert "record.approved_at = None" in source
    assert "record.approved_by = None" in source
    assert "record.approval_comment = None" in source


def test_retention_requires_two_different_superadmins():
    request_source = inspect.getsource(CaseRetentionService.request_deletion)
    approve_source = inspect.getsource(CaseRetentionService.approve_deletion)
    compact = _compact(approve_source)

    assert "record.requested_by = actor_id" in request_source
    assert "int(record.requested_byor0)==int(actor_id)" in compact
    assert "разные суперадминистраторы" in approve_source
    assert "record.approved_by = actor_id" in approve_source


def test_retention_blocks_unsettled_operations_and_legal_hold():
    eligibility = inspect.getsource(CaseRetentionService._assert_eligible)
    settled = inspect.getsource(CaseRetentionService._assert_operationally_settled)
    settled_compact = _compact(settled)

    assert "record.legal_hold" in eligibility
    assert "retention_due_at" in eligibility
    assert "UNRESOLVED_PAYMENT_STATUSES" in settled
    assert "KNOWN_TERMINAL_PAYMENT_STATUSES" in settled
    assert "ACTIVE_CONSULTATION_STATUSES" in settled
    assert "незавершённыеилинеизвестные" in settled_compact
    assert "платёжныестатусы" in settled_compact
    assert "незавершённаяконсультация" in settled_compact


def test_retention_preflights_paths_and_rejects_symlink_escape():
    resolver = inspect.getsource(CaseRetentionService._resolve_document_path)
    unlink = inspect.getsource(CaseRetentionService._unlink_and_sync)

    assert "relative_to(root)" in resolver
    assert "is_symlink()" in resolver
    assert "выходит за границы хранилища" in resolver
    assert "not resolved.is_file()" in resolver
    assert "path.is_symlink()" in unlink
    assert "path.unlink()" in unlink
    assert "os.fsync" in unlink


def test_execute_deletion_claims_state_before_touching_filesystem():
    source = _deletion_pipeline_source()

    claim = source.index("update(CaseRetentionRecord)")
    executing = source.index("status=STATUS_EXECUTING", claim)
    durable_commit = source.index("await self.db.commit()", executing)
    unlink = source.index("self._unlink_and_sync", durable_commit)

    assert claim < executing < durable_commit < unlink
    assert "CaseRetentionRecord.legal_hold.is_(False)" in source
    assert "claim.rowcount" in source
    assert "Состояние удаления изменилось параллельно" in source
    assert "STATUS_COMPLETED" in source
    assert "STATUS_FAILED" in source


def test_execute_deletion_preserves_financial_and_audit_tombstone():
    source = _deletion_pipeline_source()

    assert "delete(DocumentAccessGrant)" in source
    assert "delete(Document)" in source
    assert "delete(Message)" in source
    assert "delete(Notification)" in source
    assert "delete(Calculation)" in source
    assert "delete(Payment)" not in source
    assert "payments_preserved" in source
    assert "audit_preserved" in source
    assert "case.content_deleted_at" in source
    assert "case.title = \"Содержимое удалено по политике хранения\"" in source


@pytest.mark.parametrize("endpoint", (scan_retention, execute_deletion))
def test_retention_endpoints_rollback_expected_and_unexpected_failures(endpoint):
    source = inspect.getsource(endpoint)

    assert "except CaseRetentionError" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 2


def test_shared_retention_transaction_boundary_rolls_back_every_failure():
    source = inspect.getsource(_run_and_commit)

    assert "except CaseRetentionError" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 2
