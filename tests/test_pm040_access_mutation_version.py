from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from app.api.access_management import (
    _require_expected_account_version,
    reset_user_mfa,
    update_user,
    user_snapshot,
)
from app.models.admin_user import AdminUser


def _user(version: int = 3) -> AdminUser:
    return AdminUser(
        id=77,
        full_name="Access Version Test",
        username="access-version-test",
        email="access-version-test@example.test",
        password_hash="unused",
        role="admin",
        is_active=True,
        session_version=4,
        account_version=version,
    )


def test_account_version_is_exposed_separately_from_session_version() -> None:
    payload = user_snapshot(_user(3))
    assert payload["session_version"] == 4
    assert payload["account_version"] == 3


def test_expected_account_version_fails_closed_for_missing_or_stale_snapshot() -> None:
    user = _user(5)
    assert _require_expected_account_version(
        user,
        {"expected_account_version": 5},
    ) == 5

    for payload in ({}, {"expected_account_version": 4}):
        with pytest.raises(HTTPException) as error:
            _require_expected_account_version(user, payload)
        assert error.value.status_code == 409


def test_access_mutations_check_snapshot_under_lock_and_advance_version() -> None:
    update = inspect.getsource(update_user)
    reset = inspect.getsource(reset_user_mfa)

    assert update.index("with_for_update()") < update.index(
        "_require_expected_account_version(user, payload)"
    )
    assert reset.index("with_for_update()") < reset.index(
        "_require_expected_account_version(user, payload)"
    )
    assert "user.account_version = int(user.account_version or 1) + 1" in update
    assert "user.account_version = int(user.account_version or 1) + 1" in reset
    assert '"account_version": int(user.account_version or 1)' in update
    assert '"account_version": int(user.account_version or 1)' in reset
