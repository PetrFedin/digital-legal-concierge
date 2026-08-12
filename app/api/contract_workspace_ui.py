from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.guided_lawyer_ui import _inject_patch
from app.api.lawyer_workspace_rejection_ui import enhanced_lawyer_workspace_html
from app.config import settings
from app.db.session import get_db
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["contract-workspace-ui"])


_CONTRACT_WORKSPACE_PATCH = r"""
<script>
(function(){
  const previousPrimaryButton=primaryButton;
  primaryButton=function(x){
    if(x?.route==='M1'&&String(x.status||'')==='M1_CONTRACT_READY'){
      return `<a class="button green" href="/contracts/ui?case_id=${Number(x.case_id)}">Подготовить / проверить договор</a>`;
    }
    return previousPrimaryButton(x);
  };

  const previousCaseCard=caseCard;
  caseCard=function(x){
    let html=previousCaseCard(x);
    if(x?.route!=='M1'||String(x.status||'')!=='M1_CONTRACT_READY')return html;
    const id=Number(x.case_id);
    const block=`<div class="deadline"><b>Договор — обязательный артефакт до 30 000 ₽</b><div class="muted">Опубликуйте конкретную проверенную версию файла. Клиент получит её в Telegram; подтверждение будет связано с document_id, версией и SHA-256. Пока файла нет, первый платёж закрыт.</div><div class="actions" style="margin-top:9px"><a class="button green" href="/contracts/ui?case_id=${id}">Открыть договор</a><a class="button secondary" href="/message-center/ui?case_id=${id}">Переписка</a><a class="button secondary" href="/document-access/ui?case_id=${id}">Материалы</a></div></div>`;
    return html.replace('</article>',block+'</article>');
  };
})();
</script>
"""


def contract_aware_workspace_html() -> str:
    return _inject_patch(
        enhanced_lawyer_workspace_html(),
        _CONTRACT_WORKSPACE_PATCH,
    )


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def contract_aware_lawyer_workspace_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        await require_lawyer_actor(db, token)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(contract_aware_workspace_html())
