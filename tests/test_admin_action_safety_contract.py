from __future__ import annotations

import inspect
import re

import pytest

from app.api.admin import (
    admin_record_enforcement_receipt,
    auto_assign,
    create_lawyer,
    manual_confirm_payment,
    manual_status,
    set_setting,
)
from app.api.web_admin import ADMIN_HTML
from app.domain.cases.assignment_service import CaseAssignmentService
from app.system.settings_service import SettingsService


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function(name: str) -> str:
    marker_options = (f"async function {name}(", f"function {name}(")
    starts = [ADMIN_HTML.find(marker) for marker in marker_options]
    start = min(index for index in starts if index >= 0)
    end = ADMIN_HTML.find("\nasync function ", start + 1)
    sync_end = ADMIN_HTML.find("\nfunction ", start + 1)
    candidates = [index for index in (end, sync_end) if index >= 0]
    if candidates:
        end = min(candidates)
    else:
        end = ADMIN_HTML.index("\nboot();", start)
    return ADMIN_HTML[start:end]


def test_admin_ui_uses_personal_session_and_no_legacy_token_fallback():
    compact = _compact(ADMIN_HTML)

    assert "dev-admin-token" not in ADMIN_HTML
    assert "session.api_token" in ADMIN_HTML
    assert "Персональнаяadmin-сессиянезагружена" in compact
    assert "credentials:'same-origin'" in ADMIN_HTML
    assert "cache:'no-store'" in ADMIN_HTML
    assert "if(r.status===401)" in compact
    assert 'role="status"' in ADMIN_HTML
    assert 'aria-live="polite"' in ADMIN_HTML


def test_admin_write_actions_share_a_single_flight_guard():
    compact = _compact(ADMIN_HTML)

    assert "constpending=newSet()" in compact
    assert "asyncfunctionwithAction(key,button,selector,work" in compact
    assert "pending.has(key)" in compact
    assert "pending.add(key)" in compact
    assert "pending.delete(key)" in compact
    assert "finally" in compact
    assert ".disabled=true" in compact
    assert ".disabled=false" in compact
    assert "aria-busy" in ADMIN_HTML

    for call in (
        "runScheduler(this)",
        "autoAssign(${x.id},this)",
        "confirmPayment(${p.id},${p.case_id},this)",
        "saveStatus(${id},this)",
        "createLawyer(this)",
        "saveSetting(this)",
    ):
        assert call in compact


@pytest.mark.parametrize(
    ("function_name", "guard_key", "success_marker", "failure_marker"),
    (
        ("runScheduler", "global:scheduler", "Плановые проверки завершены", "не выполнены"),
        ("autoAssign", "case:", "назначено", "не назначено"),
        ("confirmPayment", "payment:", "подтверждён", "не подтверждён"),
        ("saveStatus", "case:", "сохранён", "не изменён"),
        ("createLawyer", "global:create-lawyer", "создан", "не создан"),
        ("saveSetting", "setting:", "сохранена", "не сохранена"),
    ),
)
def test_admin_writes_confirm_or_validate_and_report_exact_results(
    function_name: str,
    guard_key: str,
    success_marker: str,
    failure_marker: str,
):
    function = _function(function_name)

    assert "withAction(" in function
    assert guard_key in function
    assert "try{" in function
    assert "catch(e)" in function
    assert success_marker in function
    assert failure_marker in function
    assert "экран не обновился" in function or "список не обновился" in function or "раздел не обновился" in function


def test_admin_ui_sends_stale_state_snapshots_for_sensitive_writes():
    compact = _compact(ADMIN_HTML)

    assert "expected_status:expected" in compact
    assert "expected_status:expectedStatus" in compact
    assert "expected_lawyer_id:expectedLawyer" in compact
    assert "expected_updated_at:expected" in compact
    assert "data-expected-status=" in compact
    assert "data-expected-lawyer=" in compact
    assert "data-expected-updated-at=" in compact


def test_admin_status_change_validates_comment_and_requires_confirmation():
    function = _function("saveStatus")

    assert "comment.length<5" in function
    assert "next===expected" in function
    assert function.index("confirm(") < function.index("withAction(")
    assert function.index("withAction(") < function.index("await api(")


def test_admin_setting_service_locks_and_rejects_stale_updates():
    source = inspect.getsource(SettingsService.set_value)
    compact = _compact(source)

    assert ".with_for_update()" in source
    assert "expected_updated_at" in source
    assert "setting.updated_at.isoformat()!=expected_updated_at" in compact
    assert "Настройка уже изменена другим пользователем" in source
    assert "await self.db.flush()" in source


def test_case_assignment_service_checks_snapshot_after_row_lock():
    lock_source = inspect.getsource(CaseAssignmentService._get_case)
    check_source = inspect.getsource(CaseAssignmentService._assert_expected_snapshot)
    auto_source = inspect.getsource(CaseAssignmentService.auto_assign_case)

    assert "query = query.with_for_update()" in lock_source
    assert "expected_lawyer_id" in check_source
    assert "expected_status" in check_source
    assert "Назначение дела изменилось после загрузки экрана" in check_source
    assert "Статус дела изменился после загрузки экрана" in check_source
    assert "self._assert_expected_snapshot" in auto_source


def test_manual_status_locks_case_and_rejects_stale_status():
    source = inspect.getsource(manual_status)
    compact = _compact(source)

    assert ".with_for_update()" in source
    assert "expected_status" in source
    assert "str(case.status)!=str(expected_status)" in compact
    assert "Статус дела изменился после загрузки экрана" in source
    assert "force=True" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 3


def test_admin_enforcement_receipt_locks_case_and_checks_snapshot():
    source = inspect.getsource(admin_record_enforcement_receipt)
    compact = _compact(source)

    assert ".with_for_update()" in source
    assert "expected_status" in source
    assert "expected_updated_at" in source
    assert "str(case.status)!=str(expected_status)" in compact
    assert "case.updated_at.isoformat()!=str(expected_updated_at)" in compact
    assert "EnforcementService(db).record_receipt" in source
    assert 'event_code="M1_MONEY_RECEIVED"' in source
    assert source.count("await db.rollback()") >= 3


def test_manual_payment_confirmation_locks_payment_and_case():
    source = inspect.getsource(manual_confirm_payment)

    assert source.count(".with_for_update()") >= 2
    assert "expected_status" in source
    assert "Статус платежа изменился после загрузки экрана" in source
    assert "payment_can_be_manually_confirmed" in source
    assert "PaymentWebhookService(db).process_successful_payment" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 2


@pytest.mark.parametrize(
    "endpoint",
    (
        set_setting,
        create_lawyer,
        auto_assign,
        manual_status,
        admin_record_enforcement_receipt,
        manual_confirm_payment,
    ),
)
def test_admin_write_endpoints_rollback_unexpected_failures(endpoint):
    source = inspect.getsource(endpoint)

    assert "except Exception" in source
    assert "await db.rollback()" in source


def test_lawyer_creation_validates_identity_capacity_and_duplicates():
    source = inspect.getsource(create_lawyer)

    assert "email.lower()" in source or ".lower()" in source
    assert "@" in source
    assert "workload_limit < 1 or workload_limit > 500" in source
    assert "Lawyer.email == email" in source
    assert ".with_for_update()" in source
    assert "Юрист с таким email уже существует" in source
