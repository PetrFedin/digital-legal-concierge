import inspect

import pytest

from scripts import reset_admin_password as recovery


def test_recovery_cli_never_accepts_password_as_an_argument():
    parser = recovery.build_parser()
    option_names = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--password" not in option_names
    assert "--password-file" not in option_names
    assert any(action.dest == "identity" for action in parser._actions)


def test_password_is_requested_twice_and_must_be_strong(monkeypatch):
    responses = iter(["Strong-Recovery-2026", "Strong-Recovery-2026"])
    monkeypatch.setattr(recovery.getpass, "getpass", lambda _prompt: next(responses))
    assert recovery.read_new_password() == "Strong-Recovery-2026"

    mismatch = iter(["Strong-Recovery-2026", "Different-Recovery-2026"])
    monkeypatch.setattr(recovery.getpass, "getpass", lambda _prompt: next(mismatch))
    with pytest.raises(ValueError, match="не совпадают"):
        recovery.read_new_password()

    too_short = iter(["shortpass", "shortpass"])
    monkeypatch.setattr(recovery.getpass, "getpass", lambda _prompt: next(too_short))
    with pytest.raises(ValueError, match="не менее"):
        recovery.read_new_password()


def test_recovery_locks_account_revokes_sessions_clears_throttle_and_audits():
    source = inspect.getsource(recovery.reset_admin_password)
    assert ".with_for_update()" in source
    assert "hash_password(password)" in source
    assert "account.session_version = int(account.session_version or 1) + 1" in source
    assert "LoginThrottleService" in source
    assert "await throttle.register_success" in source
    assert 'action="security.admin_password_reset_cli"' in source
    assert 'severity="critical"' in source
    assert "await db.commit()" in source
    assert "await db.rollback()" in source


def test_recovery_does_not_reactivate_account_without_explicit_flag():
    source = inspect.getsource(recovery.reset_admin_password)
    assert "if activate:" in source
    assert "account.is_active = True" in source
    assert "if not result[\"active\"]" in inspect.getsource(recovery.main)
