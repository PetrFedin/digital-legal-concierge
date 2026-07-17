from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class SlidingWindowRateLimiter:
    """Process-local per-user limiter for Telegram updates."""

    def __init__(self, *, max_events: int = 8, window_seconds: int = 10):
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if window_seconds < 1:
            raise ValueError("window_seconds must be positive")
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def check(self, user_id: int, now: float | None = None) -> RateLimitDecision:
        current = monotonic() if now is None else now
        events = self._events[user_id]
        cutoff = current - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.max_events:
            retry_after = max(1, int(self.window_seconds - (current - events[0]) + 0.999))
            return RateLimitDecision(False, retry_after)
        events.append(current)
        return RateLimitDecision(True, 0)

    def reset(self, user_id: int) -> None:
        self._events.pop(user_id, None)
