from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.login_security_state import LoginSecurityState
from app.security.keyring import active_hmac_digest, hmac_candidates


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
    def _message(scope: str, value: str) -> bytes:
        normalized = str(value or "unknown").strip().lower()
        return f"login-throttle:{scope}:{normalized}".encode("utf-8")

    @classmethod
    def _active_key_hash(cls, scope: str, value: str) -> str:
        _, digest = active_hmac_digest(cls._message(scope, value))
        return digest

    @classmethod
    def _key_hashes(cls, scope: str, value: str) -> list[str]:
        return [digest for _, digest in hmac_candidates(cls._message(scope, value))]

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
        hashes = {
            digest
            for policy in policies
            for digest in self._key_hashes(policy.scope, policy.value)
        }
        rows = (
            await self.db.execute(
                select(LoginSecurityState)
                .where(LoginSecurityState.key_hash.in_(hashes))
                .with_for_update()
            )
        ).scalars().all()
        return {row.key_hash: row for row in rows}

    @classmethod
    def _policy_states(
        cls,
        states: dict[str, LoginSecurityState],
        policy: ThrottlePolicy,
    ) -> list[LoginSecurityState]:
        return [
            states[digest]
            for digest in cls._key_hashes(policy.scope, policy.value)
            if digest in states
        ]

    async def check(self, *, principal: str, client_address: str) -> None:
        now = datetime.now(timezone.utc)
        policies = self.policies(principal, client_address)
        states = await self._locked_states(policies)
        retry_after = 0
        for policy in policies:
            for state in self._policy_states(states, policy):
                locked_until = self._utc(state.locked_until)
                if locked_until and locked_until > now:
                    retry_after = max(
                        retry_after,
                        int((locked_until - now).total_seconds()),
                    )
        if retry_after:
            raise LoginRateLimitError(retry_after)

    def _seed_active_state(
        self,
        policy: ThrottlePolicy,
        states: dict[str, LoginSecurityState],
        now: datetime,
    ) -> LoginSecurityState:
        active_hash = self._active_key_hash(policy.scope, policy.value)
        state = states.get(active_hash)
        candidates = self._policy_states(states, policy)
        if state is None:
            state = LoginSecurityState(key_hash=active_hash)
            self.db.add(state)
            states[active_hash] = state

        recent = [
            candidate
            for candidate in candidates
            if self._utc(candidate.window_started_at)
            and now - self._utc(candidate.window_started_at) <= self.WINDOW
        ]
        if recent:
            state.failed_attempts = max(
                int(state.failed_attempts or 0),
                max(int(candidate.failed_attempts or 0) for candidate in recent),
            )
            newest = max(
                recent,
                key=lambda candidate: self._utc(candidate.last_attempt_at)
                or datetime.min.replace(tzinfo=timezone.utc),
            )
            if not state.window_started_at:
                state.window_started_at = newest.window_started_at
            latest_lock = max(
                (
                    self._utc(candidate.locked_until)
                    for candidate in recent
                    if candidate.locked_until
                ),
                default=None,
            )
            if latest_lock and (
                not state.locked_until
                or latest_lock > self._utc(state.locked_until)
            ):
                state.locked_until = latest_lock
        return state

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
            state = self._seed_active_state(policy, states, now)
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
        hashes = {
            digest
            for policy in policies
            for digest in self._key_hashes(policy.scope, policy.value)
        }
        await self.db.execute(
            delete(LoginSecurityState).where(LoginSecurityState.key_hash.in_(hashes))
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
