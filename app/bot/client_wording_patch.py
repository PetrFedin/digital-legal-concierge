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

    from app.bot.client_case_view import CLIENT_ACTIONS, ClientAction
    from app.bot.screens import document_action_center, my_case, payments
    from app.config import settings
    from app.domain.statuses.case_statuses import CaseStatus

    my_case._document_detail = document_detail_for_client

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
    CLIENT_ACTIONS["M2_PAYMENT_PENDING"] = ClientAction(
        "Перейти к оплате консультации",
        "consult_pay",
        "Оплатите консультацию для выбранного времени. Если резерв времени уже истёк, система вернёт вас к выбору актуального слота.",
    )

    document_action_center._STATUS_LABELS["ON_REVIEW"] = "передан юридической команде"

    if getattr(document_action_center, "_client_handoff_wording_installed", False):
        return

    original_next_action = document_action_center._next_action

    def next_action_with_real_review_boundary(case, documents):
        counts = document_action_center._counts(documents)
        status = document_action_center._case_status(case)
        if (
            status == CaseStatus.M1_DOCUMENTS_RECEIVED
            and counts["review"]
            and not counts["required"]
            and not counts["new"]
            and not counts["replacement"]
        ):
            return (
                "Документы переданы юридической команде. Сейчас ждём назначения "
                "ответственного и фактического начала проверки; повторно "
                "отправлять эти файлы не нужно.",
                [("🔄 Проверить статус", "documents_open")],
            )
        return original_next_action(case, documents)

    document_action_center._next_action = next_action_with_real_review_boundary
    document_action_center._client_handoff_wording_installed = True


__all__ = ["document_detail_for_client", "install_client_wording"]
