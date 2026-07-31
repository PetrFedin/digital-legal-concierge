from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Protocol


class ServerProtocol(Protocol):
    should_exit: bool

    async def serve(self) -> object: ...


class BackgroundServiceError(RuntimeError):
    def __init__(self, service_name: str, message: str):
        super().__init__(f"Background service {service_name}: {message}")
        self.service_name = service_name


@dataclass(frozen=True)
class BackgroundService:
    name: str
    factory: Callable[[], Awaitable[object]]


async def _cancel_and_wait(tasks: Iterable[asyncio.Task[object]]) -> None:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _stop_server(
    server: ServerProtocol,
    server_task: asyncio.Task[object],
    *,
    shutdown_timeout_seconds: float,
) -> None:
    server.should_exit = True
    if server_task.done():
        return
    try:
        await asyncio.wait_for(
            asyncio.shield(server_task),
            timeout=shutdown_timeout_seconds,
        )
    except asyncio.TimeoutError:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)


async def supervise_server(
    server: ServerProtocol,
    services: Iterable[BackgroundService],
    *,
    shutdown_timeout_seconds: float = 15.0,
) -> object:
    """Run HTTP and background services as one failure domain.

    A bot or scheduler task is expected to run until the HTTP server stops.
    Returning normally or raising unexpectedly is therefore fatal: the server
    is asked to shut down and the process receives an explicit exception.
    When Uvicorn stops normally, all background tasks are cancelled and awaited
    so database sessions, Telegram polling and file locks can close cleanly.
    """

    if shutdown_timeout_seconds <= 0:
        raise ValueError("shutdown_timeout_seconds must be positive")

    server_task = asyncio.create_task(server.serve(), name="http-server")
    service_tasks: dict[asyncio.Task[object], str] = {}
    try:
        for service in services:
            name = str(service.name or "").strip()
            if not name:
                raise ValueError("background service name is required")
            if name in service_tasks.values():
                raise ValueError(f"duplicate background service name: {name}")
            task = asyncio.create_task(service.factory(), name=f"service:{name}")
            service_tasks[task] = name

        if not service_tasks:
            return await server_task

        watched = {server_task, *service_tasks.keys()}
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)

        if server_task in done:
            server_error = server_task.exception()
            await _cancel_and_wait(service_tasks)
            if server_error is not None:
                raise server_error
            return server_task.result()

        failed_task = next(task for task in done if task in service_tasks)
        service_name = service_tasks[failed_task]
        if failed_task.cancelled():
            failure = BackgroundServiceError(
                service_name,
                "was cancelled while the HTTP server was still running",
            )
        else:
            service_error = failed_task.exception()
            if service_error is None:
                failure = BackgroundServiceError(
                    service_name,
                    "exited unexpectedly while the HTTP server was still running",
                )
            else:
                failure = BackgroundServiceError(
                    service_name,
                    f"failed with {type(service_error).__name__}",
                )
                failure.__cause__ = service_error

        await _stop_server(
            server,
            server_task,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        await _cancel_and_wait(service_tasks)
        raise failure
    except asyncio.CancelledError:
        await _stop_server(
            server,
            server_task,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        await _cancel_and_wait(service_tasks)
        raise
    except Exception:
        await _stop_server(
            server,
            server_task,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
        )
        await _cancel_and_wait(service_tasks)
        raise
    finally:
        if not server_task.done():
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
        await _cancel_and_wait(service_tasks)


__all__ = [
    "BackgroundService",
    "BackgroundServiceError",
    "ServerProtocol",
    "supervise_server",
]
