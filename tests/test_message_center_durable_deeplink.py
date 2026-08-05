from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_staff_reply_uses_client_outbox_event_and_safe_template():
    rules = read("app/domain/notifications/notification_rules.py")
    templates = read("app/domain/notifications/notification_templates.py")

    assert '"STAFF_MESSAGE_REPLIED"' in rules
    assert '"recipients": ["client"]' in rules
    assert '"template": "staff_message_reply"' in rules
    assert '"staff_message_reply"' in templates
    assert "Ответ юридической команды" in templates
    assert "Ответ сохранён в переписке по делу" in templates


def test_reply_and_outbox_commit_before_any_telegram_delivery():
    source = read("app/api/message_center.py")

    create_message = source.index("created = await service.create_lawyer_message(")
    emit = source.index("notifications = await NotificationEngine(db).emit(")
    durable_commit = source.index("await db.commit()", emit)
    delivery = source.index(
        "delivery = await _deliver_message_notifications(db, notification_ids)",
        durable_commit,
    )

    assert create_message < emit < durable_commit < delivery
    assert 'event_code="STAFF_MESSAGE_REPLIED"' in source
    assert 'dedupe_key=f"case:{case.id}:message:{created.id}:staff-reply"' in source
    assert "NotificationSender(db).send_selected(notification_ids)" in source
    assert "asyncio.wait_for" in source
    assert "telegram_timeout" in source
    assert "delivery_error" in source
    assert "Bot(" not in source
    assert "bot.send_message" not in source
    assert "TelegramNetworkError" not in source
    assert "TelegramAPIError" not in source


def test_reply_keeps_scope_snapshot_and_bounded_telegram_payload():
    source = read("app/api/message_center.py")

    assert "MAX_REPLY_LENGTH = 3800" in source
    assert 'maxlength="3800"' in source
    assert "expected_last_message_id" in source
    assert "latest_message_id != payload.expected_last_message_id" in source
    assert "case = await service.lock_case(case_id)" in source
    assert "_ensure_case_access(scope, case)" in source
    assert "Нельзя отправить ответ от имени другого юриста" in source
    assert "В диалоге появились новые сообщения" in source


def test_message_center_opens_exact_case_from_url_and_preserves_context():
    source = read("app/api/message_center.py")

    assert "new URLSearchParams(location.search).get('case_id')" in source
    assert "if(requestedCaseId>0)await openCase(requestedCaseId)" in source
    assert "url.searchParams.set('case_id',String(currentCaseId))" in source
    assert "history.replaceState(null,'',url)" in source
    assert "currentCaseId===x.case_id?'active':''" in source
    assert "setInterval(()=>{void loadMessages(null,false)},60000)" in source


def test_case_detail_opens_the_exact_conversation_instead_of_generic_inbox():
    source = read("app/admin/case_detail_page.py")

    assert 'href="/message-center/ui?case_id=${caseId}"' in source
    assert "Открыть переписку по делу" in source
    assert 'href="/message-center/ui">Открыть переписку' not in source
    assert "Статус, документы, клиент, сроки и действия в одном контексте" in source


def test_reply_ui_distinguishes_delivery_from_durable_storage():
    source = read("app/api/message_center.py")

    assert "function deliveryFeedback(delivery)" in source
    assert "Ответ отправлен и сохранён. Клиент получил его в Telegram." in source
    assert "Ответ сохранён и поставлен в очередь повторной Telegram-доставки." in source
    assert "Ответ сохранён, но Telegram отклонил доставку" in source
    assert "Ответ сохранён; уведомление уже обрабатывается другим процессом." in source
    assert "Но экран не обновился" in source
    assert "Ответ не отправлен и не сохранён" in source
    assert "Текст ответа сохранён в поле" in source
    assert "const result=await api('/message-center/cases/'+caseId+'/reply'" in source
    assert "deliveryFeedback(result.delivery)" in source


def test_message_center_ui_stays_protected_and_recovers_errors():
    source = read("app/api/message_center.py")

    assert "await require_staff_scope(request, db, x_admin_token)" in source
    assert 'RedirectResponse(url="/login", status_code=303)' in source
    assert "Дело не назначено текущему юристу" in source
    assert "Не удалось открыть диалог" in source
    assert "Повторить</button>" in source
    assert "replyText.disabled=true" in source
    assert "sendPending=true" in source
    assert "aria-busy" in source
    assert "BOT_TOKEN" not in source[source.index("MESSAGE_CENTER_HTML"):]
