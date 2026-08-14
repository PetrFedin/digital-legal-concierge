from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


NEW_CASE_REPLY_MENU_BUTTONS = [
    [KeyboardButton(text="🧮 Рассчитать неустойку")],
    [KeyboardButton(text="💬 Связаться с юристом")],
    [KeyboardButton(text="🏠 Главная")],
]

ACTIVE_CASE_REPLY_MENU_BUTTONS = [
    [KeyboardButton(text="📁 Моё дело"), KeyboardButton(text="📄 Документы")],
    [KeyboardButton(text="💬 Переписка"), KeyboardButton(text="✉️ Новый вопрос")],
    [KeyboardButton(text="🏠 Главная")],
]

COMPLETED_CASE_REPLY_MENU_BUTTONS = [
    [KeyboardButton(text="📁 Моё дело"), KeyboardButton(text="🧮 Рассчитать неустойку")],
    [KeyboardButton(text="🏠 Главная")],
]

# Compatibility alias for integrations importing the historical constant.
MAIN_MENU_BUTTONS = NEW_CASE_REPLY_MENU_BUTTONS


def reply_main_menu(
    case_exists: bool = False,
    *,
    completed_case: bool = False,
) -> ReplyKeyboardMarkup:
    if case_exists:
        keyboard = ACTIVE_CASE_REPLY_MENU_BUTTONS
        placeholder = "Дело · документы · переписка"
    elif completed_case:
        keyboard = COMPLETED_CASE_REPLY_MENU_BUTTONS
        placeholder = "Архив дела · новый расчёт"
    else:
        keyboard = NEW_CASE_REPLY_MENU_BUTTONS
        placeholder = "Выберите: расчёт или помощь юриста"
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=placeholder,
    )


def main_menu(
    case_exists: bool = False,
    *,
    completed_case: bool = False,
    payments_enabled: bool | None = None,
    primary_action: tuple[str, str] | None = None,
):
    kb = InlineKeyboardBuilder()
    primary_callback = primary_action[1] if primary_action else None

    def secondary(text: str, callback_data: str) -> None:
        # One screen = one primary action. If the primary button already opens
        # a section, do not render the same callback again under another label.
        if callback_data != primary_callback:
            kb.button(text=text, callback_data=callback_data)

    if completed_case and not case_exists:
        if primary_action:
            text, callback_data = primary_action
            kb.button(text=text, callback_data=callback_data)
        secondary("📁 Моё дело", "my_case_open")
        secondary("💳 Оплаты", "payments_open")
        secondary("🧮 Новое обращение", "calc_start")
        count = 3 - int(primary_callback in {"my_case_open", "payments_open", "calc_start"})
        kb.adjust(*([1] * (count + int(bool(primary_action)))))
        return kb.as_markup()

    if not case_exists:
        if primary_action:
            text, callback_data = primary_action
            kb.button(text=text, callback_data=callback_data)
        secondary("🧮 Рассчитать неустойку", "calc_start")
        secondary("💬 Связаться с юристом", "contact_lawyer")
        count = 2 - int(primary_callback in {"calc_start", "contact_lawyer"})
        kb.adjust(*([1] * (count + int(bool(primary_action)))))
        return kb.as_markup()

    if primary_action:
        text, callback_data = primary_action
        kb.button(text=text, callback_data=callback_data)

    # Secondary navigation is intentionally compact. Starting a second M2 flow
    # over an active M1 is forbidden by the domain layer, so the old generic
    # "Юрист / консультация" shortcut was both redundant and misleading.
    secondary("📁 Моё дело", "my_case_open")
    secondary("📄 Документы", "documents_open")
    secondary("💬 Переписка", "message_history")

    show_payments = True if payments_enabled is None else bool(payments_enabled)
    if show_payments:
        secondary("💳 Оплаты", "payments_open")

    secondary("✉️ Новый вопрос", "message_create")

    # Re-layout after de-duplication. Primary always owns its own row; secondary
    # actions are grouped by two where possible for a compact Telegram panel.
    secondary_count = 4 + int(show_payments)
    if primary_callback in {
        "my_case_open",
        "documents_open",
        "message_history",
        "payments_open" if show_payments else "",
        "message_create",
    }:
        secondary_count -= 1
    row_sizes: list[int] = [1] if primary_action else []
    while secondary_count > 0:
        row = min(2, secondary_count)
        row_sizes.append(row)
        secondary_count -= row
    kb.adjust(*row_sizes)
    return kb.as_markup()


def home_kb():
    return main_menu(False)


def one(*items):
    kb = InlineKeyboardBuilder()
    for text, cb in items:
        kb.button(text=text, callback_data=cb)
    kb.adjust(1)
    return kb.as_markup()
