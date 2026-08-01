from __future__ import annotations

import inspect
import re

from app.api.document_access import create_document_download_grant
from app.api.document_access_portal import DOCUMENT_ACCESS_HTML
from app.security.document_access import (
    consume_document_grant,
    issue_document_grant,
    resolve_document_actor,
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _function(name: str) -> str:
    marker = f"async function {name}("
    start = DOCUMENT_ACCESS_HTML.index(marker)
    end = DOCUMENT_ACCESS_HTML.find("\nasync function ", start + 1)
    if end < 0:
        end = DOCUMENT_ACCESS_HTML.index("\nboot();", start)
    return DOCUMENT_ACCESS_HTML[start:end]


def test_document_download_is_single_flight_per_document():
    compact = _compact(DOCUMENT_ACCESS_HTML)

    assert "constpendingDocuments=newSet()" in compact
    assert "asyncfunctionwithDocumentGrant(id,button,work)" in compact
    assert "pendingDocuments.has(id)" in compact
    assert "pendingDocuments.add(id)" in compact
    assert "pendingDocuments.delete(id)" in compact
    assert "finally" in compact
    assert ".disabled=true" in compact
    assert ".disabled=false" in compact
    assert "data-document-id=" in compact
    assert "downloadDocument(${x.document_id},this)" in compact


def test_document_download_checks_both_grant_and_binary_responses():
    function = _function("downloadDocument")
    compact = _compact(function)

    assert function.index("await api(") < function.index("await fetch(")
    assert "method:'POST'" in function
    assert "credentials:'same-origin'" in function
    assert "cache:'no-store'" in function
    assert "if(!response.ok)" in compact
    assert "responseError(response" in function
    assert "awaitresponse.blob()" in compact
    assert "URL.createObjectURL(blob)" in function
    assert "URL.revokeObjectURL(objectUrl)" in function
    assert "link.download=safeFileName(grant.file_name,id)" in compact
    assert "Документ не скачан" in function
    assert "передан браузеру для сохранения" in function


def test_document_portal_exposes_accessible_status_and_validates_case_id():
    compact = _compact(DOCUMENT_ACCESS_HTML)

    assert 'role="status"' in DOCUMENT_ACCESS_HTML
    assert 'aria-live="polite"' in DOCUMENT_ACCESS_HTML
    assert "Number.isInteger(id)" in DOCUMENT_ACCESS_HTML
    assert "id<1" in compact
    assert "loadingDocuments" in DOCUMENT_ACCESS_HTML
    assert "try{awaitloadDocuments()" not in compact
    assert "if(!r.ok)throw" in compact


def test_document_file_name_is_sanitized_before_browser_save():
    compact = _compact(DOCUMENT_ACCESS_HTML)

    assert "functionsafeFileName(value,id)" in compact
    assert "replace(/[\\\\/:*?\"<>|\\r\\n]/g,'_')" in compact
    assert "grant.file_name" in DOCUMENT_ACCESS_HTML


def test_grant_issuance_is_serialized_before_active_limit_is_checked():
    source = inspect.getsource(issue_document_grant)
    account_lock = source.index("with_for_update")
    active_query = source.index("select(DocumentAccessGrant)")

    assert account_lock < active_query
    assert "AdminUser.id == actor.account_id" in source
    assert "DocumentAccessGrant.actor_account_id == actor.account_id" in source
    assert "DocumentAccessGrant.used_at.is_(None)" in source
    assert "DocumentAccessGrant.revoked_at.is_(None)" in source
    assert "DocumentAccessGrant.expires_at > now" in source
    assert "stale.revoked_at = now" in source


def test_grant_is_bound_to_personal_current_session():
    source = inspect.getsource(resolve_document_actor)
    compact = _compact(source)

    assert "payload.get(\"legacy\")" in source
    assert "set(current_roles)!=set(token_roles)" in compact
    assert "payload.get(\"sv\")" in source
    assert "payload.get(\"jti\")" in source
    assert "mfa_required" in source


def test_grant_consumption_is_atomic_one_time_and_secret_bound():
    source = inspect.getsource(consume_document_grant)
    compact = _compact(source)

    assert "actor_mismatch" in source
    assert "session_mismatch" in source
    assert "session_version_mismatch" in source
    assert "grant_revoked" in source
    assert "grant_reused" in source
    assert "grant_expired" in source
    assert "grant_secret_missing" in source
    assert "grant_secret_invalid" in source
    assert "hmac.compare_digest" in source
    assert "update(DocumentAccessGrant)" in source
    assert "DocumentAccessGrant.used_at.is_(None)" in source
    assert "DocumentAccessGrant.revoked_at.is_(None)" in source
    assert "DocumentAccessGrant.expires_at>now" in compact
    assert "claimed.rowcount!=1" in compact
    assert "grant_race_lost" in source


def test_grant_response_is_no_store_secure_cookie_and_has_file_name():
    source = inspect.getsource(create_document_download_grant)

    assert '"file_name": document.file_name' in source
    assert '"one_time": True' in source
    assert 'response.headers["Cache-Control"] = "no-store"' in source
    assert "httponly=True" in source
    assert 'samesite="strict"' in source
    assert 'secure=settings.app_env == "production"' in source
    assert "path=download_path" in source
    assert "except Exception" in source
    assert source.count("await db.rollback()") >= 2
