"""Post-retention erasure must win over old Telegram UI and preserved ledgers."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bot.screens import archived_cases, documents, history, my_case, payments


def erased_case():
    return SimpleNamespace(
        id=45,
        client_id=5,
        case_number="DLC-2026-000045",
        route="M1",
        status="M1_CLOSED",
        content_deleted_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
    )


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.acknowledgements = []

    async def answer(self, text=None, **kwargs):
        self.acknowledgements.append((text, kwargs))


class FakeCaseService:
    def __init__(self, case):
        self.case = case

    async def get_active_case_for_user(self, user_id):
        assert user_id == 5
        return None

    async def get_latest_case_for_user(self, user_id):
        assert user_id == 5
        return self.case


class FakeContext:
    def __init__(self, case):
        self.case_service = FakeCaseService(case)

    async def get_user_from_callback(self, callback):
        return SimpleNamespace(id=5)


class FakeDB:
    async def rollback(self):
        raise AssertionError("Read-only view must not roll back a business action")

    async def commit(self):
        raise AssertionError("Read-only view must not commit a business action")


def text_and_buttons(callback):
    text, markup = callback.message.edits[-1]
    buttons = [button.callback_data for row in markup.inline_keyboard for button in row]
    return text, buttons


def forbid(*args, **kwargs):
    raise AssertionError("Erased-case private content was queried")


async def forbidden(*args, **kwargs):
    forbid()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["my_case_open", "next_action:v2:45:stale"])
async def test_old_my_case_buttons_cannot_show_erased_content(monkeypatch, action):
    case = erased_case()

    async def case_context(callback, db):
        return FakeContext(case), SimpleNamespace(id=5), case

    monkeypatch.setattr(my_case, "_active_case_context", case_context)
    monkeypatch.setattr(my_case, "load_client_case_view", forbidden)
    callback = FakeCallback(action)
    if action == "my_case_open":
        await my_case._render_case(callback, FakeDB())
    else:
        await my_case.next_action(callback, FakeDB())
    text, buttons = text_and_buttons(callback)
    assert "Содержимое дела удалено" in text
    assert buttons == ["cases_list", "nav_home"]
    assert "SECRET_CLOSURE" not in text


@pytest.mark.asyncio
async def test_old_timeline_button_never_reads_retained_audit(monkeypatch):
    case = erased_case()
    monkeypatch.setattr(history, "BotContextService", lambda db: FakeContext(case))

    class NoAudit:
        def __init__(self, db):
            forbid()

    monkeypatch.setattr(history, "CaseActivityService", NoAudit)
    callback = FakeCallback("case_history_before:13")
    await history._render_history(callback, FakeDB(), cursor=13)
    text, buttons = text_and_buttons(callback)
    assert "Содержимое дела удалено" in text
    assert buttons == ["cases_list", "nav_home"]


@pytest.mark.asyncio
async def test_old_payments_list_cannot_read_retained_financial_ledger(monkeypatch):
    case = erased_case()
    monkeypatch.setattr(payments, "BotContextService", lambda db: FakeContext(case))

    class NoPayments:
        def __init__(self, db):
            forbid()

    monkeypatch.setattr(payments, "PaymentService", NoPayments)
    callback = FakeCallback("payments_open")
    await payments.payments(callback, FakeDB())
    text, buttons = text_and_buttons(callback)
    assert "Содержимое дела удалено" in text
    assert buttons == ["cases_list", "nav_home"]


@pytest.mark.asyncio
@pytest.mark.parametrize("view", [
    "_render_documents_home",
    "_render_current_documents",
    "_render_document_history",
])
async def test_old_document_views_never_load_erased_files(monkeypatch, view):
    case = erased_case()
    monkeypatch.setattr(documents, "BotContextService", lambda db: FakeContext(case))

    class NoDocuments:
        def __init__(self, db):
            forbid()

    monkeypatch.setattr(documents, "DocumentService", NoDocuments)
    callback = FakeCallback("documents_open")
    handler = getattr(documents, view)
    await handler(callback, FakeDB())
    text, buttons = text_and_buttons(callback)
    assert "Содержимое дела удалено" in text
    assert buttons == ["cases_list", "nav_home"]


@pytest.mark.asyncio
async def test_old_pay_open_refuses_erased_case_even_if_payment_row_survives(monkeypatch):
    case = erased_case()
    monkeypatch.setattr(payments, "BotContextService", lambda db: FakeContext(case))
    payment = SimpleNamespace(
        id=4, case_id=case.id, status="PENDING",
        title="Previously issued bill", amount=Decimal("30000"),
    )

    class ScalarResult:
        def scalars(self):
            return self

        def first(self):
            return case

    class PaymentDB:
        async def get(self, model, id):
            assert id == 4
            return payment

        async def execute(self, statement):
            return ScalarResult()

    callback = FakeCallback("pay_open:4")
    selected_payment, selected_case = await payments.get_owned_payment(
        callback, PaymentDB(), 4
    )
    assert selected_payment is selected_case is None
    assert callback.acknowledgements[-1][1].get("show_alert") is True


@pytest.mark.asyncio
async def test_old_closed_payment_page_has_no_provider_link(monkeypatch):
    case = erased_case()
    case.content_deleted_at = None
    payment = SimpleNamespace(
        id=4, case_id=case.id, status="PENDING",
        title="Previously issued bill", amount=Decimal("30000"),
        payment_url="https://example.invalid/pay",
    )

    async def owned(*args, **kwargs):
        return payment, case

    monkeypatch.setattr(payments, "get_owned_payment", owned)
    callback = FakeCallback("pay_open:4")
    await payments.open_payment(callback, FakeDB())
    text, buttons = text_and_buttons(callback)
    assert "30 000" in text
    assert buttons == ["cases_archive:45", "cases_list", "nav_home"]
    markup = callback.message.edits[-1][1]
    assert not [b.url for row in markup.inline_keyboard for b in row if b.url]


@pytest.mark.asyncio
async def test_old_fake_payment_button_cannot_mutate_closed_matter(monkeypatch):
    case = erased_case()
    case.content_deleted_at = None
    payment = SimpleNamespace(
        id=4, case_id=case.id, status="PENDING",
        payment_code="M1_INITIAL_PAYMENT", provider="fake",
    )

    async def owned(*args, **kwargs):
        return payment, case

    class NoWebhook:
        def __init__(self, db):
            forbid()

    monkeypatch.setattr(payments, "get_owned_payment", owned)
    monkeypatch.setattr(payments, "fake_payments_enabled", lambda: True)
    monkeypatch.setattr(payments, "PaymentWebhookService", NoWebhook)
    callback = FakeCallback("pay_fake_success:4")
    await payments.fake(callback, FakeDB())
    assert any("Закрытое дело" in str(msg) for msg, _ in callback.acknowledgements)
