from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_operator_landing_separates_daily_admin_and_superadmin_leadership_work() -> None:
    source = read("app/api/operator.py")

    assert "if(roles.includes('superadmin'))return 'Суперадминистратор'" in source
    assert "Администратор · Суперадминистратор" not in source
    assert "link('/consultation-slots/ui','Расписание консультаций'" in source
    assert 'id="leadershipSection" hidden' in source
    assert "link('/access/ui','Доступ сотрудников'" in source
    assert "link('/audit-center/ui','Аудит'" in source
    assert "link('/backup-center/ui','Резервные копии'" in source
    assert "link('/retention/ui','Хранение данных'" in source
    assert source.count("link('/admin/sla/ui'") == 1
    assert source.count("link('/admin/notification-delivery/ui'") == 1


def test_operator_landing_has_keyboard_focus_and_live_access_feedback() -> None:
    source = read("app/api/operator.py")

    assert ".link:focus-visible" in source
    assert "button:focus-visible" in source
    assert 'role="status" aria-live="polite"' in source


def test_canonical_telegram_reply_menu_is_one_screen_case_bound_action_hub() -> None:
    direct = read("app/bot/screens/reply_menu_direct.py")
    bot = read("app/bot/bot.py")
    common = read("app/bot/screens/common.py")

    assert '@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})' in direct
    assert "await common._home_text(" in direct
    assert "markup = one(*my_case._case_buttons(view))" in direct
    assert "await message.answer(text, reply_markup=markup)" in direct
    assert 'text.replace("\\nТекущий этап\\n", "\\nСЕЙЧАС\\n")' in direct
    assert '"\\nГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\\n"' in direct
    assert bot.index("reply_menu_direct.router") < bot.index("common.router")
    assert '"📌 Ваш следующий шаг"' in common
    assert '"Главная кнопка ниже ведёт к самому актуальному действию."' in common


def test_canonical_telegram_document_center_has_current_state_and_one_next_step() -> None:
    direct = read("app/bot/screens/reply_menu_direct.py")

    block = direct.split(
        '@router.message(lambda m: m.text == "📄 Документы")', 1
    )[1].split(
        '@router.message(lambda m: m.text == "💬 Переписка")', 1
    )[0]
    assert '"СЕЙЧАС\\n"' in block
    assert '"ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\\n"' in block
    assert "_show_selector_if_ambiguous(" in block
    assert "DocumentService(db).list_case_documents(case.id)" in block

def test_superadmin_leadership_surfaces_use_one_russian_navigation_language() -> None:
    audit = read("app/api/audit_center.py")
    backup = read("app/api/backup_center_impl.py")
    retention = read("app/api/retention_center.py")

    assert "<title>Аудит действий</title>" in audit
    assert "🧾 Аудит действий" in audit
    assert 'href="/operator">Руководство и контроль</a>' in audit

    assert "<title>Резервные копии</title>" in backup
    assert "💾 Резервные копии" in backup
    assert 'href="/operator">Руководство и контроль</a>' in backup
    assert "События безопасности" in backup
    assert "Проверка запуска" in backup

    assert "<title>Хранение данных</title>" in retention
    assert "⚖ Хранение данных" in retention
    assert 'href="/operator" style="color:white">Руководство и контроль</a>' in retention
    assert 'href="/admin-ui"' not in retention
    assert "Проверить без изменений" in retention


def test_admin_financial_surfaces_return_to_canonical_staff_hub() -> None:
    payment_review = read("app/api/payment_review_center.py")
    refunds = read("app/api/refund_center.py")

    assert 'href="/operator">Все разделы</a>' in payment_review
    assert 'href="/admin-ui">Админка</a>' not in payment_review
    assert 'href="/operator">Все разделы</a>' in refunds
    assert "focus-visible" in payment_review
    assert "focus-visible" in refunds

