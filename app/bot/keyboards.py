from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

MAIN_MENU_BUTTONS = [
    [KeyboardButton(text='🏠 Главная'), KeyboardButton(text='🧮 Рассчитать неустойку')],
    [KeyboardButton(text='📁 Мое дело'), KeyboardButton(text='📄 Документы')],
    [KeyboardButton(text='💬 Связаться с юристом')],
]


def reply_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=MAIN_MENU_BUTTONS,
        resize_keyboard=True,
        input_field_placeholder='Выберите действие',
    )


def main_menu(case_exists: bool = False):
    kb = InlineKeyboardBuilder()
    kb.button(text='🧮 Рассчитать неустойку', callback_data='calc_start')
    if case_exists:
        kb.button(text='📁 Мое дело', callback_data='my_case_open')
        kb.button(text='📄 Документы', callback_data='documents_open')
        kb.button(text='💳 Оплаты', callback_data='payments_open')
    kb.button(text='💬 Связаться с юристом', callback_data='contact_lawyer')
    kb.adjust(1)
    return kb.as_markup()


def home_kb():
    return main_menu(False)


def one(*items):
    kb = InlineKeyboardBuilder()
    for text, cb in items:
        kb.button(text=text, callback_data=cb)
    kb.adjust(1)
    return kb.as_markup()
