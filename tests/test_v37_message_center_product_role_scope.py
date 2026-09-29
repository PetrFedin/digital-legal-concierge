from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.api.message_center_product_impl as module
from app.api.message_center import StaffScope


def _request():
    return SimpleNamespace(cookies={})


def test_auxiliary_operator_alone_cannot_open_message_product(monkeypatch):
    async def compatibility_scope(request, db, header_token=None):
        return StaffScope(
            payload={"uid": 41},
            roles=frozenset({"operator"}),
            lawyer_id=None,
        )

    monkeypatch.setattr(module, "require_staff_scope", compatibility_scope)

    with pytest.raises(HTTPException) as error:
        asyncio.run(module.require_product_staff_scope(_request(), object(), None))

    assert error.value.status_code == 403
    assert "дополнительной" in str(error.value.detail)


def test_lawyer_with_auxiliary_operator_label_stays_lawyer_scoped(monkeypatch):
    async def compatibility_scope(request, db, header_token=None):
        # The historical compatibility boundary treats ROLE_OPERATOR as broad.
        # Product scope must repair that legacy shape rather than granting all cases.
        return StaffScope(
            payload={"uid": 42},
            roles=frozenset({"lawyer", "operator"}),
            lawyer_id=None,
        )

    async def lawyer_actor(db, token):
        return SimpleNamespace(lawyer=SimpleNamespace(id=77))

    monkeypatch.setattr(module, "require_staff_scope", compatibility_scope)
    monkeypatch.setattr(module, "require_lawyer_actor", lawyer_actor)

    scope = asyncio.run(module.require_product_staff_scope(_request(), object(), None))

    assert scope.roles == frozenset({"lawyer", "operator"})
    assert scope.lawyer_id == 77


def test_lawyer_scoped_projection_drops_foreign_conversations_and_recounts_metrics():
    scope = StaffScope(
        payload={"uid": 42},
        roles=frozenset({"lawyer", "operator"}),
        lawyer_id=77,
    )
    payload = {
        "conversation_count": 3,
        "unread_count": 9,
        "waiting_count": 3,
        "critical_count": 2,
        "today_count": 1,
        "unassigned_count": 1,
        "overdue_count": 2,
        "priority_note": "keep me",
        "items": [
            {
                "case_id": 1,
                "responsibility": {"lawyer_id": 77},
                "unread_count": 2,
                "waiting_for_reply": True,
                "critical": True,
                "today": False,
                "unassigned": False,
                "overdue": True,
            },
            {
                "case_id": 2,
                "responsibility": {"lawyer_id": 88},
                "unread_count": 6,
                "waiting_for_reply": True,
                "critical": True,
                "today": True,
                "unassigned": False,
                "overdue": True,
            },
            {
                "case_id": 3,
                "responsibility": {"lawyer_id": None},
                "unread_count": 1,
                "waiting_for_reply": True,
                "critical": False,
                "today": False,
                "unassigned": True,
                "overdue": False,
            },
        ],
    }

    result = module._filter_guided_status_to_scope(payload, scope)

    assert [item["case_id"] for item in result["items"]] == [1]
    assert result["conversation_count"] == 1
    assert result["unread_count"] == 2
    assert result["waiting_count"] == 1
    assert result["critical_count"] == 1
    assert result["today_count"] == 0
    assert result["unassigned_count"] == 0
    assert result["overdue_count"] == 1
    assert result["priority_note"] == "keep me"


def test_admin_with_auxiliary_operator_label_keeps_broad_operational_scope(monkeypatch):
    expected = StaffScope(
        payload={"uid": 43},
        roles=frozenset({"admin", "operator"}),
        lawyer_id=None,
    )

    async def compatibility_scope(request, db, header_token=None):
        return expected

    monkeypatch.setattr(module, "require_staff_scope", compatibility_scope)

    scope = asyncio.run(module.require_product_staff_scope(_request(), object(), None))
    assert scope is expected


def test_access_management_keeps_operator_as_auxiliary_not_base_workspace_role():
    source = open("app/api/access_management.py", encoding="utf-8").read()
    assert "PRODUCT_WORKSPACE_ROLES = frozenset({ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER})" in source
    assert "Роли «Оператор» и «Тестировщик» являются дополнительными" in source


def test_completed_self_filing_case_is_read_only_in_staff_message_center():
    source = open("app/api/guided_message_center.py", encoding="utf-8").read()
    assert '"M1_SELF_FILING_CLOSED"' in source
    assert "def _is_terminal(case: Case)" in source
