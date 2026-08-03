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
):
    kb = InlineKeyboardBuilder()
    kb.button(text="🧮 Рассчитать неустойку", callback_data="calc_start")

    if case_exists:
        kb.button(text="📁 Мое дело", callback_data="my_case_open")
        kb.button(text="📄 Документы", callback_data="documents_open")
        show_payments = _payments_enabled() if payments_enabled is None else payments_enabled
        if show_payments:
            kb.button(text="💳 Оплаты", callback_data="payments_open")

    kb.button(text="💬 Связаться с юристом", callback_data="contact_lawyer")

    if not case_exists:
        kb.adjust(1, 1)
    elif (_payments_enabled() if payments_enabled is None else payments_enabled):
        kb.adjust(1, 2, 1, 1)
    else:
        kb.adjust(1, 2, 1)
    return kb.as_markup()


def home_kb():
    return main_menu(False)


def one(*items):
    kb = InlineKeyboardBuilder()
    for text, cb in items:
        kb.button(text=text, callback_data=cb)
    kb.adjust(1)
    return kb.as_markup()
