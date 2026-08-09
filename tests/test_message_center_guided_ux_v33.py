from pathlib import Path

from app.api.message_center import MESSAGE_CENTER_HTML


ROOT = Path(__file__).resolve().parents[1]


def _compact(value: str) -> str:
    return "".join(value.split())


def test_message_center_has_guided_action_hierarchy_and_search():
    for label in ("СЕЙЧАС", "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ", "КОНТЕКСТ"):
        assert label in MESSAGE_CENTER_HTML
    assert 'id="searchInput"' in MESSAGE_CENTER_HTML
    assert "Найти дело, клиента, текст…" in MESSAGE_CENTER_HTML
    assert 'value="attention">Требуют ответа' in MESSAGE_CENTER_HTML
    assert "f==='attention'&&x.waiting_for_reply" in MESSAGE_CENTER_HTML
    assert "latest_preview" in MESSAGE_CENTER_HTML


def test_waiting_on_client_is_a_real_state_without_fake_status_mutation():
    assert "Сейчас очередь за клиентом" in MESSAGE_CENTER_HTML
    assert "Ожидать ответ клиента" in MESSAGE_CENTER_HTML
    assert "Дополнительной мутации статуса не требуется" in MESSAGE_CENTER_HTML
    assert "Написать дополнительное сообщение" in MESSAGE_CENTER_HTML
    assert "generic status" not in MESSAGE_CENTER_HTML.lower()


def test_unassigned_conversation_routes_to_assignment_before_normal_reply():
    assert "Юрист не назначен" in MESSAGE_CENTER_HTML
    assert "Назначить ответственного" in MESSAGE_CENTER_HTML
    assert "После назначения вернитесь в этот диалог" in MESSAGE_CENTER_HTML
    assert "replyBox.classList.add('hidden')" in MESSAGE_CENTER_HTML
    assert 'href="/operator"' in MESSAGE_CENTER_HTML


def test_drafts_are_case_scoped_and_cleared_only_after_success():
    compact = _compact(MESSAGE_CENTER_HTML)
    assert "constdrafts=newMap()" in compact
    assert "if(currentCaseId)drafts.set(currentCaseId,replyText.value)" in compact
    assert "if(currentCaseId!==targetId)saveCurrentDraft()" in compact
    assert "restoreDraft(currentCaseId)" in compact
    assert "drafts.delete(caseId)" in compact
    assert "drafts.set(caseId,replyText.value)" in compact
    assert compact.index("awaitapi('/message-center/cases/'+caseId+'/reply'") < compact.index(
        "drafts.delete(caseId)"
    )


def test_open_dialog_flags_new_snapshot_before_operator_sends_again():
    assert "syncCurrentConversationState()" in MESSAGE_CENTER_HTML
    assert "row.latest_message_id" in MESSAGE_CENTER_HTML
    assert "currentLatestMessageId" in MESSAGE_CENTER_HTML
    assert "В диалоге появилось новое сообщение" in MESSAGE_CENTER_HTML
    assert "Обновить перед ответом" in MESSAGE_CENTER_HTML
    assert "expected_last_message_id:lastMessageId" in _compact(MESSAGE_CENTER_HTML)


def test_case_context_links_are_local_and_documents_are_case_scoped():
    assert "function localHref(value,fallback='/operator')" in MESSAGE_CENTER_HTML
    assert "value.startsWith('/')&&!value.startsWith('//')" in MESSAGE_CENTER_HTML
    assert "'/admin/workdesk/cases/'+caseId+'/action/documents'" in MESSAGE_CENTER_HTML
    assert "Документы дела" in MESSAGE_CENTER_HTML


def test_empty_and_error_states_have_explicit_recovery():
    assert "Подходящих диалогов нет" in MESSAGE_CENTER_HTML
    assert "Очистить поиск" in MESSAGE_CENTER_HTML
    assert "Показать все" in MESSAGE_CENTER_HTML
    assert "Не удалось загрузить диалоги" in MESSAGE_CENTER_HTML
    assert "Не удалось открыть диалог" in MESSAGE_CENTER_HTML
    assert "Повторить</button>" in MESSAGE_CENTER_HTML
    assert "Рабочий стол" in MESSAGE_CENTER_HTML


def test_reply_still_preserves_durable_delivery_feedback_and_single_flight():
    compact = _compact(MESSAGE_CENTER_HTML)
    assert "if(sendPending||!currentCaseId)return" in compact
    assert "sendPending=true" in compact
    assert "sendPending=false" in compact
    assert "replyText.disabled=true" in compact
    assert "replyText.disabled=false" in compact
    assert "deliveryFeedback(result.delivery)" in compact
    assert "Ответ отправлен и сохранён. Клиент получил его в Telegram." in MESSAGE_CENTER_HTML
    assert "Ответ сохранён и поставлен в очередь повторной Telegram-доставки." in MESSAGE_CENTER_HTML
