from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.config import settings

router = APIRouter(tags=["backup-center"])


def _backup_status():
    backups_dir = Path('backups')
    backups_dir.mkdir(exist_ok=True)
    files = sorted(backups_dir.glob('*'), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return {
        'backups_dir_exists': backups_dir.exists(),
        'files_count': len([p for p in files if p.is_file()]),
        'last_files': [p.name for p in files[:10] if p.is_file()],
        'database_url_configured': bool(settings.database_url),
        'storage_dir': settings.storage_dir,
        'storage_exists': Path(settings.storage_dir).exists(),
    }


@router.get('/backup-center/ui', response_class=HTMLResponse)
async def backup_center_ui():
    st = _backup_status()
    last = ''.join(f'<li>{name}</li>' for name in st['last_files']) or '<li>Резервных копий пока нет</li>'
    return HTMLResponse(f"""
    <!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Backup Center v23</title><style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f6f7fb;color:#111827}}
    header{{background:#111827;color:white;padding:22px}} main{{max-width:1000px;margin:auto;padding:22px;display:grid;gap:16px}}
    .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}} .card{{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
    .ok{{color:#16a34a;font-weight:700}} .warn{{color:#ca8a04;font-weight:700}} a.button{{display:inline-block;padding:10px 14px;border-radius:10px;background:#2563eb;color:#fff;text-decoration:none;font-weight:700;margin:4px 4px 4px 0}} code,pre{{background:#0b1020;color:#d1e7ff;border-radius:10px;padding:10px;display:block;overflow:auto}}
    @media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
    </style></head><body>
    <header><h1>💾 Backup Center v23</h1><p>Контроль резервных копий базы, файлов документов и восстановления.</p></header>
    <main>
      <section class='grid'>
        <div class='card'><b>Папка backups</b><p class='{ 'ok' if st['backups_dir_exists'] else 'warn'}'>{'есть' if st['backups_dir_exists'] else 'нет'}</p></div>
        <div class='card'><b>Хранилище документов</b><p>{st['storage_dir']}</p><p class='{ 'ok' if st['storage_exists'] else 'warn'}'>{'есть' if st['storage_exists'] else 'нет'}</p></div>
        <div class='card'><b>Кол-во backup-файлов</b><p>{st['files_count']}</p></div>
      </section>
      <section class='card'><h2>Последние резервные копии</h2><ul>{last}</ul></section>
      <section class='card'><h2>Команды</h2><pre>./backup.sh\n./restore.sh</pre><p>Для production лучше вынести backup в cron/systemd timer и хранить копии отдельно от сервера.</p></section>
      <section class='card'><a class='button' href='/operator'>Операторская</a><a class='button' href='/health-center/ui'>Health Center</a><a class='button' href='/diagnostic-center/ui'>Diagnostic Center</a></section>
    </main></body></html>
    """)


@router.get('/backup-center/status')
async def backup_center_status():
    st = _backup_status(); st['version']='1.0.0-v23'; return st
