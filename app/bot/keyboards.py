from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings

MAIN_MENU_BUTTONS = [
    [KeyboardButton(text="🧮 Рассчитать неустойку")],
    [KeyboardButton(text="📁 Мое дело"), KeyboardButton(text="📄 Документы")],
    [KeyboardButton(text="💬 Связаться с юристом")],
    [KeyboardButton(text="🏠 Главная")],
]


def reply_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=MAIN_MENU_BUTTONS,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите: расчет, дело, документы или помощь",
    )


def _payments_enabled() -> bool:
    return settings.payment_provider.strip().lower() != "disabled"


def main_menu(
    case_exists: bool = False,
    *,
    payments_enabled: bool | None = None,
    primary_action: tuple[str, str] | None = None,
):
    kb = InlineKeyboardBuilder()

    if not case_exists:
        kb.button(text="🧮 Рассчитать неустойку", callback_data="calc_start")
        kb.button(text="💬 Связаться с юристом", callback_data="contact_lawyer")
        kb.adjust(1, 1)
        return kb.as_markup()

    if primary_action:
        text, callback_data = primary_action
        kb.button(text=text, callback_data=callback_data)

    kb.button(text="📁 Моё дело", callback_data="my_case_open")
    kb.button(text="📄 Документы", callback_data="documents_open")
    kb.button(text="💬 Переписка", callback_data="message_history")
    kb.button(text="✉️ Новый вопрос", callback_data="message_create")

    show_payments = _payments_enabled() if payments_enabled is None else payments_enabled
    if show_payments:
        kb.button(text="💳 Оплаты", callback_data="payments_open")

    kb.button(text="⚖️ Помощь и консультация", callback_data="contact_lawyer")

    row_sizes: list[int] = []
    if primary_action:
        row_sizes.append(1)
    row_sizes.extend([2, 2])
    if show_payments:
        row_sizes.append(1)
    row_sizes.append(1)
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