from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.assignment_policy import automatic_assignment_required
from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.cases.case_timeline import get_client_visible_status
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["case-assignment-repair"])


async def _actor(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _repair_context(db: AsyncSession, case_id: int) -> dict[str, object]:
    case = await db.get(Case, int(case_id))
    if case is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")

    current_lawyer = (
        await db.get(Lawyer, int(case.assigned_lawyer_id))
        if case.assigned_lawyer_id is not None
        else None
    )
    operational = await CaseAssignmentService(db).list_active_lawyers()
    operational_ids = {int(item["id"]) for item in operational}
    current_id = int(case.assigned_lawyer_id) if case.assigned_lawyer_id is not None else None
    unreachable = current_id is not None and current_id not in operational_ids

    return {
        "case_id": int(case.id),
        "case_number": case.case_number,
        "status": str(case.status),
        "status_label": get_client_visible_status(str(case.status)),
        "current_lawyer_id": current_id,
        "current_lawyer_name": (
            current_lawyer.full_name
            if current_lawyer is not None
            else f"Недоступный профиль #{current_id}"
            if current_id is not None
            else None
        ),
        "unreachable": unreachable,
        "automatic_replacement_expected": automatic_assignment_required(case.status),
        "available_replacement_count": len(operational),
    }


@router.get("/admin/case-assignment/cases/{case_id}/repair-unreachable")
async def unreachable_assignment_context(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _actor(request, db, x_admin_token)
    return await _repair_context(db, case_id)


@router.post("/admin/case-assignment/cases/{case_id}/repair-unreachable")
async def repair_unreachable_assignment(
    case_id: int,
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _actor(request, db, x_admin_token)
    comment = str(payload.get("comment") or "").strip()
    if len(comment) < 10:
        raise HTTPException(status_code=400, detail="Укажите основание исправления минимум в 10 символах")
    try:
        expected_lawyer_id = int(payload.get("expected_lawyer_id"))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Не указан ожидаемый текущий юрист") from error
    expected_status = str(payload.get("expected_status") or "").strip()
    if not expected_status:
        raise HTTPException(status_code=400, detail="Не указан ожидаемый статус дела")

    case = (
        await db.execute(
            select(Case).where(Case.id == int(case_id)).with_for_update()
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    if str(case.status) != expected_status:
        raise HTTPException(status_code=409, detail="Статус дела уже изменился. Обновите карточку")
    if case.assigned_lawyer_id != expected_lawyer_id:
        raise HTTPException(status_code=409, detail="Назначение уже изменилось. Обновите карточку")

    service = CaseAssignmentService(db)
    operational = await service.list_active_lawyers()
    operational_ids = {int(item["id"]) for item in operational}
    if expected_lawyer_id in operational_ids:
        raise HTTPException(
            status_code=409,
            detail=(
                "Текущий юрист снова доступен для входа и назначения. "
                "Автоматическое аварийное переназначение больше не требуется"
            ),
        )

    try:
        await service.unassign_case(
            case_id=int(case_id),
            actor_type="admin",
            actor_id=int(actor.account_id),
            comment=f"Аварийное снятие недоступного назначения: {comment}",
        )
        replacement = None
        if automatic_assignment_required(case.status):
            replacement = await service.auto_assign_case(
                case_id=int(case_id),
                actor_type="admin",
                actor_id=int(actor.account_id),
                comment=f"Автоматическое переназначение после недоступного аккаунта: {comment}",
                expected_lawyer_id=None,
                expected_status=str(case.status),
            )
        await db.commit()
    except (LookupError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise

    assigned_id = int(replacement.assigned_lawyer_id) if replacement and replacement.assigned_lawyer_id else None
    return {
        "ok": True,
        "case_id": int(case.id),
        "removed_lawyer_id": expected_lawyer_id,
        "assigned_lawyer_id": assigned_id,
        "result": (
            "reassigned"
            if assigned_id is not None
            else "unassigned_waiting_capacity"
            if automatic_assignment_required(case.status)
            else "obsolete_assignment_removed"
        ),
    }


@router.get(
    "/admin/case-assignment/cases/{case_id}/repair-unreachable/ui",
    response_class=HTMLResponse,
)
async def repair_unreachable_assignment_ui(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _actor(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    context = await _repair_context(db, case_id)
    if not context["unreachable"]:
        return HTMLResponse(
            "<main style='font-family:system-ui;max-width:720px;margin:40px auto'>"
            "<h2>Назначение уже актуально</h2>"
            "<p>Аварийное переназначение не требуется. Вернитесь в рабочий стол.</p>"
            "<p><a href='/admin/workdesk/ui'>← Рабочий стол</a></p></main>"
        )

    case_number = escape(str(context["case_number"]))
    status_label = escape(str(context["status_label"]))
    lawyer_name = escape(str(context["current_lawyer_name"] or "не указан"))
    expected_id = int(context["current_lawyer_id"])
    expected_status = escape(str(context["status"]), quote=True)
    count = int(context["available_replacement_count"])
    expectation = (
        f"Система попробует сразу выбрать нового доступного юриста по текущей загрузке. Доступных профилей: {count}."
        if context["automatic_replacement_expected"]
        else "Этот этап не требует автоматического M1-назначения; недоступное устаревшее назначение будет только снято."
    )
    return HTMLResponse(
        f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Исправить назначение</title><style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6fa;color:#172033;margin:0}}main{{max-width:760px;margin:36px auto;padding:0 18px}}.card{{background:#fff;border:1px solid #e4e7ec;border-radius:16px;padding:22px;box-shadow:0 10px 30px rgba(16,24,40,.07)}}.warn{{background:#fff7e6;border:1px solid #fedf89;border-radius:12px;padding:13px}}label{{display:block;font-weight:700;margin-top:16px}}textarea{{width:100%;min-height:92px;padding:11px;border:1px solid #d0d5dd;border-radius:10px;box-sizing:border-box}}button,.button{{display:inline-block;border:0;border-radius:10px;padding:10px 13px;background:#3157d5;color:#fff;font-weight:700;text-decoration:none;cursor:pointer}}.secondary{{background:#475467}}#msg{{margin-top:12px}}</style></head><body><main><div class='card'>
<div style='font-size:12px;color:#667085;font-weight:800'>КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ · {case_number}</div><h1>Недоступный ответственный</h1><p><b>Этап:</b> {status_label}<br><b>Сейчас назначен:</b> {lawyer_name}</p><div class='warn'>У этого профиля нет рабочего персонального входа с ролью lawyer либо профиль отключён. {escape(expectation)}</div>
<label for='comment'>Основание исправления</label><textarea id='comment' placeholder='Например: аккаунт сотрудника отключён, дело необходимо вернуть доступному юристу'></textarea><p><button id='go' onclick='repair()'>Исправить назначение</button> <a class='button secondary' href='/admin/workdesk/ui'>Отмена</a></p><div id='msg'></div></div></main><script>
async function repair(){{const b=document.getElementById('go'),m=document.getElementById('msg'),comment=document.getElementById('comment').value.trim();if(comment.length<10){{m.textContent='Укажите понятное основание минимум в 10 символах.';return}}b.disabled=true;b.textContent='Проверяем и исправляем…';try{{const r=await fetch('/admin/case-assignment/cases/{int(case_id)}/repair-unreachable',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{expected_lawyer_id:{expected_id},expected_status:'{expected_status}',comment}})}});const d=await r.json().catch(()=>({{}}));if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Исправление не выполнено');m.textContent=d.result==='reassigned'?'Готово: дело передано доступному юристу.':'Недоступное назначение снято. Дело осталось в контролируемой очереди и не потеряно.';setTimeout(()=>location.href='/admin/workdesk/ui',900)}}catch(e){{m.textContent=e.message;b.disabled=false;b.textContent='Исправить назначение'}}}}
</script></body></html>"""
    )
