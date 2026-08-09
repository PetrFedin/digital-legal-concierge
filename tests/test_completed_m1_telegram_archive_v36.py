from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_case_has_explicit_terminal_card_after_active_scope_disappears():
    source = read("app/bot/screens/my_case.py")

    assert "latest_completed_m1_case_for_user" in source
    assert "📁 ИТОГ ДЕЛА" in source
    assert "✅ Дело завершено" in source
    assert "progress_bar(100)" in source
    assert "Действий по этому делу больше не требуется" in source
    assert "Документы, история и платежи остаются доступны только для просмотра" in source
    assert '("📄 Документы дела", "documents_open")' in source


def test_completed_home_remains_discoverable_without_exposing_active_case_menu():
    source = read("app/bot/screens/common.py")

    assert "completed_m1 = await latest_completed_m1_case_for_user" in source
    assert "✅ Последнее дело завершено" in source
    assert 'return "\\n".join(lines), False, COMPLETED_M1_ACTION' in source
    assert "Итог, история и платежи сохранены в режиме просмотра" in source


def test_completed_history_payment_and_documents_use_read_only_scope():
    history = read("app/bot/screens/history.py")
    payments = read("app/bot/screens/payments.py")
    documents = read("app/bot/screens/documents.py")

    assert "active_or_latest_completed_m1_case_for_user" in history
    assert "История завершённого дела" in history
    assert 'if completed:' in history
    assert "active_or_latest_completed_m1_case_for_user" in payments
    assert "Оплаты завершённого дела" in payments
    assert "новые платежи из этого архива не создаются" in payments
    assert "_load_readonly_case_documents" in documents
    assert "Архив документов завершённого дела" in documents
    assert "загрузка, замена и повторная передача юристу недоступны" in documents


def test_archive_reads_never_replace_active_scope_for_payment_or_document_mutations():
    payments = read("app/bot/screens/payments.py")
    payment_start = payments.index("async def start_payment")
    payment_section = payments[payment_start:]
    documents = read("app/bot/screens/documents.py")
    mutation_loader = documents[
        documents.index("async def _load_case_documents") : documents.index(
            "async def _load_readonly_case_documents"
        )
    ]
    upload = documents[documents.index("async def upload(message:") :]

    assert "get_active_case_for_user(user.id)" in payment_section
    assert "active_or_latest_completed_m1_case_for_user" not in payment_section.split(
        "@router.callback_query(lambda c: c.data.startswith(\"pay_open:\"))",
        1,
    )[0]
    assert "get_active_case_for_user(user.id)" in mutation_loader
    assert "active_or_latest_completed_m1_case_for_user" not in mutation_loader
    assert "get_active_case_for_user(user.id)" in upload
