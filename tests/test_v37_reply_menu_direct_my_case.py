from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_persistent_my_case_opens_shared_live_view_or_multi_case_selector():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})' in direct
    assert "active_cases = await ctx.case_service.get_active_cases_for_user" in direct
    assert "if len(active_cases) > 1:" in direct
    assert "text, buttons = await _matter_selector_text(user, db)" in direct
    assert "await common._home_text(" in direct
    assert "view = await load_client_case_view(db, case)" in direct
    assert "markup = one(*my_case._case_buttons(view))" in direct
    assert "await message.answer(text, reply_markup=markup)" in direct


def test_client_wording_wrapper_preserves_multi_case_button_signature():
    patch = read("app/bot/client_wording_patch.py")
    my_case = read("app/bot/screens/my_case.py")

    # The live wording installer replaces `_case_buttons`. `my_case._render_case`
    # passes presentation context as a keyword when several active matters exist,
    # so the wrapper must transparently forward the canonical helper signature.
    assert "def case_buttons_with_back(view, **kwargs):" in patch
    assert "original_case_buttons(view, **kwargs)" in patch
    assert "has_multiple_active_cases=has_multiple_active_cases" in my_case


def test_reply_menu_context_never_guesses_between_multiple_active_cases():
    direct = read("app/bot/screens/reply_menu_direct.py")

    context = direct.split("async def _message_context", 1)[1].split(
        "async def _active_message_case", 1
    )[0]
    assert "get_selected_case_for_user(" in context
    assert "include_terminal=False" in context
    assert "get_active_cases_for_user" in context
    assert "active_cases[0] if len(active_cases) == 1 else None" in context
    assert "get_active_case_for_user" not in context

    selector = direct.split("async def _show_selector_if_ambiguous", 1)[1].split(
        '@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")', 1
    )[0]
    assert "if case is not None:" in selector
    assert "if len(active_cases) <= 1:" in selector
    assert "_matter_selector_text(user, db)" in selector


def test_persistent_documents_reuses_canonical_logic_and_fails_closed_on_ambiguity():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text == "📄 Документы")' in direct
    assert "await document_action_center._clear_document_upload_state(state)" in direct
    assert "ctx, user, case = await _message_context(message, db)" in direct
    assert "await _show_selector_if_ambiguous(" in direct
    assert "DocumentService(db).list_case_documents(case.id)" in direct
    assert "document_action_center._active(all_documents)" in direct
    assert "document_action_center._counts(documents)" in direct
    assert "document_action_center._next_action(case, documents)" in direct
    assert "document_action_center._document_line(item)" in direct
    assert '("📋 Все актуальные документы", "documents_list_open")' in direct
    assert '("➕ Добавить документ", "documents_upload_open")' in direct
    assert 'f"🕘 История версий ({archived_count})"' in direct
    assert "Файл не будет автоматически привязан к другому делу" in direct


def test_persistent_message_history_requires_case_choice_before_marking_read():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text == "💬 Переписка")' in direct
    history = direct.split('@router.message(lambda m: m.text == "💬 Переписка")', 1)[1].split(
        '@router.message(lambda m: m.text == "✉️ Новый вопрос")', 1
    )[0]
    assert "ctx, user, case = await _message_context(message, db)" in history
    assert "await _show_selector_if_ambiguous(" in history
    assert "latest_completed_case_for_user" in history
    assert "MessageService(db).list_case_messages(case.id, limit=100)" in history
    assert "messages._format_dialog(" in history
    assert "messages._history_slice(dialog, page)" in history
    assert "messages._history_keyboard(" in history
    assert 'if item.sender_type == "lawyer"' in history
    assert "service.mark_lawyer_messages_read(" in history
    assert "message_ids=visible_team_ids" in history
    assert "read_only=read_only" in history


def test_persistent_new_question_binds_only_explicit_or_unambiguous_active_case():
    direct = read("app/bot/screens/reply_menu_direct.py")

    question = direct.split('@router.message(lambda m: m.text == "✉️ Новый вопрос")', 1)[1].split(
        '@router.message(lambda m: m.text == "💬 Связаться с юристом")', 1
    )[0]
    assert "ctx, user, case = await _message_context(message, db)" in question
    assert "await _show_selector_if_ambiguous(" in question
    assert "latest_completed_case_for_user" in question
    assert "Завершённое обращение не принимает новые сообщения" in question
    assert '("🆕 Создать новое обращение", "message_new_request")' in question
    assert "case_id=int(case.id)" in question
    assert "case_number=str(case.case_number)" in question
    assert "new_request_confirmed=False" in question
    assert "messages.MessageStates.choosing_category" in question


def test_persistent_legal_help_is_route_aware_and_requires_unambiguous_context():
    direct = read("app/bot/screens/reply_menu_direct.py")

    legal = direct.split('@router.message(lambda m: m.text == "💬 Связаться с юристом")', 1)[1]
    assert "ctx, user, case = await _message_context(message, db)" in legal
    assert "await _show_selector_if_ambiguous(" in legal
    assert 'str(case.route or "") != RouteCode.M2.value' in legal
    assert "Консультационный маршрут не подменяет и не меняет M1" in legal
    assert "ConsultationService(db).get_current_for_case(case.id)" in legal
    assert "consultation.status == ConsultationStatus.BOOKED" in legal
    assert "consultation_description_ready(consultation)" in legal
    assert '("📅 Продолжить: выбрать время", "consult_booking_start")' in legal
    assert '("📝 Продолжить: описать вопрос", "consult_subject_start")' in legal
    assert "First legal-help entry is presentation-only" in legal
    assert "source-operation" in legal
    assert '("▶️ Начать: описать вопрос", "consult_subject_start")' in legal


def test_direct_reply_router_precedes_old_common_trampolines():
    bot = read("app/bot/bot.py")
    common = read("app/bot/screens/common.py")

    assert "reply_menu_direct.router" in bot
    assert bot.index("reply_menu_direct.router") < bot.index("common.router")
    # Historical handlers remain compatibility/dead-code fallbacks until live
    # Telegram regression proves the direct reply-menu owners on real clients.
    assert 'reply_markup=one(\n            ("📁 Моё дело", "my_case_open")' in common
    assert 'reply_markup=one(\n            ("📄 Открыть документы", "documents_open")' in common
    assert 'reply_markup=one(\n            ("💬 Открыть переписку", "message_history")' in common
    assert 'reply_markup=one(\n            ("✉️ Задать вопрос", "message_create")' in common
    assert 'reply_markup=one(\n            ("💬 Открыть связь с юристом", "contact_lawyer")' in common
