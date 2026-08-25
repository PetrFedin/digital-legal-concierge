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
