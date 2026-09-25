from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.guided_lawyer_ui import _inject_patch
from app.api.lawyer_consultation_decision_guard import guarded_consultation_desk_html
from app.api.lawyer_workspace_rejection_ui import enhanced_lawyer_workspace_html
from app.config import settings
from app.db.session import get_db
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["contract-workspace-ui"])


_CONTRACT_WORKSPACE_PATCH = r"""
<script>
(function(){
  const headerLinks=document.querySelector('header .links');
  if(headerLinks&&!headerLinks.querySelector('a[href="/consultation-slots/ui"]')){
    headerLinks.insertAdjacentHTML('afterbegin','<a class="button secondary" href="/consultation-slots/ui">Расписание</a>');
  }

  const previousPrimaryButton=primaryButton;
  primaryButton=function(x){
    if(x?.route==='M1'&&String(x.service_mode||'')==='SELF_FILING_PACKAGE'){
      return `<a class="button green" href="/self-filing/ui?case_id=${Number(x.case_id)}">Открыть пакет самостоятельной подачи</a>`;
    }
    if(x?.route==='M1'&&String(x.status||'')==='M1_CONTRACT_READY'){
      return `<a class="button green" href="/contracts/ui?case_id=${Number(x.case_id)}">Подготовить / проверить договор</a>`;
    }
    return previousPrimaryButton(x);
  };

  const previousCaseCard=caseCard;
  caseCard=function(x){
    let html=previousCaseCard(x);
    if(x?.route==='M1'&&String(x.service_mode||'')==='SELF_FILING_PACKAGE'){
      const id=Number(x.case_id);
      const block=`<div class="deadline"><b>Пакет для самостоятельной подачи</b><div class="muted">Это отдельный режим M1 без представительства в суде. Полнота документов, конкретная подсудность, 15 000 ₽, двухдневный рабочий SLA и финальная email-доставка ведутся в отдельной карточке.</div><div class="actions" style="margin-top:9px"><a class="button green" href="/self-filing/ui?case_id=${id}">Открыть карточку пакета</a><a class="button secondary" href="/message-center/ui?case_id=${id}">Переписка</a><a class="button secondary" href="/document-access/ui?case_id=${id}">Материалы</a></div></div>`;
      return html.replace('</article>',block+'</article>');
    }
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


async def _lawyer_ui_or_login(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return None
    try:
        return await require_lawyer_actor(db, token)
    except HTTPException:
        return None


@router.get("/lawyer/ui")
async def legacy_lawyer_ui_redirect(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _lawyer_ui_or_login(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/lawyer/workspace/ui", status_code=303)


@router.get("/lawyer/workspace/ui", response_class=HTMLResponse)
async def contract_aware_lawyer_workspace_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _lawyer_ui_or_login(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(contract_aware_workspace_html())


@router.get("/lawyer/consultation-desk/ui", response_class=HTMLResponse)
async def protected_lawyer_consultation_desk_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _lawyer_ui_or_login(request, db, x_admin_token)
    if actor is None:
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(guarded_consultation_desk_html())
