from datetime import datetime, date
from decimal import Decimal

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens.consultations import begin_m2_description_flow
from app.bot.states import CalculatorStates
from app.domain.calculator.penalty_calculator import parse_money
from app.domain.calculator.calculator_service import CalculatorService
from app.domain.calculator.calculator_result_formatter import format_calculation_result
from app.domain.statuses.case_statuses import CaseStatus

router = Router()


@router.callback_query(lambda c: c.data == "calc_start")
async def calc_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CalculatorStates.waiting_contract_price)
    await callback.message.edit_text(
        "🧮 Расчет неустойки\n\n"
        "Ответьте на несколько вопросов. Расчет будет предварительным и не является юридическим заключением.\n\n"
        "💰 Введите стоимость объекта по ДДУ в рублях.\n"
        "Например: 8500000",
        reply_markup=one(("Не знаю стоимость", "calc_unknown_price"), ("🏠 Главная", "nav_home")),
    )


@router.message(CalculatorStates.waiting_contract_price)
async def price(message: Message, state: FSMContext):
    try:
        amount = parse_money(message.text)
    except Exception:
        await message.answer("⚠️ Введите сумму цифрами. Например: 8500000")
        return
    await state.update_data(contract_price=str(amount))
    await state.set_state(CalculatorStates.waiting_planned_transfer_date)
    await message.answer(
        "📅 Укажите дату передачи объекта по ДДУ. Формат ДД.ММ.ГГГГ",
        reply_markup=one(("Не знаю дату", "calc_unknown_date"), ("Отмена", "nav_home")),
    )


@router.message(CalculatorStates.waiting_planned_transfer_date)
async def planned(message: Message, state: FSMContext):
    try:
        planned_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer("⚠️ Дата нужна в формате ДД.ММ.ГГГГ")
        return
    if planned_date > date.today():
        await message.answer(
            "Дата передачи еще не наступила. Автоматический расчет сейчас невозможен. Лучше обсудить ситуацию с юристом.",
            reply_markup=one(("💬 Перейти к юристу", "calc_unknown_date"), ("🏠 Главная", "nav_home")),
        )
        return
    await state.update_data(planned_transfer_date=planned_date.isoformat())
    await state.set_state(CalculatorStates.waiting_object_transfer_status)
    await message.answer(
        "🏗 Объект уже передан по акту?",
        reply_markup=one(
            ("Да, передан", "calc_object_transferred_yes"),
            ("Нет, не передан", "calc_object_transferred_no"),
        ),
    )


@router.callback_query(lambda c: c.data == "calc_object_transferred_yes")
async def yes(callback: CallbackQuery, state: FSMContext):
    await state.update_data(object_transferred=True)
    await state.set_state(CalculatorStates.waiting_actual_transfer_date)
    await callback.message.edit_text(
        "📅 Укажите дату фактической передачи по акту. Формат ДД.ММ.ГГГГ"
    )


@router.message(CalculatorStates.waiting_actual_transfer_date)
async def actual(message: Message, state: FSMContext, db):
    try:
        actual_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer("⚠️ Дата нужна в формате ДД.ММ.ГГГГ")
        return
    data = await state.get_data()
    planned_date = date.fromisoformat(data["planned_transfer_date"])
    if actual_date < planned_date:
        await message.answer("⚠️ Фактическая дата передачи не может быть раньше даты по ДДУ.")
        return
    if actual_date > date.today():
        await message.answer("⚠️ Фактическая дата передачи не может быть в будущем.")
        return
    await state.update_data(actual_transfer_date=actual_date.isoformat())
    await calculate_show_message(message, state, db)


@router.callback_query(lambda c: c.data == "calc_object_transferred_no")
async def no(callback: CallbackQuery, state: FSMContext, db):
    await state.update_data(object_transferred=False, actual_transfer_date=None)
    await calculate_show_callback(callback, state, db)


async def calc_result(state, db, case):
    data = await state.get_data()
    return await CalculatorService(db).calculate_and_save(
        case=case,
        contract_price=Decimal(data["contract_price"]),
        planned_transfer_date=date.fromisoformat(data["planned_transfer_date"]),
        object_transferred=bool(data["object_transferred"]),
        actual_transfer_date=(
            date.fromisoformat(data["actual_transfer_date"])
            if data.get("actual_transfer_date")
            else None
        ),
    )


async def calculate_show_message(message: Message, state: FSMContext, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.get_or_create_active_case_for_user(user)
    result = await calc_result(state, db, case)
    await db.commit()
    await state.clear()
    await message.answer(format_calculation_result(result), reply_markup=result_kb())


async def calculate_show_callback(callback: CallbackQuery, state: FSMContext, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.get_or_create_active_case_for_user(user)
    result = await calc_result(state, db, case)
    await db.commit()
    await state.clear()
    await callback.message.edit_text(format_calculation_result(result), reply_markup=result_kb())


def result_kb():
    return one(
        ("Продолжить работу", "calc_continue_m1"),
        ("💬 Связаться с юристом", "calc_to_m2"),
        ("Пока изучаю вопрос", "calc_postpone"),
        ("🏠 Главная", "nav_home"),
    )


@router.callback_query(lambda c: c.data in {"calc_unknown_price", "calc_unknown_date"})
async def unknown_calc_data(callback: CallbackQuery, state: FSMContext, db):
    reason = "Клиент не знает стоимость" if callback.data == "calc_unknown_price" else "Клиент не знает дату передачи"
    await begin_m2_description_flow(
        callback=callback,
        state=state,
        db=db,
        reason=reason,
    )


@router.callback_query(lambda c: c.data == "calc_continue_m1")
async def to_m1(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.get_or_create_active_case_for_user(user)
    await ctx.case_service.change_status(
        case=case,
        next_status=CaseStatus.CLIENT_DECISION,
        actor_type="client",
        actor_id=user.id,
        force=True,
        comment="Клиент выбрал продолжение работы по М1",
    )
    await db.commit()
    await callback.message.edit_text(
        "📄 Чтобы передать документы юристу, нужно подтвердить согласие на обработку персональных данных.",
        reply_markup=one(("Перейти к согласию", "consent_open"), ("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "calc_to_m2")
async def to_m2(callback: CallbackQuery, state: FSMContext, db):
    await begin_m2_description_flow(
        callback=callback,
        state=state,
        db=db,
        reason="Клиент выбрал консультацию",
    )


@router.callback_query(lambda c: c.data == "calc_postpone")
async def postpone(callback: CallbackQuery):
    await callback.message.edit_text(
        "📌 Расчет сохранен. Вернуться можно через 📁 Мое дело.",
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")),
    )
