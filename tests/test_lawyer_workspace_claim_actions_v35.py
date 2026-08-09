from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workspace_derives_claim_action_from_domain_deadline_not_case_updated_at():
    source = read("app/api/lawyer_workspace.py")

    assert "M1ClaimService" in source
    assert "court_eligibility(case=case, now=now)" in source
    assert 'm1_action = "start_claim"' in source
    assert 'm1_action = "mark_claim_sent"' in source
    assert 'm1_action = "open_court" if eligibility.eligible else "wait_claim_period"' in source
    assert "case.updated_at <=" not in source


def test_workspace_priority_keeps_client_work_ahead_of_claim_mutations():
    source = read("app/api/lawyer_workspace.py")

    unread = source.index("if unread_client_messages:")
    documents = source.index("if documents_on_review:")
    consultation = source.index("if consultation_today:")
    start_claim = source.index('if m1_action == "start_claim":')

    assert unread < documents < consultation < start_claim


def test_waiting_claim_period_has_no_court_mutation_button():
    source = read("app/api/lawyer_workspace.py")

    assert "До доступности судебного этапа" in source
    assert "Проверить срок" in source
    assert "['start_claim','mark_claim_sent','open_court'].includes(x.m1_action)" in source
    assert "wait_claim_period" not in source[
        source.index("['start_claim','mark_claim_sent','open_court'].includes(x.m1_action)") :
        source.index("if(x.m1_action==='wait_claim_period')")
    ]


def test_claim_actions_are_two_stage_snapshot_safe_and_preserve_draft_on_conflict():
    source = read("app/api/lawyer_workspace.py")

    assert "reviewCaseForm" in source
    assert "Проверить действие" in source
    assert "Подтвердить действие" in source
    assert "form.dataset.stage!=='review'" in source
    assert "expected_status:x.status" in source
    assert "expected_updated_at:x.updated_at" in source
    assert "caseDrafts.set(draftKey(id,type),comment)" in source
    assert "Карточка дела изменилась. Черновик сохранён." in source
    assert "caseDrafts.delete(draftKey(id,type))" in source
    assert "confirm(" not in source


def test_workspace_uses_named_claim_endpoints_and_transfer_payload_contract():
    source = read("app/api/lawyer_workspace.py")

    assert "`/lawyer/cases/${id}/claim/start`" in source
    assert "`/lawyer/cases/${id}/claim/sent`" in source
    assert "`/lawyer/cases/${id}/court/open`" in source
    assert "`/lawyer/cases/${id}/transfer-to-m2`" in source
    assert "reason:type==='transfer'?comment:undefined" in source


def test_workspace_keeps_local_case_scoped_navigation_and_recovery():
    source = read("app/api/lawyer_workspace.py")

    assert "x.startsWith('/')&&!x.startsWith('//')" in source
    assert 'href="/message-center/ui?case_id=${x.case_id}"' in source
    assert 'href="/document-access/review/ui?case_id=${x.case_id}"' in source
    assert "Очистить поиск" in source
    assert "Не удалось загрузить кабинет" in source
