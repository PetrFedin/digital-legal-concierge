from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/document-access", tags=["document-access-ui"])


@router.get("/ui", response_class=HTMLResponse)
async def document_access_ui():
    return HTMLResponse(DOCUMENT_ACCESS_HTML)


DOCUMENT_ACCESS_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Защищённые документы</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}header a{color:#fff}main{max-width:1000px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px;margin-bottom:16px}.row{display:flex;gap:10px;flex-wrap:wrap}input{border:1px solid #d1d5db;border-radius:9px;padding:10px 12px;min-width:220px}button{border:0;border-radius:9px;padding:10px 14px;color:#fff;background:#2563eb;font-weight:700;cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}table{width:100%;border-collapse:collapse;margin-top:14px}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}.muted{font-size:13px;color:#6b7280}.ok{color:#166534}.error{color:#991b1b}.badge{display:inline-block;border-radius:999px;padding:4px 8px;background:#dcfce7;color:#166534;font-size:12px}@media(max-width:760px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>🔐 Защищённые документы</b><span><a href="/lawyer/ui">Кабинет юриста</a> · <a href="/admin-ui">Админка</a></span></header>
<main>
<div class="card">
<h2>Документы дела</h2>
<p class="muted">Файл расшифровывается только на момент выдачи. Разрешение действует несколько минут, привязано к текущей сессии и погашается после первого скачивания.</p>
<div class="row"><input id="caseId" inputmode="numeric" placeholder="ID дела"><button onclick="loadDocuments()">Показать документы</button></div>
<div id="message" class="muted"></div><div id="content"></div>
</div>
</main>
<script>
let token='';
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function size(v){if(v===null||v===undefined)return '—';if(v<1024)return v+' Б';if(v<1048576)return (v/1024).toFixed(1)+' КБ';return (v/1048576).toFixed(1)+' МБ'}
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!roles.some(x=>['lawyer','admin','superadmin'].includes(x))){content.textContent='Недостаточно прав.';return}token=s.api_token;const preset=new URLSearchParams(location.search).get('case_id');if(preset){caseId.value=preset;await loadDocuments()}}
async function loadDocuments(){const id=Number(caseId.value);if(!Number.isInteger(id)||id<1){message.className='error';message.textContent='Укажите корректный ID дела';return}message.className='muted';message.textContent='Загрузка…';content.innerHTML='';try{const rows=await api('/document-access/cases/'+id+'/documents');message.textContent=rows.length?'':'В деле нет доступных проверенных документов.';content.innerHTML=rows.length?`<table><tr><th>Документ</th><th>Файл</th><th>Статус</th><th></th></tr>${rows.map(x=>`<tr><td><b>${esc(x.title)}</b><br><span class="muted">тип ${esc(x.document_type)}, версия ${esc(x.version)}</span></td><td>${esc(x.file_name)}<br><span class="muted">${esc(size(x.file_size))}</span></td><td><span class="badge">зашифрован</span><br><span class="muted">${esc(x.status)}</span></td><td><button id="download-${x.document_id}" onclick="downloadDocument(${x.document_id})">Скачать один раз</button></td></tr>`).join('')}</table>`:''}catch(e){message.className='error';message.textContent=e.message}}
async function downloadDocument(id){const button=document.getElementById('download-'+id);button.disabled=true;message.className='muted';message.textContent='Создаётся одноразовое разрешение…';try{const grant=await api('/document-access/documents/'+id+'/grant',{method:'POST',body:'{}'});message.className='ok';message.textContent='Разрешение создано. Начинается защищённое скачивание.';location.assign(grant.download_url)}catch(e){button.disabled=false;message.className='error';message.textContent=e.message}}
boot();
</script>
</body>
</html>
"""
