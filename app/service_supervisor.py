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
    all_tasks = tuple(tasks)
    for task in all_tasks:
        if not task.done():
            task.cancel()
    if all_tasks:
        await asyncio.gather(*all_tasks, return_exceptions=True)


async def _stop_server(
    server: ServerProtocol,
    server_task: asyncio.Task[object],
    *,
    shutdown_timeout_seconds: float,
) -> None:
    server.should_exit = True
    if server_task.done():
        await asyncio.gather(server_task, return_exceptions=True)
        return
    try:
        await asyncio.wait_for(
            asyncio.shield(server_task),
            timeout=shutdown_timeout_seconds,
        )
    except asyncio.TimeoutError:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
    except BaseException:
        await asyncio.gather(server_task, return_exceptions=True)


async def supervise_server(
    server: ServerProtocol,
    services: Iterable[BackgroundService],
    *,
    shutdown_timeout_seconds: float = 15.0,
) -> object:
    """Run HTTP, Telegram and scheduler services as one failure domain.

    Background services are expected to run until the HTTP server stops.
    Returning normally, being cancelled independently, or raising unexpectedly
    is therefore fatal. The supervisor then asks the server to stop, cancels and
    awaits every remaining service, and propagates an explicit process failure.
    When the HTTP server stops normally, all services are cancelled and awaited
    so polling sessions, database connections and cross-process locks close.
    """

    if shutdown_timeout_seconds <= 0:
        raise ValueError("shutdown_timeout_seconds must be positive")

    normalized_services: list[BackgroundService] = []
    service_names: set[str] = set()
    for service in services:
        name = str(service.name or "").strip()
        if not name:
            raise ValueError("background service name is required")
        if name in service_names:
            raise ValueError(f"duplicate background service name: {name}")
        service_names.add(name)
        normalized_services.append(BackgroundService(name=name, factory=service.factory))

    server_task = asyncio.create_task(server.serve(), name="http-server")
    service_tasks: dict[asyncio.Task[object], str] = {}
    try:
        for service in normalized_services:
            try:
                awaitable = service.factory()
            except Exception as error:
                raise BackgroundServiceError(
                    service.name,
                    f"failed to start with {type(error).__name__}",
                ) from error
            task = asyncio.create_task(awaitable, name=f"service:{service.name}")
            service_tasks[task] = service.name

        if not service_tasks:
            return await server_task

        watched = {server_task, *service_tasks.keys()}
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)

        if server_task in done:
            await _cancel_and_wait(service_tasks)
            if server_task.cancelled():
                raise asyncio.CancelledError
            server_error = server_task.exception()
            if server_error is not None:
                raise server_error
            return server_task.result()

        failed_task = min(
            (task for task in done if task in service_tasks),
            key=lambda task: service_tasks[task],
        )
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
