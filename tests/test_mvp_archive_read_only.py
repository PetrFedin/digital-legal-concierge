"""Dynamic aiogram-facing tests for fail-closed, case-scoped read-only archive."""
from types import SimpleNamespace

import pytest

from app.bot.screens import archived_cases as archive
from app.bot.screens import my_case


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.alerts = []

    async def answer(self, text=None, **kwargs):
        self.alerts.append((text, kwargs))


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def closed_case(case_id=40, client_id=5, status="M1_CLOSED"):
    return SimpleNamespace(
        id=case_id,
        client_id=client_id,
        status=status,
        case_number=f"DLC-2026-{case_id:06d}",
    )


class FakeCaseService:
    def __init__(self, cases, *, active_case_id=7):
        self.cases = cases
        self.active_case_id = active_case_id
        self.lookups = []
        self.selections = 0

    async def get_terminal_case_for_user(self, *, user_id, case_id):
        self.lookups.append((user_id, case_id))
        case = self.cases.get(case_id)
        if (
            case is None or case.client_id != user_id
            or case.status not in archive.ARCHIVE_STATUSES
        ):
            return None
        return case

    async def select_active_case(self, **kwargs):
        self.selections += 1
        raise AssertionError("Archive must never select or mutate an active matter")


def setup_context(monkeypatch, service, user_id=5):
    user = SimpleNamespace(id=user_id, active_case_id=service.active_case_id)

    class FakeContext:
        def __init__(self, db):
            self.case_service = service

        async def get_user_from_callback(self, callback):
            return user

    monkeypatch.setattr(archive, "BotContextService", FakeContext)
    return user


def fake_view(case):
    return SimpleNamespace(
        case_number=case.case_number,
        route_label="Взыскание М1",
        status_label="Закрыто",
        calculation_summary="Данные сохранены",
        documents=SimpleNamespace(summary="2 документа"),
        consultation_summary="Не назначена",
        enforcement_summary=None,
        closure_reason="Исполнено",
        updated_at=None,
    )


@pytest.mark.asyncio
async def test_owned_archive_shows_only_safe_navigation_without_switching(monkeypatch):
    case = closed_case()
    service = FakeCaseService({case.id: case})
    user = setup_context(monkeypatch, service)
    async def view(db, current):
        assert current is case
        return fake_view(current)

    monkeypatch.setattr(archive, "load_client_case_view", view)
    monkeypatch.setattr(archive, "format_updated_at", lambda v: "13.10.2026")
    db = FakeDB()
    callback = FakeCallback("cases_archive:40")
    await archive.open_readonly_archive(callback, db)
    text, markup = callback.message.edits[-1]
    assert "Архивное дело №" in text
    assert "Основание закрытия: Исполнено" in text
    assert callbacks(markup) == [
        "archive_docs:40:0", "archive_payments:40:0",
        "archive_history:40:0", "archive_messages:40:0", "cases_list", "nav_home",
    ]
    assert service.lookups == [(5, 40)]
    assert user.active_case_id == 7
    assert service.selections == 0
    assert db.commits == db.rollbacks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("request", [
    "cases_archive:41", "archive_docs:41:0",
    "archive_payments:41:0", "archive_history:41:0",
    "archive_messages:41:0",
])
async def test_foreign_case_denied_before_any_domain_data_read(
    monkeypatch, request
):
    service = FakeCaseService({41: closed_case(41, client_id=8)})
    setup_context(monkeypatch, service)
    db = FakeDB()

    class NoRead:
        def __init__(self, db):
            raise AssertionError("Private case data queried before ownership gate")

    monkeypatch.setattr(archive, "DocumentService", NoRead)
    monkeypatch.setattr(archive, "PaymentService", NoRead)
    monkeypatch.setattr(archive, "CaseActivityService", NoRead)
    monkeypatch.setattr(archive, "MessageService", NoRead)
    callback = FakeCallback(request)
    await archive.open_readonly_archive(callback, db)
    text, markup = callback.message.edits[-1]
    assert text == "Архивное дело недоступно. Откройте список своих обращений."
    assert callbacks(markup) == ["cases_list", "nav_home"]
    assert db.commits == db.rollbacks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("request", [
    "cases_archive:-1", "cases_archive:0", "cases_archive:7:extra",
    "archive_docs:7:-1", "archive_docs:7:10001",
    "archive_history:7:not-a-cursor", "archive_payments:7:１",
    "archive_messages:7:10001",
    "archive_docs:7:0:extra",
])
async def test_malformed_archive_callbacks_fail_closed(monkeypatch, request):
    setup_context(monkeypatch, FakeCaseService({}))
    db = FakeDB()
    callback = FakeCallback(request)
    await archive.open_readonly_archive(callback, db)
    assert "недействительна" in callback.message.edits[-1][0]
    assert db.commits == 0


@pytest.mark.asyncio
async def test_active_matter_id_denied_as_archive(monkeypatch):
    service = FakeCaseService({40: closed_case(status="M1_COURT_STAGE")})
    setup_context(monkeypatch, service)
    callback = FakeCallback("archive_docs:40:0")
    await archive.open_readonly_archive(callback, FakeDB())
    assert "недоступно" in callback.message.edits[-1][0]
    assert service.selections == 0


@pytest.mark.asyncio
async def test_archived_documents_paginate_without_mutation_actions(monkeypatch):
    case = closed_case()
    setup_context(monkeypatch, FakeCaseService({40: case}))
    documents = [
        SimpleNamespace(title=f"Документ {i}", version=1, status="ARCHIVED", lawyer_comment="")
        for i in range(14)
    ]
    class Docs:
        def __init__(self, db):
            pass
        async def list_case_documents(self, case_id):
            assert case_id == 40
            return documents

    monkeypatch.setattr(archive, "DocumentService", Docs)
    db = FakeDB()
    callback = FakeCallback("archive_docs:40:1")
    await archive.open_readonly_archive(callback, db)
    text, markup = callback.message.edits[-1]
    assert "Документ 6" in text and "Документ 11" in text
    assert "Документ 5" not in text
    choices = callbacks(markup)
    assert choices[:2] == ["archive_docs:40:0", "archive_docs:40:2"]
    assert "doc_finish_upload" not in choices
    assert "documents_upload_open" not in choices
    assert db.commits == 0


@pytest.mark.asyncio
async def test_archived_payments_are_displayed_without_pay_link(monkeypatch):
    case = closed_case()
    setup_context(monkeypatch, FakeCaseService({40: case}))
    class Payments:
        def __init__(self, db):
            pass
        async def list_case_payments(self, case_id):
            assert case_id == 40
            return [SimpleNamespace(title="Первый платёж", amount=30000, status="PAID")]

    monkeypatch.setattr(archive, "PaymentService", Payments)
    callback = FakeCallback("archive_payments:40:0")
    await archive.open_readonly_archive(callback, FakeDB())
    text, markup = callback.message.edits[-1]
    assert "Первый платёж" in text
    assert all(not x.startswith("pay_") for x in callbacks(markup))


@pytest.mark.asyncio
async def test_archived_history_has_bound_cursor_and_no_message_actions(monkeypatch):
    case = closed_case()
    setup_context(monkeypatch, FakeCaseService({40: case}))
    class Activity:
        def __init__(self, db):
            pass
        async def page(self, *, case_id, audience, before_id, limit):
            assert (case_id, audience, before_id, limit) == (40, "client", 23, 7)
            return {
                "items": [{"category":"case", "title":"Закрытие обращения",
                           "occurred_at":"2026-10-13T09:00:00+00:00", "detail":""}],
                "has_more":True, "next_before_id":11,
            }
    monkeypatch.setattr(archive, "CaseActivityService", Activity)
    callback = FakeCallback("archive_history:40:23")
    await archive.open_readonly_archive(callback, FakeDB())
    text, markup = callback.message.edits[-1]
    assert "Закрытие обращения" in text
    choices = callbacks(markup)
    assert choices[:2] == ["archive_history:40:0", "archive_history:40:11"]
    assert "message_create" not in choices


@pytest.mark.asyncio
async def test_case_list_pagination_can_reach_oldest_closed_matter(monkeypatch):
    cases = [
        closed_case(i, 5, "M1_CLOSED" if i % 2 == 0 else "M1_COURT_STAGE")
        for i in range(30, 0, -1)
    ]
    class Service:
        async def list_cases_for_user(self, user_id):
            assert user_id == 5
            return cases
        async def get_active_case_for_user(self, user_id):
            return cases[0]
    class Context:
        def __init__(self, db):
            self.case_service = Service()
        async def get_user_from_callback(self, callback):
            return SimpleNamespace(id=5)

    monkeypatch.setattr(my_case, "BotContextService", Context)
    db = FakeDB()
    callback = FakeCallback("cases_page:3")
    await my_case.list_my_cases(callback, db)
    text, markup = callback.message.edits[-1]
    assert "Страница 4 из 4" in text
    assert "DLC-2026-000006" in text
    assert "DLC-2026-000001" in text
    choices = callbacks(markup)
    assert "cases_archive:6" in choices
    assert "cases_select:1" in choices
    assert "cases_page:2" in choices
    assert db.commits == 0


@pytest.mark.asyncio
async def test_archive_correspondence_is_read_only_and_case_scoped(monkeypatch):
    case = closed_case()
    setup_context(monkeypatch, FakeCaseService({40: case}))
    class Messages:
        def __init__(self, db):
            pass
        async def list_case_messages_page(self, case_id, *, page, page_size):
            assert (case_id, page, page_size) == (40, 1, 5)
            return [
                SimpleNamespace(
                    text="Сообщение без повторной отправки", sender_type="lawyer",
                    created_at=None,
                )
            ], 1, 3
    monkeypatch.setattr(archive, "MessageService", Messages)
    callback = FakeCallback("archive_messages:40:1")
    await archive.open_readonly_archive(callback, FakeDB())
    text, markup = callback.message.edits[-1]
    assert "Сообщение без повторной отправки" in text
    assert "Страница 2 из 3" in text
    choices = callbacks(markup)
    assert choices[:2] == ["archive_messages:40:0", "archive_messages:40:2"]
    assert "message_submit" not in choices
    assert "message_create" not in choices


def test_archive_router_has_all_required_safe_handlers():
    registered = [route.callback for route in archive.router.callback_query.handlers]
    assert len(registered) == 1
    assert archive.parse_archive_callback("archive_messages:14:1") == (
        "archive_messages", 14, 1
    )
