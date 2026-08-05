from __future__ import annotations

import re
from dataclasses import dataclass

CLIENT_MESSAGE_PATTERN = re.compile(
    r"^Тема:\s*(?P<category>[^\n]{1,160})\n"
    r"Срочность:\s*(?P<urgency>[^\n]{1,160})\n\n"
    r"(?P<body>.*)$",
    re.DOTALL,
)

PRIORITY_CRITICAL = "critical"
PRIORITY_TODAY = "today"
PRIORITY_NORMAL = "normal"

URGENCY_TO_PRIORITY = {
    "Критично: срок менее 24 часов": (
        PRIORITY_CRITICAL,
        "Клиент указал срок менее 24 часов",
        0,
    ),
    "Нужен ответ сегодня": (
        PRIORITY_TODAY,
        "Клиент просит ответ сегодня",
        1,
    ),
    "Обычный": (
        PRIORITY_NORMAL,
        "Обычная срочность",
        2,
    ),
}

CLIENT_URGENCY_NOTE = (
    "Срочность указана клиентом. Проверьте документы, фактический срок и SLA дела."
)


@dataclass(frozen=True)
class MessagePresentation:
    body: str
    category: str | None
    urgency: str | None
    priority: str
    priority_label: str | None
    priority_rank: int
    structured: bool


def present_message(text: str | None, *, sender_type: str) -> MessagePresentation:
    value = str(text or "").strip()
    if sender_type != "client":
        return MessagePresentation(
            body=value,
            category=None,
            urgency=None,
            priority=PRIORITY_NORMAL,
            priority_label=None,
            priority_rank=2,
            structured=False,
        )

    match = CLIENT_MESSAGE_PATTERN.fullmatch(value)
    if not match:
        return MessagePresentation(
            body=value,
            category=None,
            urgency=None,
            priority=PRIORITY_NORMAL,
            priority_label=None,
            priority_rank=2,
            structured=False,
        )

    category = match.group("category").strip() or None
    urgency = match.group("urgency").strip() or None
    body = match.group("body").strip()
    priority, label, rank = URGENCY_TO_PRIORITY.get(
        urgency or "",
        (PRIORITY_NORMAL, urgency or "Обычная срочность", 2),
    )
    return MessagePresentation(
        body=body,
        category=category,
        urgency=urgency,
        priority=priority,
        priority_label=label,
        priority_rank=rank,
        structured=True,
    )


def queue_bucket(
    *,
    waiting_for_reply: bool,
    overdue: bool,
    priority: str,
) -> int:
    """Return an objective inbox bucket; lower values are shown first.

    An actual four-hour response breach outranks self-reported urgency. Client
    urgency then helps order the remaining waiting conversations, while already
    answered threads stay last.
    """

    if not waiting_for_reply:
        return 4
    if overdue:
        return 0
    if priority == PRIORITY_CRITICAL:
        return 1
    if priority == PRIORITY_TODAY:
        return 2
    return 3
