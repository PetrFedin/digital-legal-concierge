import inspect

from app.domain.users.user_service import UserService


def test_telegram_user_creation_is_race_safe_and_keeps_outer_transaction_usable():
    source = inspect.getsource(UserService.get_or_create_from_telegram)
    assert "begin_nested" in source
    assert "except IntegrityError" in source
    assert "_get_by_telegram_id" in source
    assert "raise" in source


def test_existing_telegram_profile_is_refreshed_without_overwriting_with_empty_values():
    source = inspect.getsource(UserService.get_or_create_from_telegram)
    assert "if telegram_username:" in source
    assert "if full_name:" in source
    assert "await self.db.flush()" in source
