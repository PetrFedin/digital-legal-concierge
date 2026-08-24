from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_outcomes import (
    OUTCOMES_HTML,
    available_slots,
    mark_lawyer_no_show,
    rebook_after_lawyer_no_show,
    refund_after_lawyer_no_show,
)
from app.api.guided_consultation_outcomes import (
    _inject_client_no_show_ui,
    close_after_client_no_show,
    guided_outcome_queue,
    rebook_after_client_no_show,
)
from app.api.legacy_consultation_outcome_guard import (
    inject_legacy_outcome_ui,
    legacy_outcome_queue,
    resolve_legacy_outcome,
)
from app.config import settings
from app.db.session import get_db
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(
    prefix="/admin/consultation-outcomes",
    tags=["admin", "consultation-outcomes"],
)

# One runtime owner per public path. Existing endpoint functions remain the
# implementation source while this module is the only router mounted by the
# application. This removes FastAPI include-order and import-time route mutation
# without changing the business services or URLs used by staff UI/bookmarks.
router.add_api_route(
    "",
    guided_outcome_queue,
    methods=["GET"],
    name="consultation_outcomes_queue",
)
router.add_api_route(
    "/slots",
    available_slots,
    methods=["GET"],
    name="consultation_outcomes_slots",
)
router.add_api_route(
    "/{consultation_id}/lawyer-no-show",
    mark_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show",
)
router.add_api_route(
    "/{consultation_id}/rebook",
    rebook_after_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show_rebook",
)
router.add_api_route(
    "/{consultation_id}/refund",
    refund_after_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show_refund",
)
router.add_api_route(
    "/{consultation_id}/client-no-show/rebook",
    rebook_after_client_no_show,
    methods=["POST"],
    name="consultation_outcomes_client_no_show_rebook",
)
router.add_api_route(
    "/{consultation_id}/client-no-show/close",
    close_after_client_no_show,
    methods=["POST"],
    name="consultation_outcomes_client_no_show_close",
)
router.add_api_route(
    "/legacy",
    legacy_outcome_queue,
    methods=["GET"],
    name="consultation_outcomes_legacy_queue",
)
router.add_api_route(
    "/{consultation_id}/legacy/resolve",
    resolve_legacy_outcome,
    methods=["POST"],
    name="consultation_outcomes_legacy_resolve",
)


def _js_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False).replace("<", "\\u003c")


def _inject_business_timezone_ui(html: str) -> str:
    """Unify outcome-desk time and action hierarchy at the product boundary.

    The legacy base HTML formats timestamps in the browser's local timezone and
    mixes operational mutation buttons with navigation links. Staff may work
    from another region, so the same consultation must look identical in
    Telegram, Workdesk and this desk. The final product patch changes only
    presentation: business facts, forms, confirmations and endpoint ownership
    remain untouched.
    """

    head, marker, tail = html.rpartition("</body>")
    if not marker:
        raise RuntimeError("Consultation outcomes template contract changed: closing body missing")
    zone = _js_string(settings.business_timezone)
    label = _js_string(settings.business_timezone_label)
    patch = f"""
<style>
.guided-section-label{{margin:12px 0 6px;color:#667085;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}}
.guided-secondary{{border-top:1px solid #e4e7ec;margin-top:12px;padding-top:10px}}
.guided-secondary .row{{margin-top:0!important}}
</style>
<script>
(function(){{
  const businessTimeZone={zone};
  const businessTimeLabel={label};
  dt=function(v){{
    if(!v)return '—';
    try{{
      const rendered=new Intl.DateTimeFormat('ru-RU',{{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}}).format(new Date(v));
      return businessTimeLabel?rendered+' '+businessTimeLabel:rendered;
    }}catch(_){{return String(v)}}
  }};
  const subtitle=document.querySelector('header .header p');
  if(subtitle)subtitle.textContent=subtitle.textContent+' · Время: '+(businessTimeLabel||businessTimeZone);

  function applyGuidedHierarchy(){{
    document.querySelectorAll('.case').forEach(card=>{{
      const meta=card.querySelector('.meta');
      if(meta&&!card.querySelector('.guided-now')){{
        const now=document.createElement('div');
        now.className='guided-section-label guided-now';
        now.textContent='Сейчас';
        meta.parentNode.insertBefore(now,meta);
      }}
      const next=card.querySelector('.next');
      const nextHeading=next?.querySelector('b');
      if(nextHeading)nextHeading.textContent='Главный следующий шаг';

      const actions=card.querySelector('.actions');
      if(!actions||actions.querySelector('.guided-secondary'))return;
      const links=Array.from(actions.querySelectorAll('a.button.secondary'));
      if(!links.length)return;
      const secondary=document.createElement('div');
      secondary.className='guided-secondary';
      const heading=document.createElement('div');
      heading.className='guided-section-label';
      heading.textContent='Вторичные действия';
      const row=document.createElement('div');
      row.className='row';
      links.forEach(link=>row.appendChild(link));
      secondary.appendChild(heading);
      secondary.appendChild(row);
      actions.appendChild(secondary);
      Array.from(actions.querySelectorAll(':scope > .row')).forEach(existing=>{{
        if(existing!==row&&!existing.children.length)existing.remove();
      }});
    }});
  }}

  const previousRender=render;
  render=function(rows){{
    const result=previousRender(rows);
    applyGuidedHierarchy();
    return result;
  }};
  applyGuidedHierarchy();
}})();
</script>
"""
    return head + patch + marker + tail


@router.get("/ui", response_class=HTMLResponse, name="consultation_outcomes_ui")
async def consultation_outcomes_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Single authenticated staff UI for every existing M2 outcome decision."""

    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        return RedirectResponse(url="/admin-ui", status_code=303)

    html = _inject_client_no_show_ui(OUTCOMES_HTML)
    html = inject_legacy_outcome_ui(html)
    return HTMLResponse(_inject_business_timezone_ui(html))


__all__ = ["consultation_outcomes_ui", "router"]
