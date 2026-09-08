from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.guided_lawyer_ui import (
    _CONSULTATION_DRAFT_PATCH,
    _inject_patch,
    guided_complete_consultation,
)
from app.api.lawyer_consultation_desk import CONSULTATION_DESK_HTML
from app.db.session import get_db

router = APIRouter(tags=["lawyer-consultation-decision-guard"])

# The MVP has only two routes. After a completed M2 consultation the lawyer may
# close the matter, move it to M1, or keep the same M2 route for an explicit
# follow-up consultation. A generic "other" outcome leaves the case in a
# non-terminal M2_CONSULTATION_DONE state with no deterministic business step,
# so it is intentionally rejected in the production UI/API.
ALLOWED_COMPLETION_DECISIONS = frozenset({"close", "to_m1", "follow_up"})
_OTHER_OPTION = '<option value="other">Иное решение</option>'
_OLD_HELP = (
    "Опишите вывод для клиента и выберите дальнейший маршрут. "
    "Изменение произойдёт только после подтверждения."
)
_NEW_HELP = (
    "Опишите итог для клиента и выберите законченный следующий шаг: "
    "перевод в M1, закрытие обращения или повторную консультацию в том же M2. "
    "Изменение произойдёт только после подтверждения."
)


def guarded_consultation_desk_html() -> str:
    html = _inject_patch(CONSULTATION_DESK_HTML, _CONSULTATION_DRAFT_PATCH)
    if html.count(_OTHER_OPTION) != 1:
        raise RuntimeError(
            "Consultation desk template contract changed: ambiguous outcome option not found"
        )
    html = html.replace(_OTHER_OPTION, "", 1)
    if html.count(_OLD_HELP) != 1:
        raise RuntimeError(
            "Consultation desk template contract changed: completion help text not found"
        )
    return html.replace(_OLD_HELP, _NEW_HELP, 1)


@router.post("/lawyer/consultations/{consultation_id}/complete")
async def guarded_complete_consultation(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in ALLOWED_COMPLETION_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Выберите законченный итог консультации: закрыть обращение, "
                "перевести в M1 или назначить повторную консультацию."
            ),
        )
    return await guided_complete_consultation(
        consultation_id=consultation_id,
        payload=payload,
        db=db,
        x_admin_token=x_admin_token,
    )


@router.get("/lawyer/consultation-desk/ui", response_class=HTMLResponse)
async def guarded_consultation_desk_ui():
    return HTMLResponse(guarded_consultation_desk_html())
