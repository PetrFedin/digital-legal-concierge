from types import SimpleNamespace

from app.bot.client_case_view import client_action_for


def test_m2_consultation_route_has_direct_description_action():
    action = client_action_for(SimpleNamespace(status="M2_CONSULTATION_ROUTE"))

    assert action is not None
    assert action.label == "Описать вопрос"
    assert action.callback == "consult_description_start"
    assert "без возврата в главное меню" in action.description
