import asyncio
from types import SimpleNamespace

import pytest

from app.api.guided_lawyer_ui import guided_workspace_data
from app.api.lawyer_m1_enforcement import router as enforcement_router
from app.api.message_center_role_ui import (
    _BAD_DOCUMENT_LINK,
    role_safe_message_center_html,
    role_safe_message_center_ui,
)
from app.domain.cases.case_responsibility import M2_CURRENT_LAWYER_STATUSES
from app.main import create_app
from app.models.case import Case
from app.models.document import Document
from app.security import document_access
from app.security.document_access import DocumentAccessError
from app.security.document_encryption import ENCRYPTION_STATUS, FORMAT_V2


def _first_endpoint(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return getattr(route, "endpoint", None)
    return None


def test_enforcement_router_is_mounted_in_create_app():
    app = create_app()
    mounted = {
        (getattr(route, "path", None), method)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    for route in enforcement_router.routes:
        for method in getattr(route, "methods", set()):
            assert (route.path, method) in mounted


def test_role_safe_message_center_ui_wins_route_precedence():
    app = create_app()
    assert _first_endpoint(app, "/message-center/ui", "GET") is role_safe_message_center_ui
    html = role_safe_message_center_html()
    assert "/document-access/ui?case_id=" in html
    assert _BAD_DOCUMENT_LINK not in html


def test_guided_workspace_data_wins_route_precedence():
    app = create_app()
    assert _first_endpoint(app, "/lawyer/workspace/data", "GET") is guided_workspace_data


def test_cancelled_or_rescheduled_consultation_does_not_keep_m2_ownership():
    assert "CANCELLED" not in M2_CURRENT_LAWYER_STATUSES
    assert "RESCHEDULED" not in M2_CURRENT_LAWYER_STATUSES
    assert "BOOKED" in M2_CURRENT_LAWYER_STATUSES
    assert "DONE" in M2_CURRENT_LAWYER_STATUSES


class _DocumentDb:
    def __init__(self, document, case):
        self.document = document
        self.case = case

    async def get(self, model, key):
        if model is Document:
            return self.document
        if model is Case:
            return self.case
        raise AssertionError(model)


def _secure_document():
    return SimpleNamespace(
        id=31,
        case_id=10,
        security_status="VERIFIED",
        encryption_status=ENCRYPTION_STATUS,
        data_key_destroyed_at=None,
        encryption_format_version=FORMAT_V2,
        encryption_key_id="key-1",
        encryption_envelope_id="env-1",
        encrypted_data_key=b"ciphertext",
        encrypted_data_key_nonce=b"nonce",
    )


def test_document_access_uses_route_aware_lawyer_responsibility(monkeypatch):
    case = SimpleNamespace(id=10, route="M2", status="M2_CONSULTATION_BOOKED")
    db = _DocumentDb(_secure_document(), case)
    actor = SimpleNamespace(role="lawyer", lawyer_id=7)

    async def allow(*args, **kwargs):
        return True

    monkeypatch.setattr(document_access, "lawyer_can_access_case", allow)
    document, resolved_case = asyncio.run(
        document_access.load_authorized_document(db, actor=actor, document_id=31)
    )
    assert document.id == 31
    assert resolved_case is case

    async def deny(*args, **kwargs):
        return False

    monkeypatch.setattr(document_access, "lawyer_can_access_case", deny)
    with pytest.raises(DocumentAccessError) as error:
        asyncio.run(document_access.load_authorized_document(db, actor=actor, document_id=31))
    assert error.value.reason == "lawyer_not_responsible"
