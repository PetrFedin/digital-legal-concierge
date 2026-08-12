from types import SimpleNamespace

from app.api.workdesk import _primary_action
from app.domain.documents.document_workflow import describe_document_attention


def _case(*, case_id=17, status="M1_LAWYER_REVIEW"):
    return SimpleNamespace(id=case_id, status=status)


def _reasons(*codes):
    return [{"code": code} for code in codes]


def _no_finance():
    return {"review_ids": [], "refund_ids": []}


def test_sla_action_beats_message_and_assignment_when_all_are_present():
    action = _primary_action(
        _case(),
        _reasons("overdue", "messages", "unassigned"),
        describe_document_attention(()),
        _no_finance(),
    )
    assert action["kind"] == "link"
    assert action["label"] == "Устранить просрочку"
    assert action["href"] == "/admin/workdesk/cases/17/action/sla"


def test_client_message_beats_assignment_when_sla_is_not_overdue():
    action = _primary_action(
        _case(),
        _reasons("messages", "unassigned"),
        describe_document_attention(()),
        _no_finance(),
    )
    assert action["kind"] == "link"
    assert action["label"] == "Прочитать сообщение клиента"
    assert action["href"] == "/message-center/ui?case_id=17"


def test_assignment_is_primary_when_no_higher_priority_reason_exists():
    action = _primary_action(
        _case(),
        _reasons("unassigned"),
        describe_document_attention(()),
        _no_finance(),
    )
    assert action["kind"] == "auto_assign"
    assert action["label"] == "Назначить юриста"
    assert action["endpoint"] == "/admin/cases/17/auto-assign"


def test_financial_review_remains_above_all_operational_actions():
    action = _primary_action(
        _case(),
        _reasons("payment_review", "overdue", "messages", "unassigned"),
        describe_document_attention(()),
        {"review_ids": [901], "refund_ids": []},
    )
    assert action["label"] == "Сверить полученный платёж"
    assert action["href"] == "/admin/payment-reviews/ui?payment_id=901&case_id=17"
