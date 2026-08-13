from __future__ import annotations


def document_detail_for_client(view) -> str:
    """Describe document lifecycle without claiming lawyer work started early.

    Document.ON_REVIEW means the package has been handed to the legal queue. The
    semantic boundary for actual lawyer review is the case status
    M1_LAWYER_REVIEW, which is set only by an authorized staff decision.
    """

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
    """Install presentation rules without changing document/domain state."""

    from app.bot.screens import document_action_center, my_case
    from app.domain.statuses.case_statuses import CaseStatus

    my_case._document_detail = document_detail_for_client

    # A submitted document enters ON_REVIEW as a queue/storage state before a
    # human decision begins substantive review. Keep the per-file label neutral.
    document_action_center._STATUS_LABELS["ON_REVIEW"] = (
        "передан юридической команде"
    )

    if getattr(
        document_action_center,
        "_client_handoff_wording_installed",
        False,
    ):
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
