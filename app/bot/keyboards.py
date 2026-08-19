from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# Canonical persistent information architecture. Availability is explained by
# the destination screen instead of hiding stable navigation items as a Case
# moves through M1/M2. This keeps Telegram muscle memory predictable.
CANONICAL_REPLY_MENU_BUTTONS = [
    [KeyboardButton(text="🏠 Главная"), KeyboardButton(text="🧮 Рассчитать неустойку")],
    [KeyboardButton(text="📁 Моё дело"), KeyboardButton(text="📄 Документы")],
    [KeyboardButton(text="💬 Связаться с юристом")],
]

# Compatibility aliases for code/tests that still import historical names.
NEW_CASE_REPLY_MENU_BUTTONS = CANONICAL_REPLY_MENU_BUTTONS
ACTIVE_CASE_REPLY_MENU_BUTTONS = CANONICAL_REPLY_MENU_BUTTONS
COMPLETED_CASE_REPLY_MENU_BUTTONS = CANONICAL_REPLY_MENU_BUTTONS
MAIN_MENU_BUTTONS = CANONICAL_REPLY_MENU_BUTTONS


def reply_main_menu(
    case_exists: bool = False,
    *,
    completed_case: bool = False,
) -> ReplyKeyboardMarkup:
    # case_exists/completed_case remain in the public function signature because
    # older callers supply them. The persistent IA itself is deliberately stable.
    _ = (case_exists, completed_case)
    return ReplyKeyboardMarkup(
        keyboard=CANONICAL_REPLY_MENU_BUTTONS,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Главная · расчёт · дело · документы · юрист",
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

    # Inline actions are contextual; the persistent reply keyboard above is the
    # stable five-item navigation. These shortcuts focus on the selected Case.
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
