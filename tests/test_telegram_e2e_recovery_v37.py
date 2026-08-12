from pathlib import Path
from types import SimpleNamespace

from app.bot.client_case_view import CLIENT_ACTIONS
from app.bot.consultation_result import consultation_result_view
from app.domain.cases.case_transition_policy import allowed_next_statuses
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus


SCREENS = Path("app/bot/screens")


def _screen_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in SCREENS.glob("*.py"))


def test_every_client_primary_callback_has_a_telegram_owner():
    sources = _screen_sources()
    missing = sorted(
        {
            action.callback
            for action in CLIENT_ACTIONS.values()
            if f'"{action.callback}"' not in sources
            and f"'{action.callback}'" not in sources
        }
    )
    assert not missing, f"Client actions without Telegram handlers: {missing}"


def test_terminal_consultation_primary_callbacks_have_telegram_owners():
    sources = _screen_sources()
    fixtures = [
        SimpleNamespace(status=ConsultationStatus.DONE, decision="close"),
        SimpleNamespace(status=ConsultationStatus.DONE, decision="to_m1"),
        SimpleNamespace(status=ConsultationStatus.DONE, decision="follow_up"),
        SimpleNamespace(status=ConsultationStatus.DONE, decision="other"),
        SimpleNamespace(status=ConsultationStatus.CLIENT_NO_SHOW, decision=None),
        SimpleNamespace(status=ConsultationStatus.LAWYER_NO_SHOW, decision=None),
        SimpleNamespace(status=ConsultationStatus.CANCELLED, decision=None),
        SimpleNamespace(status=ConsultationStatus.RESCHEDULED, decision=None),
        SimpleNamespace(status=ConsultationStatus.CLOSED, decision=None),
    ]
    callbacks = {
        consultation_result_view(item).primary_callback
        for item in fixtures
        if consultation_result_view(item) is not None
    }
    missing = sorted(
        callback
        for callback in callbacks
        if f'"{callback}"' not in sources and f"'{callback}'" not in sources
    )
    assert not missing, f"Consultation result callbacks without handlers: {missing}"


def test_rejected_m1_has_both_real_exit_paths_and_precedes_old_m2_contact_flow():
    allowed = allowed_next_statuses(CaseStatus.M1_REJECTED)
    assert CaseStatus.M2_DESCRIPTION_PENDING in allowed
    assert CaseStatus.M1_CLOSED in allowed

    bot_source = Path("app/bot/bot.py").read_text(encoding="utf-8")
    assert "m1_rejection_recovery.router" in bot_source
    assert bot_source.index("m1_rejection_recovery.router") < bot_source.index(
        "consultation_intake.router"
    )

    recovery = Path("app/bot/screens/m1_rejection_recovery.py").read_text(
        encoding="utf-8"
    )
    assert '"m1_rejected_to_m2"' in recovery
    assert '"m1_rejected_close_confirm"' in recovery
    assert '"message_create"' in recovery
    assert "transfer_to_m2" in recovery
    assert "CaseStatus.M1_CLOSED" in recovery

    guard = Path("app/bot/consultation_route_guard.py").read_text(encoding="utf-8")
    assert 'event.data == "contact_lawyer"' in guard
    assert "CaseStatus.M1_REJECTED" in guard
    assert "return await handler(event, data)" in guard

    terminal_results = Path("app/bot/screens/consultation_results.py").read_text(
        encoding="utf-8"
    )
    start = terminal_results.index("class TerminalContactLawyerFilter")
    end = terminal_results.index("def _format_scheduled_at", start)
    terminal_filter = terminal_results[start:end]
    assert "CaseStatus.M1_REJECTED" in terminal_filter
    assert "return False" in terminal_filter


def test_document_workspace_is_role_safe_and_mobile_friendly():
    source = Path("app/api/document_access_portal.py").read_text(encoding="utf-8")
    assert "/message-center/ui?case_id=" in source
    assert "/lawyer/workspace/ui" in source
    assert "/admin/workdesk/ui" in source
    assert "@media(max-width:580px)" in source
    assert "однораз" in source.lower()
    assert "/document-access/documents/" in source
    assert "/document-access/cases/" in source
