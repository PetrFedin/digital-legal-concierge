from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source() -> str:
    return (ROOT / "app/api/lawyer_workspace.py").read_text(encoding="utf-8")


def test_enforcement_is_a_named_primary_action_not_generic_status_edit():
    text = source()

    assert "elif status == CaseStatus.M1_ENFORCEMENT:" in text
    assert 'm1_action = "record_money_received"' in text
    assert "Зафиксировать фактически взысканную сумму" in text
    assert "`/lawyer/cases/${id}/enforcement/money-received`" in text
    assert 'router.post("/cases/{case_id}/status")' not in text


def test_recovery_form_uses_actual_amount_configured_percent_and_two_stage_review():
    text = source()

    assert '"success_fee_percent": success_fee_percent' in text
    assert 'inputmode="decimal"' in text
    assert "Используйте сумму фактического поступления" in text
    assert "Предварительно:" in text
    assert "Итоговую сумму повторно рассчитает сервер" in text
    assert "reviewCaseForm" in text
    assert "form.dataset.stage!=='review'" in text
    assert "expected_status:x.status" in text
    assert "expected_updated_at:x.updated_at" in text


def test_recovery_amount_and_comment_drafts_survive_conflict_until_success():
    text = source()

    assert "caseAmountDrafts=new Map()" in text
    assert "caseAmountDrafts.set(draftKey(id,type),amountRaw)" in text
    assert "Карточка дела изменилась или финансовые данные требуют проверки. Черновик сохранён." in text
    assert "caseAmountDrafts.delete(draftKey(id,type))" in text
    assert "caseDrafts.delete(draftKey(id,type))" in text


def test_waiting_success_fee_is_read_only_and_explains_automatic_close():
    text = source()
    action_list = "['start_claim','mark_claim_sent','open_court','open_court_payment','record_money_received'].includes(x.m1_action)"

    assert 'elif status == CaseStatus.M1_WAITING_SUCCESS_FEE:' in text
    assert 'm1_action = "wait_success_fee"' in text
    assert "После подтверждения оплаты дело закроется автоматически" in text
    assert "Webhook закроет дело только после подтверждения оплаты" in text
    assert action_list in text
    action_start = text.index(action_list)
    waiting_start = text.index("if(x.m1_action==='wait_success_fee')", action_start)
    assert "wait_success_fee" not in text[action_start:waiting_start]


def test_workspace_surfaces_missing_success_fee_payment_as_financial_inconsistency():
    text = source()

    assert "Дело ожидает success fee, но платёж не найден" in text
    assert "Не меняйте статус вручную" in text
    assert 'priority = "critical"' in text
