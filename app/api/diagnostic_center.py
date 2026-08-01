from pathlib import Path
import os
import sys
import shutil
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.config import settings

router = APIRouter(prefix="/diagnostic-center", tags=["diagnostic-center"])

@router.get("")
async def diagnostic_center():
    storage = Path(settings.storage_dir)
    backups = Path(settings.backup_dir)
    env_exists = Path('.env').exists()
    disk = shutil.disk_usage('.')
    return {
        "version": "1.0.0-v20",
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "env_exists": env_exists,
        "database_url": settings.database_url,
        "run_bot": settings.run_bot,
        "run_scheduler": settings.run_scheduler,
        "payment_provider": settings.payment_provider,
        "demo_mode": settings.demo_mode,
        "storage_exists": storage.exists(),
        "storage_files": len(list(storage.rglob('*'))) if storage.exists() else 0,
        "backups_exists": backups.exists(),
        "backup_files": len(list(backups.glob('*'))) if backups.exists() else 0,
        "free_disk_mb": int(disk.free/1024/1024),
        "pages": ["/health-center/ui", "/recovery-center/ui", "/launch-assistant", "/operator", "/admin-ui"],
    }

@router.get("/ui", response_class=HTMLResponse)
async def diagnostic_center_ui():
    return """
    <html><head><meta charset='utf-8'><title>Diagnostic Center</title>
    <style>body{font-family:Arial;margin:30px;background:#f7f7f7}.card{background:#fff;padding:18px;border-radius:14px;margin:12px 0;box-shadow:0 1px 8px #ddd}pre{white-space:pre-wrap}</style></head>
    <body><h1>Diagnostic Center v20</h1><div class='card'>Единая диагностика перед запуском и при проблемах.</div><div id='out' class='card'>Загрузка...</div>
    <script>fetch('/diagnostic-center').then(r=>r.json()).then(d=>{document.getElementById('out').innerHTML='<pre>'+JSON.stringify(d,null,2)+'</pre>'})</script></body></html>
    """
