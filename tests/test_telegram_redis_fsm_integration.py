from __future__ import annotations

import os

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage


@pytest.mark.asyncio
async def test_redis_fsm_survives_storage_reconnect():
    redis_url = os.getenv("REDIS_TEST_URL")
    if not redis_url:
        pytest.skip("REDIS_TEST_URL is not configured")

    key = StorageKey(
        bot_id=214000001,
        chat_id=214000002,
        user_id=214000003,
    )
    first = RedisStorage.from_url(redis_url)
    try:
        await first.set_state(key, "consultation:date_selected")
        await first.set_data(
            key,
            {
                "consultation_date": "2026-08-10",
                "case_id": 214,
            },
        )
    finally:
        await first.close()

    second = RedisStorage.from_url(redis_url)
    try:
        assert await second.get_state(key) == "consultation:date_selected"
        assert await second.get_data(key) == {
            "consultation_date": "2026-08-10",
            "case_id": 214,
        }
        await second.set_state(key, None)
        await second.set_data(key, {})
    finally:
        await second.close()
