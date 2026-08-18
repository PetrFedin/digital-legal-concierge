from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_persistent_my_case_opens_shared_live_view_without_callback_trampoline():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})' in direct
    assert "await common._home_text(" in direct
    assert "view = await load_client_case_view(db, case)" in direct
    assert "markup = one(*my_case._case_buttons(view))" in direct
    assert "await message.answer(text, reply_markup=markup)" in direct


def test_direct_my_case_keeps_draft_guard_and_same_case_actions():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert "await common._guard_message_draft(message, state)" in direct
    assert "await state.clear()" in direct
    assert "ctx.case_service.get_active_case_for_user(user.id)" in direct
    assert 'text.replace("🏠 Главная", "📁 МОЁ ДЕЛО", 1)' in direct


def test_persistent_documents_reuses_canonical_document_decision_logic():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text == "📄 Документы")' in direct
    assert "await document_action_center._clear_document_upload_state(state)" in direct
    assert "DocumentService(db).list_case_documents(case.id)" in direct
    assert "document_action_center._active(all_documents)" in direct
    assert "document_action_center._counts(documents)" in direct
    assert "document_action_center._next_action(case, documents)" in direct
    assert "document_action_center._document_line(item)" in direct
    assert '("📋 Все актуальные документы", "documents_list_open")' in direct
    assert '("➕ Добавить документ", "documents_upload_open")' in direct
    assert 'f"🕘 История версий ({archived_count})"' in direct


def test_stale_documents_reply_button_never_creates_or_mutates_another_case():
    direct = read("app/bot/screens/reply_menu_direct.py")

    no_case = direct.split("if case is None:", 1)[1].split("all_documents =", 1)[0]
    assert "Старая кнопка нижнего меню" in no_case
    assert "не создаёт новое обращение" in no_case
    assert "не загружает файл в другой кейс" in no_case
    assert '"my_case_open"' in no_case
    assert '"calc_start"' in no_case


def test_persistent_message_history_opens_real_page_and_marks_only_visible_team_replies():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text == "💬 Переписка")' in direct
    assert "MessageService(db).list_case_messages(case.id, limit=100)" in direct
    assert "messages._format_dialog(" in direct
    assert "messages._history_slice(dialog, page)" in direct
    assert "messages._history_keyboard(" in direct
    assert 'if item.sender_type == "lawyer"' in direct
    assert "service.mark_lawyer_messages_read(" in direct
    assert "message_ids=visible_team_ids" in direct
    assert "read_only=read_only" in direct


def test_persistent_new_question_binds_draft_to_active_case_and_never_auto_reopens_archive():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text == "✉️ Новый вопрос")' in direct
    assert "latest_completed_case_for_user" in direct
    assert "Завершённое обращение не принимает новые сообщения" in direct
    assert '("🆕 Создать новое обращение", "message_new_request")' in direct
    assert "case_id=int(case.id)" in direct
    assert "case_number=str(case.case_number)" in direct
    assert "new_request_confirmed=False" in direct
    assert "messages.MessageStates.choosing_category" in direct
    assert "messages._category_prompt(data)" in direct
    assert "messages._category_buttons()" in direct


def test_persistent_legal_help_is_route_aware_and_presentation_only_until_explicit_m2_start():
    direct = read("app/bot/screens/reply_menu_direct.py")

    assert '@router.message(lambda m: m.text == "💬 Связаться с юристом")' in direct
    assert 'str(case.route or "") != RouteCode.M2.value' in direct
    assert "отдельная консультация не заменит и не скроет текущее дело" in direct
    assert "ConsultationService(db).get_current_for_case(case.id)" in direct
    assert "consultation.status == ConsultationStatus.BOOKED" in direct
    assert "consultation_description_ready(consultation)" in direct
    assert '("📅 Продолжить: выбрать время", "consult_booking_start")' in direct
    assert '("📝 Продолжить: описать вопрос", "consult_subject_start")' in direct
    assert "First legal-help entry is presentation-only" in direct
    assert "M2 case/consultation creation" in direct
    assert '("▶️ Начать: описать вопрос", "consult_subject_start")' in direct


def test_direct_reply_router_precedes_old_common_trampolines():
    bot = read("app/bot/bot.py")
    common = read("app/bot/screens/common.py")

    assert "reply_menu_direct.router" in bot
    assert bot.index("reply_menu_direct.router") < bot.index("common.router")
    # Keep the old handlers as compatibility/dead-code fallback until live
    # Telegram regression confirms the earlier exact handlers are effective.
    assert 'reply_markup=one(\n            ("📁 Моё дело", "my_case_open")' in common
    assert 'reply_markup=one(\n            ("📄 Открыть документы", "documents_open")' in common
    assert 'reply_markup=one(\n            ("💬 Открыть переписку", "message_history")' in common
    assert 'reply_markup=one(\n            ("✉️ Задать вопрос", "message_create")' in common
    assert 'reply_markup=one(\n            ("💬 Открыть связь с юристом", "contact_lawyer")' in common
