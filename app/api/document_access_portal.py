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
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between;align-items:center}header a{color:#fff}main{max-width:1000px;margin:auto;padding:24px}.card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:20px;margin-bottom:16px}.row{display:flex;gap:10px;flex-wrap:wrap}input{border:1px solid #d1d5db;border-radius:9px;padding:10px 12px;min-width:220px}button{border:0;border-radius:9px;padding:10px 14px;color:#fff;background:#2563eb;font-weight:700;cursor:pointer}button:disabled{opacity:.5;cursor:wait}table{width:100%;border-collapse:collapse;margin-top:14px}th,td{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}.muted{font-size:13px;color:#6b7280}.ok{color:#166534}.error{color:#991b1b}.warn{color:#a16207}.badge{display:inline-block;border-radius:999px;padding:4px 8px;background:#dcfce7;color:#166534;font-size:12px}@media(max-width:760px){table{display:block;overflow-x:auto;font-size:12px}}
</style>
</head>
<body>
<header><b>🔐 Защищённые документы</b><span><a href="/lawyer/ui">Кабинет юриста</a> · <a href="/admin-ui">Админка</a></span></header>
<main>
<div class="card">
<h2>Документы дела</h2>
<p class="muted">Файл расшифровывается только на момент выдачи. Разрешение действует несколько минут, привязано к текущей сессии и погашается после первого скачивания.</p>
<div class="row"><input id="caseId" inputmode="numeric" placeholder="ID дела"><button id="loadButton" onclick="loadDocuments(this)">Показать документы</button></div>
<div id="message" class="muted" role="status" aria-live="polite"></div><div id="content"></div>
</div>
</main>
<script>
let token='';let loadingDocuments=false;const pendingDocuments=new Set();
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function size(v){if(v===null||v===undefined)return '—';if(v<1024)return v+' Б';if(v<1048576)return (v/1024).toFixed(1)+' КБ';return (v/1048576).toFixed(1)+' МБ'}
function feedback(text,state='muted'){message.className=state;message.textContent=text}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
async function responseError(response,fallback){const data=await response.json().catch(()=>({}));return new Error(data.detail||fallback)}
function safeFileName(value,id){const name=String(value||('document-'+id)).replace(/[\\/:*?"<>|\r\n]/g,'_').trim();return name||('document-'+id)}
async function boot(){const r=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!r.ok){location.href='/login';return}const s=await r.json();const roles=s.roles||[s.role];if(!roles.some(x=>['lawyer','admin','superadmin'].includes(x))){content.textContent='Недостаточно прав.';return}token=s.api_token;const preset=new URLSearchParams(location.search).get('case_id');if(preset){caseId.value=preset;await loadDocuments()}}
async function loadDocuments(button=null){if(loadingDocuments)return;const id=Number(caseId.value);if(!Number.isInteger(id)||id<1){feedback('Укажите корректный ID дела','error');return}loadingDocuments=true;const target=button||document.getElementById('loadButton');const label=target?target.textContent:'';if(target){target.disabled=true;target.setAttribute('aria-busy','true');target.textContent='Загрузка…'}feedback('Загрузка…');content.innerHTML='';try{const rows=await api('/document-access/cases/'+id+'/documents');feedback(rows.length?'':'В деле нет доступных проверенных документов.');content.innerHTML=rows.length?`<table><tr><th>Документ</th><th>Файл</th><th>Статус</th><th></th></tr>${rows.map(x=>`<tr><td><b>${esc(x.title)}</b><br><span class="muted">тип ${esc(x.document_type)}, версия ${esc(x.version)}</span></td><td>${esc(x.file_name)}<br><span class="muted">${esc(size(x.file_size))}</span></td><td><span class="badge">зашифрован</span><br><span class="muted">${esc(x.status)}</span></td><td><button data-document-id="${x.document_id}" onclick="downloadDocument(${x.document_id},this)">Скачать один раз</button></td></tr>`).join('')}</table>`:''}catch(e){feedback(`Документы не загружены: ${e.message}`,'error')}finally{loadingDocuments=false;if(target){target.disabled=false;target.removeAttribute('aria-busy');target.textContent=label}}}
async function withDocumentGrant(id,button,work){if(pendingDocuments.has(id))return;pendingDocuments.add(id);const controls=Array.from(document.querySelectorAll(`[data-document-id="${id}"]`));const labels=new Map(controls.map(x=>[x,x.textContent]));controls.forEach(x=>{x.disabled=true;x.setAttribute('aria-busy','true')});if(button)button.textContent='Подготовка…';try{return await work()}finally{pendingDocuments.delete(id);controls.forEach(x=>{x.disabled=false;x.removeAttribute('aria-busy')});labels.forEach((label,x)=>{x.textContent=label})}}
async function downloadDocument(id,button){return withDocumentGrant(id,button,async()=>{feedback('Создаётся одноразовое разрешение…');try{const grant=await api('/document-access/documents/'+id+'/grant',{method:'POST',body:'{}'});feedback('Разрешение создано. Документ расшифровывается для выдачи…');const response=await fetch(grant.download_url,{method:'GET',credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':token}});if(!response.ok)throw await responseError(response,'Не удалось получить документ по одноразовому разрешению');const blob=await response.blob();const objectUrl=URL.createObjectURL(blob);try{const link=document.createElement('a');link.href=objectUrl;link.download=safeFileName(grant.file_name,id);document.body.appendChild(link);link.click();link.remove()}finally{setTimeout(()=>URL.revokeObjectURL(objectUrl),0)}feedback(`Документ ${safeFileName(grant.file_name,id)} передан браузеру для сохранения`,'ok')}catch(e){feedback(`Документ не скачан: ${e.message}`,'error')}})}
boot();
</script>
</body>
</html>
"""
