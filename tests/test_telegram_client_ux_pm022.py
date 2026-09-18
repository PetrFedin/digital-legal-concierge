from pathlib import Path

from app.bot.keyboards import reply_main_menu


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _reply_texts() -> list[str]:
    return [
        button.text
        for row in reply_main_menu(True).keyboard
        for button in row
    ]


def test_client_persistent_navigation_keeps_one_stable_information_architecture() -> None:
    assert _reply_texts() == [
        "🏠 Главная",
        "🧮 Рассчитать неустойку",
        "📁 Моё дело",
        "📄 Документы",
        "💬 Связаться с юристом",
    ]


def test_home_and_my_case_share_current_state_next_action_hierarchy() -> None:
    home = _source("app/bot/screens/common.py")
    case = _source("app/bot/screens/my_case.py")

    assert "Текущий этап" in home
    assert "📌 Ваш следующий шаг" in home
    assert "СЕЙЧАС" in case
    assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in case
    assert "⚠️ ЧТО МЕШАЕТ ПРОДОЛЖИТЬ" in case
    assert "view.status_label" in case
    assert "view.next_action" in case


def test_consultation_recovery_copy_states_what_did_not_change() -> None:
    source = _source("app/bot/screens/consultations.py")

    assert "Старая кнопка не меняет активное дело" in source
    assert "Консультация уже завершена" in source
    assert "Текущая запись не изменена" in source
    assert "Ничего не изменено" in source
    assert "Повторная оплата не потребуется" in source


def test_completed_case_card_is_explicitly_read_only() -> None:
    source = _source("app/bot/screens/my_case.py")

    assert "Действий по этому обращению больше не требуется" in source
    assert "Документы, история и платежи остаются доступны только для просмотра" in source
    assert "Новое обращение создаётся отдельно" in source
