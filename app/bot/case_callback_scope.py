from __future__ import annotations

import re
from dataclasses import dataclass

from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one


PAYMENT_CASE_BOUND_ACTIONS = frozenset(
    {
        "pay_start_30000",
        "pay_court_70000",
        "pay_success_fee",
        "pay_self_filing",
        "consult_pay",
    }
)
CASE_BOUND_MUTATING_ACTIONS = frozenset(
    {
        *PAYMENT_CASE_BOUND_ACTIONS,
        "calc_recover",
        "message_create",
        "contract_open",
        "poa_instruction",
        "court_status",
        "consult_subject_start",
        "consult_description_start",
        "consult_booking_start",
        "consult_slot_open",
        "consult_reschedule",
        "consult_cancel",
        "consult_follow_up_start",
        # Case-sensitive entry/read screens can expose subsequent actions and
        # therefore need exact provenance on fresh My Case keyboards as well.
        "contact_lawyer",
        "consultation_booked_open",
        "documents_open",
        "payments_open",
        "consultation_result_open",
    }
)
_CASE_NUMBER_PATTERN = re.compile(r"\bDLC-\d{4}-\d{6}\b")


@dataclass(frozen=True)
class CaseCallbackScope:
    ctx: BotContextService
    user: object
    case: object | None
    expected_case_id: int | None
    legacy_unbound: bool


def bound_case_callback(action: str, case_id: int) -> str:
    """Encode a Case-sensitive Telegram action with explicit Case provenance."""

    clean_action = str(action or "").strip()
    if not clean_action or ":" in clean_action:
        raise ValueError("action должен быть непустым callback token без ':'")
    case_value = int(case_id)
    if case_value <= 0:
        raise ValueError("case_id должен быть положительным")
    return f"{clean_action}:v2:{case_value}"


def bind_payment_case_action(action: str, case_id: int) -> str:
    """Bind every known Case-sensitive action emitted by My Case.

    The helper name is retained for compatibility with the existing My Case
    renderer. It originally covered payments only; message entry, calculator
    recovery, contextual document/payment/result/contact screens, M1 action
    screens and M2 question/booking/change actions must also not be reinterpreted
    against whichever Case happens to be selected later. Truly global navigation
    callbacks remain unchanged.
    """

    clean_action = str(action or "").strip()
    if clean_action in CASE_BOUND_MUTATING_ACTIONS:
        return bound_case_callback(clean_action, case_id)
    return clean_action


def callback_matches_action(data: str | None, action: str) -> bool:
    value = str(data or "")
    return value == action or value.startswith(f"{action}:v2:")


def parse_bound_case_id(data: str | None, action: str) -> tuple[bool, int | None]:
    """Return (legacy_unbound, expected_case_id); malformed v2 raises ValueError."""

    value = str(data or "")
    if value == action:
        return True, None
    prefix = f"{action}:v2:"
    if not value.startswith(prefix):
        raise ValueError("callback не соответствует ожидаемому действию")
    raw = value[len(prefix) :]
    if not raw or ":" in raw:
        raise ValueError("повреждён case-bound callback")
    try:
        case_id = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError("повреждён case_id callback") from error
    if case_id <= 0:
        raise ValueError("некорректный case_id callback")
    return False, case_id


def _legacy_message_text(callback: CallbackQuery) -> str:
    message = getattr(callback, "message", None)
    return str(
        getattr(message, "text", "") or getattr(message, "caption", "") or ""
    )


def _legacy_message_case_numbers(callback: CallbackQuery) -> frozenset[str]:
    """Extract only canonical bot Case numbers visible on a legacy screen."""

    return frozenset(_CASE_NUMBER_PATTERN.findall(_legacy_message_text(callback)))


def _legacy_message_mentions_selected_case(callback: CallbackQuery, selected_case) -> bool:
    """Recognize a trusted bot-rendered legacy screen with visible Case context."""

    if selected_case is None:
        return False
    case_number = str(getattr(selected_case, "case_number", "") or "").strip()
    if not case_number:
        return False
    return case_number in _legacy_message_case_numbers(callback)


def _legacy_message_names_other_case(callback: CallbackQuery, selected_case) -> bool:
    """Detect a stale legacy screen even when only one active Case remains.

    Before v2 rollout, callbacks carried no Case id. A client can finish Case A,
    later open Case B, and still press a raw button on an old bot screen for A.
    Counting active cases alone would reinterpret that stale action as Case B.
    If the trusted bot-rendered screen visibly names a canonical Case number that
    is not the currently selected one, fail closed instead. Legacy screens with
    no Case number keep the one-active-Case compatibility path.
    """

    numbers = _legacy_message_case_numbers(callback)
    if not numbers:
        return False
    selected_number = str(
        getattr(selected_case, "case_number", "") if selected_case is not None else ""
    ).strip()
    return not selected_number or selected_number not in numbers


async def resolve_case_callback_scope(
    callback: CallbackQuery,
    db,
    *,
    action: str,
    allow_legacy_message_case_context: bool = False,
) -> CaseCallbackScope | None:
    """Resolve a Case-sensitive action against the selected active Case.

    New callbacks may carry ``:v2:<case_id>``. Historical unbound buttons stay
    usable only while their context is unambiguous. With several active matters
    an old unbound action is blocked unless the bot-rendered message visibly
    names the exact selected Case.

    A raw legacy button is also blocked when its bot message visibly names a
    *different* canonical Case, even if only one active Case remains now. This
    closes the terminal-Case-A -> new-Case-B stale-screen reinterpretation gap
    while preserving compatibility for genuinely old screens with no Case number.

    A bound button never switches the cabinet implicitly: if the client has
    selected another Case since the message was rendered, the action is rejected
    and the user must choose the intended matter explicitly first.
    """

    try:
        legacy_unbound, expected_case_id = parse_bound_case_id(callback.data, action)
    except ValueError:
        await callback.message.edit_text(
            "Эта кнопка повреждена или относится к старой версии экрана. Действие не выполнено.",
            reply_markup=one(
                ("📁 Открыть мои обращения", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return None

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    selected_case = await ctx.case_service.get_active_case_for_user(int(user.id))

    legacy_message_bound = bool(
        legacy_unbound
        and allow_legacy_message_case_context
        and _legacy_message_mentions_selected_case(callback, selected_case)
    )
    legacy_message_conflict = bool(
        legacy_unbound
        and allow_legacy_message_case_context
        and _legacy_message_names_other_case(callback, selected_case)
    )

    if legacy_message_conflict:
        await callback.message.edit_text(
            "Эта старая кнопка относится к другому обращению, чем выбрано сейчас. "
            "Действие не выполнено: старый экран не может быть перенесён в новый контекст автоматически.\n\n"
            "Откройте актуальное дело и повторите действие с нового экрана.",
            reply_markup=one(
                ("📁 Мои обращения", "my_cases_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return None

    if legacy_unbound and len(active_cases) > 1 and not legacy_message_bound:
        await callback.message.edit_text(
            "Эта старая кнопка не содержит номер обращения, а у вас сейчас несколько активных дел. "
            "Чтобы платёж или другое значимое действие не попало в чужой контекст, оно не выполнено.\n\n"
            "Выберите нужное обращение и откройте его актуальный шаг.",
            reply_markup=one(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return None

    if expected_case_id is not None:
        target = next(
            (item for item in active_cases if int(item.id) == expected_case_id),
            None,
        )
        if target is None:
            await callback.message.edit_text(
                "Эта кнопка относится к обращению, которое уже не активно. Действие не выполнено.",
                reply_markup=one(
                    ("📁 Мои обращения", "my_cases_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return None
        if selected_case is None or int(selected_case.id) != expected_case_id:
            await callback.message.edit_text(
                f"Эта кнопка относится к обращению № {target.case_number}, но сейчас выбрано другое дело. "
                "Действие не выполнено. Сначала переключитесь на нужное обращение.",
                reply_markup=one(
                    ("📁 Выбрать обращение", "my_cases_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return None
        selected_case = target

    return CaseCallbackScope(
        ctx=ctx,
        user=user,
        case=selected_case,
        expected_case_id=expected_case_id,
        legacy_unbound=legacy_unbound,
    )
