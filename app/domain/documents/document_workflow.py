from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Iterable

from app.domain.statuses.document_statuses import DocumentStatus


class DocumentAttentionState(StrEnum):
    REVIEW = "REVIEW"
    CLIENT_DRAFT = "CLIENT_DRAFT"
    LEGACY_ATTENTION = "LEGACY_ATTENTION"
    NONE = "NONE"


ACTIONABLE_REVIEW_STATUSES = frozenset({DocumentStatus.ON_REVIEW.value})
CLIENT_DRAFT_STATUSES = frozenset({DocumentStatus.UPLOADED.value})
LEGACY_ATTENTION_STATUSES = frozenset(
    {
        "PENDING",
        "PENDING_REVIEW",
        "REVIEW_PENDING",
        "REVIEW_REQUIRED",
        "NEEDS_REVIEW",
    }
)


@dataclass(frozen=True, slots=True)
class DocumentWorkflowDescriptor:
    state: DocumentAttentionState
    code: str
    label: str
    detail: str
    actionable: bool
    primary_label: str
    primary_href_kind: str

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


def normalize_document_status(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value or "").strip().upper()


def classify_document_attention(
    statuses: Iterable[object],
) -> DocumentAttentionState:
    normalized = {normalize_document_status(status) for status in statuses}
    if normalized & ACTIONABLE_REVIEW_STATUSES:
        return DocumentAttentionState.REVIEW
    if normalized & CLIENT_DRAFT_STATUSES:
        return DocumentAttentionState.CLIENT_DRAFT
    if normalized & LEGACY_ATTENTION_STATUSES:
        return DocumentAttentionState.LEGACY_ATTENTION
    return DocumentAttentionState.NONE


def describe_document_attention(
    statuses: Iterable[object],
) -> DocumentWorkflowDescriptor:
    state = classify_document_attention(statuses)
    if state == DocumentAttentionState.REVIEW:
        return DocumentWorkflowDescriptor(
            state=state,
            code="documents_review",
            label="Документы ждут решения юриста",
            detail=(
                "Клиент завершил передачу пакета. Проверьте каждый файл и "
                "зафиксируйте решение."
            ),
            actionable=True,
            primary_label="Проверить документы",
            primary_href_kind="review",
        )
    if state == DocumentAttentionState.CLIENT_DRAFT:
        return DocumentWorkflowDescriptor(
            state=state,
            code="documents_client_draft",
            label="Клиент загрузил файлы, но не передал пакет",
            detail=(
                "Файлы остаются черновиками клиента. Решения юриста пока "
                "недоступны; при необходимости уточните передачу в диалоге."
            ),
            actionable=False,
            primary_label="Уточнить передачу",
            primary_href_kind="message",
        )
    if state == DocumentAttentionState.LEGACY_ATTENTION:
        return DocumentWorkflowDescriptor(
            state=state,
            code="documents_legacy_attention",
            label="Статус документов требует уточнения",
            detail=(
                "Обнаружен исторический статус, который нельзя безопасно "
                "обработать кнопками проверки. Уточните состояние в диалоге."
            ),
            actionable=False,
            primary_label="Уточнить статус",
            primary_href_kind="message",
        )
    return DocumentWorkflowDescriptor(
        state=state,
        code="documents_none",
        label="Нет документов, ожидающих решения",
        detail=(
            "Новых пакетов на проверке нет. Вернитесь к рабочему столу или "
            "откройте диалог по делу."
        ),
        actionable=False,
        primary_label="Открыть диалог",
        primary_href_kind="message",
    )
