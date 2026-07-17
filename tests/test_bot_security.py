import pytest

from app.bot.security import SlidingWindowRateLimiter


def test_allows_events_within_limit():
    limiter = SlidingWindowRateLimiter(max_events=3, window_seconds=10)
    assert limiter.check(42, now=0).allowed
    assert limiter.check(42, now=1).allowed
    assert limiter.check(42, now=2).allowed


def test_blocks_event_over_limit_and_returns_retry_after():
    limiter = SlidingWindowRateLimiter(max_events=2, window_seconds=10)
    assert limiter.check(42, now=0).allowed
    assert limiter.check(42, now=1).allowed
    blocked = limiter.check(42, now=2)
    assert not blocked.allowed
    assert blocked.retry_after_seconds == 8


def test_window_expires_and_user_is_allowed_again():
    limiter = SlidingWindowRateLimiter(max_events=2, window_seconds=10)
    limiter.check(42, now=0)
    limiter.check(42, now=1)
    assert not limiter.check(42, now=2).allowed
    assert limiter.check(42, now=10.1).allowed


def test_users_are_limited_independently():
    limiter = SlidingWindowRateLimiter(max_events=1, window_seconds=10)
    assert limiter.check(1, now=0).allowed
    assert not limiter.check(1, now=1).allowed
    assert limiter.check(2, now=1).allowed


def test_reset_clears_user_history():
    limiter = SlidingWindowRateLimiter(max_events=1, window_seconds=10)
    limiter.check(7, now=0)
    assert not limiter.check(7, now=1).allowed
    limiter.reset(7)
    assert limiter.check(7, now=1).allowed


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(max_events=0)
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(window_seconds=0)
