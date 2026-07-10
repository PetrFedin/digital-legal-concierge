from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from app.config import settings
from app.db.session import get_db
from app.models.notification import Notification
from app.models.payment import Payment

router = APIRouter(prefix="/recovery-center", tags=["recovery-center"])

def check(token: str | None):
    if token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="bad token")
    if not settings.enable_recovery_actions:
        raise HTTPException(status_code=403, detail="recovery actions disabled")

@router.get("")
async def recovery_status():
    return {"ok": True, "enabled": settings.enable_recovery_actions, "actions": ["reset-stuck-notifications", "expire-waiting-payments"]}

@router.post("/reset-stuck-notifications")
async def reset_stuck_notifications(db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None)):
    check(x_admin_token)
    result = await db.execute(update(Notification).where(Notification.status == 'FAILED').values(status='PENDING', is_sent=False))
    await db.commit()
    return {"ok": True, "updated": result.rowcount or 0}

@router.post("/expire-waiting-payments")
async def expire_waiting_payments(db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None)):
    check(x_admin_token)
    result = await db.execute(update(Payment).where(Payment.status.in_(['PENDING','WAITING_CONFIRMATION'])).values(status='EXPIRED'))
    await db.commit()
    return {"ok": True, "updated": result.rowcount or 0}

@router.get("/ui")
async def recovery_ui():
    return """
    <html><head><meta charset='utf-8'><title>Recovery Center</title>
    <style>body{font-family:Arial;margin:30px;background:#f7f7f7}.card{background:#fff;padding:18px;border-radius:14px;margin:12px 0;box-shadow:0 1px 8px #ddd}code{background:#eee;padding:3px}</style></head>
    <body><h1>Recovery Center v20</h1><div class='card'>Безопасные восстановительные действия. Для POST нужен заголовок <code>x-admin-token</code>.</div>
    <div class='card'><b>Действия:</b><br>POST /recovery-center/reset-stuck-notifications<br>POST /recovery-center/expire-waiting-payments</div>
    <a href='/health-center/ui'>Health Center</a> <a href='/diagnostic-center/ui'>Diagnostic Center</a> <a href='/operator'>Operator</a></body></html>
    """
