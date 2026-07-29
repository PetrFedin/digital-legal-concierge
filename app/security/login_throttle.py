from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.login_security_state import LoginSecurityState


@dataclass(frozen=True)
class ThrottlePolicy:
    scope: str
    value: str
    max_attempts: int


class LoginRateLimitError(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__("Слишком много попыток входа")
        self.retry_after = max(1, int(retry_after))


class LoginThrottleService:
    WINDOW = timedelta(minutes=10)
    LOCK_DURATION = timedelta(minutes=15)
    RETENTION = timedelta(days=2)

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _key_hash(scope: str, value: str) -> str:
        normalized = str(value or "unknown").strip().lower()
        message = f"login-throttle:{scope}:{normalized}".encode("utf-8")
        return hmac.new(
            settings.admin_api_token.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def policies(cls, principal: str, client_address: str) -> list[ThrottlePolicy]:
        normalized_principal = str(principal or "").strip().lower() or "unknown"
        normalized_address = str(client_address or "unknown").strip().lower()
        return [
            ThrottlePolicy("principal", normalized_principal, 5),
            ThrottlePolicy(
                "principal_address",
                f"{normalized_principal}|{normalized_address}",
                5,
            ),
            ThrottlePolicy("address", normalized_address, 30),
        ]

    async def _locked_states(
        self,
        policies: list[ThrottlePolicy],
    ) -> dict[str, LoginSecurityState]:
        hashes = [self._key_hash(policy.scope, policy.value) for policy in policies]
        rows = (
            await self.db.execute(
                select(LoginSecurityState)
                .where(LoginSecurityState.key_hash.in_(hashes))
                .with_for_update()
            )
        ).scalars().all()
        return {row.key_hash: row for row in rows}

    async def check(self, *, principal: str, client_address: str) -> None:
        now = datetime.now(timezone.utc)
        policies = self.policies(principal, client_address)
        states = await self._locked_states(policies)
        retry_after = 0
        for policy in policies:
            state = states.get(self._key_hash(policy.scope, policy.value))
            locked_until = self._utc(state.locked_until) if state else None
            if locked_until and locked_until > now:
                retry_after = max(
                    retry_after,
                    int((locked_until - now).total_seconds()),
                )
        if retry_after:
            raise LoginRateLimitError(retry_after)

    async def register_failure(
        self,
        *,
        principal: str,
        client_address: str,
    ) -> int:
        now = datetime.now(timezone.utc)
        policies = self.policies(principal, client_address)
        states = await self._locked_states(policies)
        longest_lock = 0
        for policy in policies:
            key_hash = self._key_hash(policy.scope, policy.value)
            state = states.get(key_hash)
            if not state:
                state = LoginSecurityState(key_hash=key_hash)
                self.db.add(state)
                states[key_hash] = state
            window_started = self._utc(state.window_started_at)
            if not window_started or now - window_started > self.WINDOW:
                state.failed_attempts = 0
                state.window_started_at = now
                state.locked_until = None
            state.failed_attempts = int(state.failed_attempts or 0) + 1
            state.last_attempt_at = now
            if state.failed_attempts >= policy.max_attempts:
                state.locked_until = now + self.LOCK_DURATION
                longest_lock = max(
                    longest_lock,
                    int(self.LOCK_DURATION.total_seconds()),
                )
        await self.db.flush()
        return longest_lock

    async def register_success(
        self,
        *,
        principal: str,
        client_address: str,
    ) -> None:
        policies = self.policies(principal, client_address)[:2]
        hashes = [self._key_hash(policy.scope, policy.value) for policy in policies]
        await self.db.execute(
            delete(LoginSecurityState).where(
                LoginSecurityState.key_hash.in_(hashes)
            )
        )
        await self.db.flush()

    async def cleanup(self) -> int:
        cutoff = datetime.now(timezone.utc) - self.RETENTION
        result = await self.db.execute(
            delete(LoginSecurityState).where(
                LoginSecurityState.last_attempt_at.is_not(None),
                LoginSecurityState.last_attempt_at < cutoff,
            )
        )
        return int(result.rowcount or 0)
