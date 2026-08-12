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

    if completed_case and not case_exists:
        if primary_action:
            text, callback_data = primary_action
            kb.button(text=text, callback_data=callback_data)
        if not primary_action or primary_action[1] != "my_case_open":
            kb.button(text="📁 Моё дело", callback_data="my_case_open")
        kb.button(text="💳 Оплаты", callback_data="payments_open")
        kb.button(text="🧮 Новое обращение", callback_data="calc_start")
        kb.adjust(*([1, 1, 1, 1] if primary_action and primary_action[1] != "my_case_open" else [1, 1, 1]))
        return kb.as_markup()

    if not case_exists:
        if primary_action:
            text, callback_data = primary_action
            kb.button(text=text, callback_data=callback_data)
        kb.button(text="🧮 Рассчитать неустойку", callback_data="calc_start")
        kb.button(text="💬 Связаться с юристом", callback_data="contact_lawyer")
        kb.adjust(*([1, 1, 1] if primary_action else [1, 1]))
        return kb.as_markup()

    if primary_action:
        text, callback_data = primary_action
        kb.button(text=text, callback_data=callback_data)

    # Keep the active-case menu visually grouped by user intent:
    # case context -> communication/finance -> new legal request/help.
    # This mirrors the persistent reply menu while keeping payment history
    # reachable even when online payment creation is disabled.
    kb.button(text="📁 Моё дело", callback_data="my_case_open")
    kb.button(text="📄 Документы", callback_data="documents_open")
    kb.button(text="💬 Переписка", callback_data="message_history")

    # Payment provider mode controls whether a new online payment link can be
    # created; it must never hide persisted payment status/history from a client.
    # Explicit callers may still suppress the shortcut for a specialized screen.
    show_payments = True if payments_enabled is None else bool(payments_enabled)
    if show_payments:
        kb.button(text="💳 Оплаты", callback_data="payments_open")

    kb.button(text="✉️ Новый вопрос", callback_data="message_create")
    kb.button(text="⚖️ Юрист / консультация", callback_data="contact_lawyer")

    row_sizes: list[int] = []
    if primary_action:
        row_sizes.append(1)
    row_sizes.append(2)
    row_sizes.append(2 if show_payments else 1)
    row_sizes.append(2)
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
