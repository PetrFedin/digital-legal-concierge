from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# Persistent Telegram navigation follows the approved visibility contract:
# Home / Calculator / Contact are available on first entry; Case/Documents only
# appear after an active Case exists. A completed Case keeps its read-only
# archive entry while Contact Lawyer remains a global M2/help entry. Destination handlers
# remain fail-closed, so old Telegram keyboards/messages are still safe.
NEW_CASE_REPLY_MENU_BUTTONS = [
    [KeyboardButton(text="🏠 Главная"), KeyboardButton(text="🧮 Рассчитать неустойку")],
    [KeyboardButton(text="💬 Связаться с юристом")],
]

ACTIVE_CASE_REPLY_MENU_BUTTONS = [
    [KeyboardButton(text="🏠 Главная"), KeyboardButton(text="🧮 Рассчитать неустойку")],
    [KeyboardButton(text="📁 Моё дело"), KeyboardButton(text="📄 Документы")],
    [KeyboardButton(text="💬 Связаться с юристом")],
]

COMPLETED_CASE_REPLY_MENU_BUTTONS = [
    [KeyboardButton(text="🏠 Главная"), KeyboardButton(text="🧮 Рассчитать неустойку")],
    [KeyboardButton(text="📁 Моё дело")],
    [KeyboardButton(text="💬 Связаться с юристом")],
]

# Compatibility aliases remain deterministic but no longer imply that every
# navigation item is visible in every client state.
CANONICAL_REPLY_MENU_BUTTONS = ACTIVE_CASE_REPLY_MENU_BUTTONS
MAIN_MENU_BUTTONS = ACTIVE_CASE_REPLY_MENU_BUTTONS


def reply_main_menu(
    case_exists: bool = False,
    *,
    completed_case: bool = False,
) -> ReplyKeyboardMarkup:
    if case_exists:
        keyboard = ACTIVE_CASE_REPLY_MENU_BUTTONS
        placeholder = "Дело · документы · юрист · новый расчёт"
    elif completed_case:
        keyboard = COMPLETED_CASE_REPLY_MENU_BUTTONS
        placeholder = "Архив обращения или помощь юриста"
    else:
        keyboard = NEW_CASE_REPLY_MENU_BUTTONS
        placeholder = "Расчёт или помощь юриста"
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

    # Inline actions are contextual; the persistent reply keyboard above follows
    # current client state. These shortcuts focus on the selected Case.
    secondary("📁 Моё дело", "my_case_open")
    secondary("📄 Документы", "documents_open")
    secondary("💬 Связаться с юристом", "contact_lawyer")

    show_payments = True if payments_enabled is None else bool(payments_enabled)
    if show_payments:
        secondary("💳 Оплаты", "payments_open")

    secondary("🧮 Новый расчёт", "calc_start")

    secondary_count = 4 + int(show_payments)
    if primary_callback in {
        "my_case_open",
        "documents_open",
        "contact_lawyer",
        "payments_open" if show_payments else "",
        "calc_start",
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
