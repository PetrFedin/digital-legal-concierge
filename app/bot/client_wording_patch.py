from __future__ import annotations


def document_detail_for_client(view) -> str:
    """Describe document lifecycle without claiming lawyer work started early."""

    documents = view.documents
    status = str(getattr(view, "case_status", "") or "")
    if status == "M1_DOCUMENTS_RECEIVED" and documents.review_count:
        text = (
            f"{documents.current_count} актуальных · "
            f"{documents.review_count} передано юридической команде, "
            "ждём начала проверки"
        )
    else:
        text = documents.summary
    if documents.archived_count:
        text += f" · в истории {documents.archived_count}"
    return text


def install_client_wording() -> None:
    """Install client presentation and fail-closed runtime compatibility rules."""

    from app.bot.case_callback_scope import bound_case_callback
    from app.bot.client_case_view import CLIENT_ACTIONS, ClientAction
    from app.bot.keyboards import one
    from app.bot.screens import (
        calculator,
        consultation_intake as consultation_intake_screen,
        document_action_center,
        documents,
        my_case,
        payments,
        service_contract,
    )
    from app.config import settings
    from app.domain.payments.mode import payments_disabled
    from app.domain.statuses.case_statuses import CaseStatus

    my_case._document_detail = document_detail_for_client

    # `Моё дело` is the main client cabinet, not a terminal dialog. Keep the
    # primary action first, but expose the approved global Back affordance just
    # before Home. The navigation guard resolves the previous read-only screen
    # and never replays business mutations. Preserve the full canonical helper
    # signature so multi-case rendering can pass presentation context without a
    # runtime TypeError after this compatibility layer is installed.
    if not getattr(my_case, "_logical_back_button_installed", False):
        original_case_buttons = my_case._case_buttons

        def case_buttons_with_back(view, **kwargs):
            items = list(original_case_buttons(view, **kwargs))
            callbacks = [str(item[1]) for item in items]
            if "nav_back" not in callbacks:
                back_button = ("⬅️ Назад", "nav_back")
                try:
                    home_index = callbacks.index("nav_home")
                except ValueError:
                    items.append(back_button)
                else:
                    items.insert(home_index, back_button)
            return items

        my_case._case_buttons = case_buttons_with_back
        my_case._logical_back_button_installed = True

    # Calculator result historically exposed three unbound mutating callbacks.
    # They do not contain case_id, so an old Telegram message could otherwise be
    # clicked after a new case was created. The current result has one navigation
    # action; the decision screen then emits version-bound v2 callbacks.
    def safe_calculator_result_keyboard():
        return one(
            ("🧭 Выбрать дальнейший путь", "calc_decision_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )

    calculator.result_kb = safe_calculator_result_keyboard

    # Historical payments.py still contains a compatibility helper that treated
    # demo_mode as permission to expose a DEV payment button. Production runtime
    # must never derive financial authorization from a demo/presentation flag.
    # Both payment_keyboard() and pay_fake_success call this module function at
    # runtime, so replacing it here removes the button and blocks the callback.
    def local_test_fake_payments_only() -> bool:
        return bool(
            str(settings.payment_provider or "").strip().lower() == "fake"
            and str(settings.app_env or "").strip().lower() in {"local", "test"}
        )

    payments.fake_payments_enabled = local_test_fake_payments_only

    CLIENT_ACTIONS["M1_WAITING_PAYMENT_30000"] = ClientAction(
        "Оплатить 30 000 ₽",
        "pay_start_30000",
        "Первый платёж открывает этап доверенности только после подтверждения оплаты.",
    )
    CLIENT_ACTIONS["M1_WAITING_PAYMENT_70000"] = ClientAction(
        "Оплатить 70 000 ₽",
        "pay_court_70000",
        "Второй платёж доступен после зафиксированного судебного акта и открывает этап исполнения только после подтверждения оплаты.",
    )
    # M1_MONEY_RECEIVED is an internal transition inside the lawyer-owned
    # enforcement transaction. The client must never manufacture/open the fee
    # from this intermediate state. If it persists, Workdesk integrity flags it.
    CLIENT_ACTIONS["M1_MONEY_RECEIVED"] = ClientAction(
        "Проверить финальный расчёт",
        "my_case_open",
        "Фактически взысканная сумма зафиксирована. Команда завершает расчёт финального процента; оплачивать его нужно только после появления отдельного платежа.",
    )
    CLIENT_ACTIONS["M1_WAITING_SUCCESS_FEE"] = ClientAction(
        "Оплатить финальный процент",
        "pay_success_fee",
        "Оплатите рассчитанный процент от фактически взысканной суммы. После подтверждения финансовый этап завершается.",
    )
    if payments_disabled():
        CLIENT_ACTIONS["M2_PAYMENT_PENDING"] = ClientAction(
            "Подтвердить выбранное время",
            "consult_pay",
            "Онлайн-оплата для этого маршрута отключена. Подтвердите только актуальный удерживаемый слот; если резерв истёк, бот вернёт к выбору времени.",
        )
    else:
        CLIENT_ACTIONS["M2_PAYMENT_PENDING"] = ClientAction(
            "Открыть оплату консультации",
            "payments_open",
            "Откройте платёж, привязанный к текущему резерву. Старая ссылка другого слота не будет показана.",
        )

    # New live slot-selection keyboards should not emit the historical unbound
    # online consult_pay callback. Keep the callback only in no-payment mode,
    # where it is a guarded confirmation of the exact current held slot.
    if not getattr(consultation_intake_screen, "_route_aware_payment_cta_installed", False):
        original_consultation_one = consultation_intake_screen.one

        def route_aware_consultation_keyboard(*buttons):
            routed = []
            for label, callback in buttons:
                if callback == "consult_pay" and not payments_disabled():
                    callback = "payments_open"
                    if str(label).strip().startswith("💳"):
                        label = "💳 Открыть актуальную оплату"
                routed.append((label, callback))
            return original_consultation_one(*routed)

        consultation_intake_screen.one = route_aware_consultation_keyboard
        consultation_intake_screen._route_aware_payment_cta_installed = True

    # The legacy document screen is still a live entry point. Keep its primary
    # M2 confirmation action aligned with My Case/action-center so new screens do
    # not emit raw online consult_pay callbacks. Historical messages remain
    # backward-compatible through payment_archive_guard.
    if not getattr(documents, "_route_aware_payment_cta_installed", False):
        original_after_documents_buttons = documents._after_documents_buttons

        def route_aware_after_documents_buttons(case):
            buttons = list(original_after_documents_buttons(case))
            if documents._case_status(case) != CaseStatus.M2_PAYMENT_PENDING:
                return tuple(buttons)
            primary = (
                (
                    "Подтвердить выбранное время",
                    bound_case_callback("consult_pay", int(case.id)),
                )
                if payments_disabled()
                else ("💳 Открыть актуальную оплату", "payments_open")
            )
            result = [primary]
            for label, callback in buttons:
                if callback == "consult_pay":
                    continue
                result.append((label, callback))
            return tuple(result)

        documents._after_documents_buttons = route_aware_after_documents_buttons
        documents._route_aware_payment_cta_installed = True

    # Contract screens are one of the places where the approved UX explicitly
    # requires a Back action. Add it presentation-side without touching any
    # contract/payment transition. The navigation guard replays only read-only
    # screen callbacks, so Back can never reconfirm a version or recreate money.
    if not getattr(service_contract, "_logical_back_button_installed", False):
        original_contract_show = service_contract._show

        async def contract_show_with_back(callback, text, *, buttons):
            items = list(buttons)
            callbacks = [str(item[1]) for item in items]
            if "nav_back" not in callbacks:
                back_button = ("⬅️ Назад", "nav_back")
                try:
                    home_index = callbacks.index("nav_home")
                except ValueError:
                    items.append(back_button)
                else:
                    items.insert(home_index, back_button)
            return await original_contract_show(
                callback,
                text,
                buttons=tuple(items),
            )

        service_contract._show = contract_show_with_back
        service_contract._logical_back_button_installed = True

    document_action_center._STATUS_LABELS["ON_REVIEW"] = "передан юридической команде"

    if getattr(document_action_center, "_client_handoff_wording_installed", False):
        return

    original_next_action = document_action_center._next_action

    def bind_document_mutations(case, buttons):
        case_id = int(case.id)
        result = []
        for label, callback in buttons:
            if callback == "doc_finish_upload":
                callback = f"doc_finish_upload:v2:{case_id}"
            elif callback == "doc_skip_m2":
                callback = f"doc_skip_m2:v2:{case_id}"
            result.append((label, callback))
        return result

    def next_action_with_real_review_boundary(case, documents_list):
        counts = document_action_center._counts(documents_list)
        status = document_action_center._case_status(case)
        route = str(getattr(case, "route", "") or "").upper()
        review_only = bool(
            counts["review"]
            and not counts["required"]
            and not counts["new"]
            and not counts["replacement"]
        )
        if status == CaseStatus.M1_DOCUMENTS_RECEIVED and review_only:
            return (
                "Документы переданы юридической команде. Сейчас ждём назначения "
                "ответственного и фактического начала проверки; повторно "
                "отправлять эти файлы не нужно.",
                [("🔄 Проверить статус", "documents_open")],
            )
        if route == "M2" and review_only:
            if status == CaseStatus.M2_SLOT_PENDING:
                return (
                    "Документы переданы юридической команде и не блокируют запись. "
                    "Следующий обязательный шаг — выбрать свободное время консультации.",
                    [
                        (
                            "📅 Выбрать дату и время",
                            bound_case_callback("consult_booking_start", int(case.id)),
                        )
                    ],
                )
            return (
                "Документы переданы юридической команде. Их проверка идёт параллельно; "
                "основной обязательный шаг консультации показан в «Моё дело».",
                [("📁 К текущему шагу обращения", "my_case_open")],
            )
        text, buttons = original_next_action(case, documents_list)
        return text, bind_document_mutations(case, buttons)

    document_action_center._next_action = next_action_with_real_review_boundary
    document_action_center._client_handoff_wording_installed = True


__all__ = ["document_detail_for_client", "install_client_wording"]
