from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.calculator_draft import (
    CALCULATOR_CASE_ID,
    CALCULATOR_DRAFTS_BY_CASE,
    activate_calculator_case_draft,
    finish_calculator_case,
    start_fresh_calculator_case,
)
from app.bot.states import CalculatorStates


_REDIS_URL = os.getenv("TEST_REDIS_URL") or os.getenv("REDIS_URL")
pytestmark = pytest.mark.skipif(
    not _REDIS_URL,
    reason="Redis runtime contract requires TEST_REDIS_URL or REDIS_URL",
)


def _storage_key() -> StorageKey:
    probe = uuid.uuid4().int % 1_000_000_000
    return StorageKey(
        bot_id=9_900_000_000 + probe,
        chat_id=9_800_000_000 + probe,
        user_id=9_700_000_000 + probe,
    )


def test_two_unfinished_calculator_cases_survive_redis_storage_restart() -> None:
    async def scenario() -> None:
        assert _REDIS_URL is not None
        key = _storage_key()
        storage = RedisStorage.from_url(_REDIS_URL)
        restarted_storage: RedisStorage | None = None
        try:
            state = FSMContext(storage=storage, key=key)

            # Case A starts first and receives one answer.
            await state.set_data(
                {
                    CALCULATOR_CASE_ID: 301,
                    "contract_price": "8100000",
                }
            )
            await state.set_state(CalculatorStates.waiting_planned_transfer_date)

            # Starting Case B must snapshot A rather than erase it.
            await start_fresh_calculator_case(state, case_id=302)
            await state.update_data(contract_price="9200000")
            await state.set_state(CalculatorStates.waiting_planned_transfer_date)

            before_restart = await state.get_data()
            assert int(before_restart[CALCULATOR_CASE_ID]) == 302
            assert before_restart["contract_price"] == "9200000"
            assert (
                before_restart[CALCULATOR_DRAFTS_BY_CASE]["301"]["contract_price"]
                == "8100000"
            )

            # Close the storage client to simulate bot process death while Redis
            # itself remains authoritative for FSM state.
            await storage.close()

            restarted_storage = RedisStorage.from_url(_REDIS_URL)
            restarted_state = FSMContext(storage=restarted_storage, key=key)
            persisted_state = await restarted_state.get_state()
            persisted_data = await restarted_state.get_data()
            assert persisted_state == CalculatorStates.waiting_planned_transfer_date.state
            assert int(persisted_data[CALCULATOR_CASE_ID]) == 302
            assert persisted_data["contract_price"] == "9200000"
            assert (
                persisted_data[CALCULATOR_DRAFTS_BY_CASE]["301"]["contract_price"]
                == "8100000"
            )

            # Recover A after restart. B is snapshotted before the switch.
            restored_a = await activate_calculator_case_draft(
                restarted_state,
                case_id=301,
            )
            assert int(restored_a[CALCULATOR_CASE_ID]) == 301
            assert restored_a["contract_price"] == "8100000"
            assert (
                restored_a[CALCULATOR_DRAFTS_BY_CASE]["302"]["contract_price"]
                == "9200000"
            )

            # B must still restore its own answer, not A's.
            restored_b = await activate_calculator_case_draft(
                restarted_state,
                case_id=302,
            )
            assert int(restored_b[CALCULATOR_CASE_ID]) == 302
            assert restored_b["contract_price"] == "9200000"
            assert (
                restored_b[CALCULATOR_DRAFTS_BY_CASE]["301"]["contract_price"]
                == "8100000"
            )

            # Completing B removes only B. A remains recoverable from Redis.
            await finish_calculator_case(restarted_state, case_id=302)
            after_finish = await restarted_state.get_data()
            assert CALCULATOR_CASE_ID not in after_finish
            assert "302" not in after_finish[CALCULATOR_DRAFTS_BY_CASE]
            assert (
                after_finish[CALCULATOR_DRAFTS_BY_CASE]["301"]["contract_price"]
                == "8100000"
            )

            restored_a_again = await activate_calculator_case_draft(
                restarted_state,
                case_id=301,
            )
            assert int(restored_a_again[CALCULATOR_CASE_ID]) == 301
            assert restored_a_again["contract_price"] == "8100000"

            await restarted_state.clear()
        finally:
            if restarted_storage is not None:
                await restarted_storage.close()
            else:
                # The first close is idempotent for normal aiogram RedisStorage;
                # this also handles failures before the simulated restart point.
                await storage.close()

    asyncio.run(scenario())
