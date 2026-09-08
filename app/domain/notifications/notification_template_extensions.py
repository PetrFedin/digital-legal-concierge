"""Current notification templates added after the legacy template catalogue.

Keeping the extension small avoids another string-patching compatibility layer;
NotificationEngine resolves these keys first, then the historical catalogue.
"""

TEMPLATE_EXTENSIONS = {
    "client_inactivity_reminder": (
        "Напоминание по обращению {case_number}.\n\n"
        "Вы остановились на незавершённом шаге: {next_action}.\n\n"
        "Все уже сохранённые данные остаются в деле. Откройте «Моё дело», чтобы продолжить с актуального шага."
    ),
}

__all__ = ["TEMPLATE_EXTENSIONS"]
