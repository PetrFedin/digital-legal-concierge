from __future__ import annotations

import asyncio

import uvicorn

from app.config import settings
from app.main import app
from app.service_supervisor import BackgroundService, supervise_server


def build_background_services() -> tuple[BackgroundService, ...]:
    services: list[BackgroundService] = []
    if settings.run_bot:
        from app.bot.bot import run_bot

        services.append(BackgroundService(name="telegram-bot", factory=run_bot))
    if settings.run_scheduler:
        from app.scheduler.scheduler import AppScheduler

        scheduler = AppScheduler()
        services.append(
            BackgroundService(name="scheduler", factory=scheduler.run_forever)
        )
    return tuple(services)


async def main() -> object:
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    return await supervise_server(server, build_background_services())


if __name__ == "__main__":
    asyncio.run(main())


__all__ = ["build_background_services", "main"]
