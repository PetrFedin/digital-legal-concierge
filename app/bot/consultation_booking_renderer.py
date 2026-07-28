from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.bot.consultation_booking_callbacks import ConsultationBookingCallbacks
from app.bot.keyboards import one
from app.domain.consultations.slot_selection_service import (
    ClientDateOption,
    ClientLawyerOption,
    ClientSlotOption,
    ConsultationSlotSelection,
)


MOSCOW = ZoneInfo("Europe/Moscow")
RU_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
RU_WEEKDAYS = (
    "пн",
    "вт",
    "ср",
    "чт",
    "пт",
    "сб",
    "вс",
)


def _local(value: datetime) -> datetime:
    aware = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value
    )
    return aware.astimezone(MOSCOW)


def _date_title(value: date) -> str:
    return f"{RU_WEEKDAYS[value.weekday()]}, {value.day} {RU_MONTHS[value.month - 1]}"


def _time(value: datetime) -> str:
    return _local(value).strftime("%H:%M")


def _slot_button(option: ClientSlotOption) -> tuple[str, str]:
    label = (
        f"{_time(option.starts_at)} · "
        f"{option.lawyer_name[:22]} · {option.duration_minutes} мин"
    )
    callback = f"consult_slot_select:{option.reference}"
    if len(callback.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data exceeds 64 bytes.")
    return label, callback


def render_selection_modes(selection: ConsultationSlotSelection):
    buttons = [
        (
            "⚡ Ближайшее свободное время",
            ConsultationBookingCallbacks.mode("nearest"),
        ),
        (
            "⚖️ Выбрать юриста",
            ConsultationBookingCallbacks.mode("lawyer"),
        ),
        (
            "📅 Выбрать дату",
            ConsultationBookingCallbacks.dates(),
        ),
    ]
    if (
        selection.assigned_lawyer_reference is not None
        and any(
            lawyer.reference == selection.assigned_lawyer_reference
            for lawyer in selection.lawyers
        )
    ):
        buttons.append(
            (
                "👤 Мой юрист",
                ConsultationBookingCallbacks.mode("assigned"),
            )
        )
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главное меню", "nav_home"),
        ]
    )
    return (
        "⚖️ Запись на консультацию\n\n"
        "Как вам удобнее выбрать консультацию?\n\n"
        "Время указано по Москве.",
        one(*buttons),
    )


def render_nearest(options: tuple[ClientSlotOption, ...], *, limit: int = 8):
    visible = options[:limit]
    if not visible:
        return render_empty("Сейчас нет доступного времени.")
    lines = ["⚡ Ближайшее свободное время", ""]
    for option in visible:
        local = _local(option.starts_at)
        lines.extend(
            [
                _date_title(local.date()),
                f"{_time(option.starts_at)}–{_time(option.ends_at)}",
                option.lawyer_name,
                option.specialization or "Общая юридическая практика",
                f"{option.duration_minutes} минут · {option.consultation_format}",
                "",
            ]
        )
    buttons = [_slot_button(option) for option in visible]
    buttons.extend(
        [
            ("🔄 Обновить", ConsultationBookingCallbacks.mode("nearest")),
            ("⬅ Назад", "consult_slot_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главное меню", "nav_home"),
        ]
    )
    return "\n".join(lines).strip(), one(*buttons)


def render_lawyers(lawyers: tuple[ClientLawyerOption, ...]):
    if not lawyers:
        return render_empty("Сейчас нет специалистов со свободным временем.")
    lines = [
        "⚖️ Выберите юриста",
        "",
        "Показаны только активные специалисты с доступным временем.",
        "",
    ]
    for lawyer in lawyers[:10]:
        nearest = _local(lawyer.nearest_at)
        lines.extend(
            [
                lawyer.full_name,
                lawyer.specialization or "Общая юридическая практика",
                f"Ближайшее время: {nearest:%d.%m} в {_time(lawyer.nearest_at)}",
                "",
            ]
        )
    buttons = [
        (
            f"⚖️ {lawyer.full_name[:36]}",
            ConsultationBookingCallbacks.lawyer(lawyer.reference),
        )
        for lawyer in lawyers[:10]
    ]
    buttons.extend(
        [
            ("⚡ Ближайшее время", ConsultationBookingCallbacks.mode("nearest")),
            ("⬅ Назад", "consult_slot_open"),
            ("🏠 Главное меню", "nav_home"),
        ]
    )
    return "\n".join(lines).strip(), one(*buttons)


def render_lawyer_card(lawyer: ClientLawyerOption):
    nearest = _local(lawyer.nearest_at)
    return (
        "⚖️ Консультация с юристом\n\n"
        f"{lawyer.full_name}\n\n"
        "Специализация:\n"
        f"{lawyer.specialization or 'Общая юридическая практика'}\n\n"
        "Ближайшее свободное время:\n"
        f"{_date_title(nearest.date())}, {_time(lawyer.nearest_at)}\n\n"
        f"Формат: {lawyer.consultation_format}",
        one(
            (
                "📅 Посмотреть даты",
                ConsultationBookingCallbacks.lawyer_dates(lawyer.reference),
            ),
            (
                "🕒 Ближайшее время",
                ConsultationBookingCallbacks.lawyer_dates(lawyer.reference),
            ),
            (
                "⬅ К списку юристов",
                ConsultationBookingCallbacks.mode("lawyer"),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главное меню", "nav_home"),
        ),
    )


def render_dates(
    dates: tuple[ClientDateOption, ...],
    *,
    page: int,
    lawyer_reference: int | None,
    page_size: int = 7,
):
    page = max(0, page)
    start = page * page_size
    visible = dates[start : start + page_size]
    if not visible:
        return (
            "📅 Выберите дату\n\n"
            "На выбранный период свободного времени нет.",
            one(
                (
                    "↩ К первой странице",
                    ConsultationBookingCallbacks.dates(
                        page=0,
                        lawyer_reference=lawyer_reference,
                    ),
                ),
                ("⚡ Ближайшее время", ConsultationBookingCallbacks.mode("nearest")),
                ("⬅ Назад", "consult_slot_open"),
                ("🏠 Главное меню", "nav_home"),
            ),
        )
    buttons = [
        (
            f"{_date_title(item.value)} · {item.option_count}",
            ConsultationBookingCallbacks.date(
                value=item.value,
                lawyer_reference=lawyer_reference,
            ),
        )
        for item in visible
    ]
    if page > 0:
        buttons.append(
            (
                "⬅ Предыдущие даты",
                ConsultationBookingCallbacks.dates(
                    page=page - 1,
                    lawyer_reference=lawyer_reference,
                ),
            )
        )
    if start + page_size < len(dates):
        buttons.append(
            (
                "➡ Следующие даты",
                ConsultationBookingCallbacks.dates(
                    page=page + 1,
                    lawyer_reference=lawyer_reference,
                ),
            )
        )
    buttons.extend(
        [
            ("⬅ Назад", "consult_slot_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главное меню", "nav_home"),
        ]
    )
    return (
        "📅 Выберите дату\n\n"
        "Показаны только дни со свободным временем. Время указано по Москве.",
        one(*buttons),
    )


def render_date_slots(
    selected_date: date,
    options: tuple[ClientSlotOption, ...],
    *,
    lawyer_reference: int | None,
):
    visible = tuple(
        option
        for option in options
        if _local(option.starts_at).date() == selected_date
    )
    if not visible:
        return render_empty("На эту дату свободных интервалов больше нет.")
    buttons = [_slot_button(option) for option in visible[:20]]
    buttons.extend(
        [
            (
                "⬅ Выбрать другую дату",
                ConsultationBookingCallbacks.dates(
                    lawyer_reference=lawyer_reference,
                ),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главное меню", "nav_home"),
        ]
    )
    return (
        f"🕒 {_date_title(selected_date)}\n\n"
        "Выберите свободное время. Время указано по Москве.",
        one(*buttons),
    )


def render_empty(message: str):
    return (
        f"⚖️ Запись на консультацию\n\n{message}\n\n"
        "Попробуйте другой способ выбора или обновите расписание позже.",
        one(
            ("⚡ Ближайшее время", ConsultationBookingCallbacks.mode("nearest")),
            ("⚖️ Другой юрист", ConsultationBookingCallbacks.mode("lawyer")),
            ("📅 Выбрать дату", ConsultationBookingCallbacks.dates()),
            ("⬅ Назад", "consult_slot_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главное меню", "nav_home"),
        ),
    )
