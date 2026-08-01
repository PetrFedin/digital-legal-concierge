from __future__ import annotations

import asyncio

import pytest

from app.service_supervisor import (
    BackgroundService,
    BackgroundServiceError,
    supervise_server,
)


class FakeServer:
    def __init__(self, *, stop_on_exit_flag: bool = True):
        self.should_exit = False
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.stop_on_exit_flag = stop_on_exit_flag

    async def serve(self) -> str:
        self.started.set()
        try:
            while not self.should_exit or not self.stop_on_exit_flag:
                await asyncio.sleep(0.001)
            return "server-stopped"
        finally:
            self.finished.set()


async def _wait_until(predicate, timeout: float = 0.5) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached")
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_server_exit_cancels_and_awaits_background_services():
    server = FakeServer()
    service_started = asyncio.Event()
    service_finished = asyncio.Event()

    async def service() -> None:
        service_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            service_finished.set()

    task = asyncio.create_task(
        supervise_server(server, [BackgroundService("worker", service)])
    )
    await server.started.wait()
    await service_started.wait()
    server.should_exit = True

    assert await task == "server-stopped"
    assert service_finished.is_set()


@pytest.mark.asyncio
async def test_background_exception_stops_server_and_raises_named_error():
    server = FakeServer()
    release = asyncio.Event()

    async def failing_service() -> None:
        await release.wait()
        raise LookupError("boom")

    task = asyncio.create_task(
        supervise_server(server, [BackgroundService("telegram-bot", failing_service)])
    )
    await server.started.wait()
    release.set()

    with pytest.raises(BackgroundServiceError) as caught:
        await task

    assert caught.value.service_name == "telegram-bot"
    assert isinstance(caught.value.__cause__, LookupError)
    assert server.should_exit is True
    assert server.finished.is_set()


@pytest.mark.asyncio
async def test_background_normal_return_is_fatal():
    server = FakeServer()

    async def short_service() -> None:
        return None

    with pytest.raises(BackgroundServiceError, match="exited unexpectedly"):
        await supervise_server(
            server,
            [BackgroundService("scheduler", short_service)],
        )

    assert server.should_exit is True
    assert server.finished.is_set()


@pytest.mark.asyncio
async def test_one_failure_cancels_and_awaits_other_services():
    server = FakeServer()
    other_started = asyncio.Event()
    other_finished = asyncio.Event()

    async def failing_service() -> None:
        await other_started.wait()
        raise RuntimeError("failure")

    async def other_service() -> None:
        other_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            other_finished.set()

    with pytest.raises(BackgroundServiceError):
        await supervise_server(
            server,
            [
                BackgroundService("a-failing", failing_service),
                BackgroundService("b-other", other_service),
            ],
        )

    assert other_finished.is_set()


@pytest.mark.asyncio
async def test_duplicate_or_empty_service_names_are_rejected_before_server_start():
    server = FakeServer()

    async def service() -> None:
        await asyncio.Event().wait()

    with pytest.raises(ValueError, match="name is required"):
        await supervise_server(server, [BackgroundService("  ", service)])
    assert not server.started.is_set()

    with pytest.raises(ValueError, match="duplicate"):
        await supervise_server(
            server,
            [BackgroundService("worker", service), BackgroundService("worker", service)],
        )
    assert not server.started.is_set()


@pytest.mark.asyncio
async def test_synchronous_factory_failure_stops_server():
    server = FakeServer()

    def broken_factory():
        raise OSError("cannot initialize")

    with pytest.raises(BackgroundServiceError) as caught:
        await supervise_server(
            server,
            [BackgroundService("telegram-bot", broken_factory)],
        )

    assert caught.value.service_name == "telegram-bot"
    assert isinstance(caught.value.__cause__, OSError)
    assert server.should_exit is True


@pytest.mark.asyncio
async def test_shutdown_timeout_cancels_stuck_server():
    server = FakeServer(stop_on_exit_flag=False)

    async def short_service() -> None:
        return None

    with pytest.raises(BackgroundServiceError):
        await supervise_server(
            server,
            [BackgroundService("scheduler", short_service)],
            shutdown_timeout_seconds=0.01,
        )

    assert server.should_exit is True
    await _wait_until(server.finished.is_set)


@pytest.mark.asyncio
async def test_cancelling_supervisor_stops_server_and_services():
    server = FakeServer()
    service_started = asyncio.Event()
    service_finished = asyncio.Event()

    async def service() -> None:
        service_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            service_finished.set()

    task = asyncio.create_task(
        supervise_server(server, [BackgroundService("worker", service)])
    )
    await server.started.wait()
    await service_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert server.should_exit is True
    assert server.finished.is_set()
    assert service_finished.is_set()
