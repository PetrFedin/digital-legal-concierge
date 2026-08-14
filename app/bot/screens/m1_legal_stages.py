from __future__ import annotations

from aiogram import Router

# Compatibility router required by app.bot.bot. The historical v37 dispatcher
# mounted ``m1_legal_stages.router`` before the guarded M1 client screens but the
# module itself was missing, causing an ImportError before the bot could start.
#
# Keep this router intentionally mutation-free. Proof-bearing legal facts
# (claim sent, court stage/evidence, enforcement/money received) are owned by
# authenticated lawyer/admin services. Client-facing informational callbacks
# continue through the guarded m1_stages/payment/stale-view routers mounted
# later in the dispatcher.
router = Router()

__all__ = ["router"]
