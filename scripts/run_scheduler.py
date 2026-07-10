import asyncio
from app.scheduler.scheduler import AppScheduler

if __name__ == "__main__":
    asyncio.run(AppScheduler().run_forever())
