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
    """Install the presentation-only wording over the guided My Case screen."""

    from app.bot.screens import my_case

    my_case._document_detail = document_detail_for_client


__all__ = ["document_detail_for_client", "install_client_wording"]
