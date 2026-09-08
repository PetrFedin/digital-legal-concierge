from types import SimpleNamespace

from app.bot.client_case_view import (
    DocumentOverview,
    _priority_action,
    client_action_for,
)


def _case(status: str):
    return SimpleNamespace(status=status)


def _documents(*, review_count: int = 0):
    return DocumentOverview(
        current_count=max(review_count, 1),
        archived_count=0,
        uploaded_count=0,
        review_count=review_count,
        approved_count=0,
        replacement_count=0,
        summary="1 актуальный",
        blocker=None,
        latest_updated_at=None,
        legacy_attention_count=0,
    )


def test_waiting_claim_period_opens_read_only_court_status_instead_of_refresh_loop():
    action = client_action_for(_case("M1_WAITING_30_DAYS"))
    assert action is not None
    assert action.label == "Открыть срок ожидания"
    assert action.callback == "court_status"
    assert "сам не открывает судебный этап" in action.description


def test_court_stage_opens_existing_court_status_screen():
    action = client_action_for(_case("M1_COURT_STAGE"))
    assert action is not None
    assert action.callback == "court_status"


def test_enforcement_stage_uses_read_only_case_history():
    action = client_action_for(_case("M1_ENFORCEMENT"))
    assert action is not None
    assert action.label == "Следить за исполнением"
    assert action.callback == "case_history_open"


def test_document_review_stage_keeps_useful_documents_action_visible():
    action = _priority_action(
        _case("M1_LAWYER_REVIEW"),
        _documents(review_count=1),
    )
    assert action is not None
    assert action.label == "Открыть документы"
    assert action.callback == "documents_open"


def test_transient_paid_stages_point_to_financial_history_not_duplicate_payment_creation():
    for status in (
        "M1_PAYMENT_30000_RECEIVED",
        "M1_PAYMENT_70000_RECEIVED",
        "M1_SUCCESS_FEE_RECEIVED",
    ):
        action = client_action_for(_case(status))
        assert action is not None
        assert action.callback == "payments_open"
