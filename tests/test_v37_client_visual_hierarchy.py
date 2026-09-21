from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_case_is_the_canonical_client_context_and_main_step_surface():
    source = read("app/bot/screens/my_case.py")

    assert '"📁 МОЁ ДЕЛО"' in source
    assert 'f"№ {view.case_number}"' in source
    assert '"СЕЙЧАС"' in source
    assert '"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ"' in source
    assert '"ТРЕБУЕТСЯ ОТ ВАС"' in source
    assert '"СВОДКА"' in source
    assert '"📄 Документы"' in source
    assert '"💳 Оплаты"' in source
    assert '"🕘 История дела"' in source
    assert '"💬 Связаться с юристом"' in source
    assert "has_multiple_active_cases" in source
    assert '"📁 Выбрать другое обращение"' in source


def test_multi_case_selector_explains_that_secondary_sections_follow_selected_case():
    source = read("app/bot/screens/my_case.py")

    selector = source.split("def _case_selector", 1)[1].split(
        "async def _render_completed_case", 1
    )[0]
    assert "несколько активных обращений" in selector
    assert "документы, оплаты, переписка и дальнейшие действия" in selector
    assert 'f"my_case_select:v2:{int(case.id)}"' in selector


def test_message_draft_uses_context_now_main_step_and_review_before_send():
    source = read("app/bot/screens/messages.py")

    assert "_draft_case_context(data)" in source
    assert '"СЕЙЧАС\\n"' in source
    assert '"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\\n"' in source
    assert '"✅ ПРОВЕРКА ПЕРЕД ОТПРАВКОЙ\\n"' in source
    assert '"✅ Отправить вопрос"' in source
    assert '"✖️ Отменить черновик"' in source
    assert "Черновик сохранён и ещё не отправлен" in source


def test_m2_action_center_has_one_primary_action_before_secondary_controls():
    source = read("app/bot/screens/consultation_booking_ui.py")

    assert 'f"Обращение № {case_number}' in source
    assert '"СЕЙЧАС\\n"' in source
    assert '"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\\n"' in source
    assert "buttons: list[tuple[str, str]] = [primary]" in source
    assert "Первая кнопка ниже — самое актуальное безопасное действие" in source
    assert 'bound_case_callback("consult_reschedule", case_id)' in source
    assert 'bound_case_callback("consult_cancel", case_id)' in source
    assert "format_business_datetime" in source


def test_contract_and_poa_surfaces_show_exact_case_now_and_main_step():
    contract = read("app/bot/screens/service_contract.py")
    poa = read("app/bot/screens/poa_handoff.py")

    for source in (contract, poa):
        assert "Обращение №" in source
        assert "СЕЙЧАС" in source
        assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in source
    assert 'bound_case_callback("contract_open", case_id)' in contract
    assert 'bound_case_callback("message_create", case_id)' in contract
    assert 'bound_case_callback("poa_upload_document", int(case_id))' in poa
    assert 'bound_case_callback("message_create", int(case_id))' in poa


def test_completed_case_is_explicit_read_only_archive_with_new_request_separate():
    source = read("app/bot/screens/my_case.py")

    completed = source.split("async def _render_completed_case", 1)[1].split(
        "async def _render_case", 1
    )[0]
    assert "Действий по этому" in completed
    assert "АРХИВ" in completed
    assert "только для просмотра" in completed
    assert '"🧮 Новое обращение"' in completed
