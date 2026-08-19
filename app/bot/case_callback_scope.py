from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one


@dataclass(frozen=True)
class CaseCallbackScope:
    ctx: BotContextService
    user: object
    case: object | None
    expected_case_id: int | None
    legacy_unbound: bool


def bound_case_callback(action: str, case_id: int) -> str:
    """Encode a mutating Telegram action with explicit Case provenance."""

    clean_action = str(action or "").strip()
    if not clean_action or ":" in clean_action:
        raise ValueError("action должен быть непустым callback token без ':'")
    case_value = int(case_id)
    if case_value <= 0:
        raise ValueError("case_id должен быть положительным")
    return f"{clean_action}:v2:{case_value}"


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


async def resolve_case_callback_scope(
    callback: CallbackQuery,
    db,
    *,
    action: str,
) -> CaseCallbackScope | None:
    """Resolve a mutation against the explicitly selected active Case.

    New callbacks must carry ``:v2:<case_id>``. Historical unbound buttons stay
    usable only while the client has zero/one active Case. With several active
    matters an old unbound mutation is ambiguous and therefore fails closed.

    A bound button never switches the cabinet implicitly: if the client has
    selected another Case since the message was rendered, the mutation is
    rejected and the user must choose the intended matter explicitly first.
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

    if legacy_unbound and len(active_cases) > 1:
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
