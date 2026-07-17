from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class SlidingWindowRateLimiter:
    """In-memory per-user limiter for Telegram updates.

    The limiter protects the bot from accidental rapid taps and basic flooding.
    It is intentionally process-local: after a container restart the counters reset.
    """
