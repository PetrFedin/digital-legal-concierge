from __future__ import annotations

import json
from datetime import date
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.calculator.rule_revision_service import (
    CalculationRuleRevisionError,
    CalculationRuleRevisionService,
)
from app.models.calculation_rule_revision import CalculationRuleRevision
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["calculator-builder"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _superadmin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await _admin(request, db, header_token)
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="Активация и вывод редакций из применения доступны только SUPERADMIN",
        )
    return actor


def _auth_recovery(error: DocumentAccessError | HTTPException) -> RedirectResponse | None:
    if error.status_code == 401:
        return RedirectResponse(url="/login?next=/calculator-builder/ui", status_code=303)
    if error.status_code in {403, 409}:
        return RedirectResponse(url="/admin-ui", status_code=303)
    return None


def _redirect_notice(text: str) -> RedirectResponse:
    return RedirectResponse(
        url="/calculator-builder/ui?" + urlencode({"notice": text}),
        status_code=303,
    )


def _parse_date(raw: str, *, field: str, optional: bool = False) -> date | None:
    value = str(raw or "").strip()
    if not value and optional:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CalculationRuleRevisionError(
            f"{field}: ожидается дата YYYY-MM-DD"
        ) from error


def _parse_rules_json(raw: str) -> dict:
    try:
        value = json.loads(str(raw or ""))
    except json.JSONDecodeError as error:
        raise CalculationRuleRevisionError(
            f"Набор правил: некорректный JSON ({error.msg})"
        ) from error
    if not isinstance(value, dict):
        raise CalculationRuleRevisionError("Набор правил должен быть JSON-объектом")
    return value


def _pretty_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


async def _revision(db: AsyncSession, revision_id: int) -> CalculationRuleRevision:
    item = await db.get(CalculationRuleRevision, int(revision_id))
    if item is None:
        raise CalculationRuleRevisionError("Редакция правил не найдена")
    return item


@router.get("/calculator-builder/status")
async def calculator_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    counts = dict(
        (
            await db.execute(
                select(
                    CalculationRuleRevision.status,
                    func.count(CalculationRuleRevision.id),
                ).group_by(CalculationRuleRevision.status)
            )
        ).all()
    )
    return {
        "ok": True,
        "status": "versioned_calculation_rules",
        "draft_revisions": int(counts.get("DRAFT", 0)),
        "approved_revisions": int(counts.get("APPROVED", 0)),
        "retired_revisions": int(counts.get("RETIRED", 0)),
        "rule_management": "/calculator-builder/ui",
        "workdesk": "/admin/workdesk/ui",
        "integrity": "/admin/workdesk/integrity",
        "production_note": (
            "Наличие APPROVED-ревизии означает техническую активацию в сервисе; "
            "юридическое содержание должно быть подтверждено ответственным лицом вне автоматических defaults."
        ),
    }


@router.get("/calculator-builder/rules")
async def calculator_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    revisions = list(
        (
            await db.execute(
                select(CalculationRuleRevision).order_by(
                    CalculationRuleRevision.effective_from.desc(),
                    CalculationRuleRevision.id.desc(),
                )
            )
        ).scalars().all()
    )
    return {
        "items": [
            {
                "id": item.id,
                "revision_key": item.revision_key,
                "status": item.status,
                "effective_from": item.effective_from.isoformat(),
                "effective_to": item.effective_to.isoformat() if item.effective_to else None,
                "rules_sha256": item.rules_sha256,
                "rules": item.rules,
                "note": item.note,
                "approved_by_actor_type": item.approved_by_actor_type,
                "approved_by_actor_id": item.approved_by_actor_id,
                "approved_at": item.approved_at.isoformat() if item.approved_at else None,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in revisions
        ]
    }


@router.get("/calculator-builder/ui", response_class=HTMLResponse)
async def calculator_ui(
    request: Request,
    notice: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
    except (DocumentAccessError, HTTPException) as error:
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise

    revisions = list(
        (
            await db.execute(
                select(CalculationRuleRevision).order_by(
                    CalculationRuleRevision.effective_from.desc(),
                    CalculationRuleRevision.id.desc(),
                )
            )
        ).scalars().all()
    )
    is_superadmin = actor.role == ROLE_SUPERADMIN
    cards: list[str] = []
    for item in revisions:
        status = str(item.status).upper()
        effective_to = item.effective_to.isoformat() if item.effective_to else ""
        approved_meta = ""
        if item.approved_at:
            approved_meta = (
                f"<div class='meta'>Активирована: {escape(item.approved_at.isoformat())}; "
                f"actor={escape(str(item.approved_by_actor_type or ''))}:"
                f"{escape(str(item.approved_by_actor_id or ''))}</div>"
            )
        if status == "DRAFT":
            actions = ""
            if is_superadmin:
                actions = f"""
                <form class="inline" method="post" action="/calculator-builder/{item.id}/approve">
                  <button class="approve" type="submit">Активировать редакцию</button>
                </form>
                """
            else:
                actions = "<div class='meta'>Активация доступна только SUPERADMIN.</div>"
            body = f"""
            <form method="post" action="/calculator-builder/{item.id}/edit">
              <input type="hidden" name="expected_updated_at" value="{escape(item.updated_at.isoformat(), quote=True)}">
              <label>Действует с<input type="date" name="effective_from" value="{escape(item.effective_from.isoformat(), quote=True)}" required></label>
              <label>Действует по<input type="date" name="effective_to" value="{escape(effective_to, quote=True)}"></label>
              <label class="wide">Основание / ссылка на согласование<textarea name="note" rows="2">{escape(item.note or '')}</textarea></label>
              <label class="wide">JSON правил<textarea class="rules" name="rules_json" rows="18" spellcheck="false" required>{escape(_pretty_json(item.rules))}</textarea></label>
              <div class="wide actions"><button type="submit">Сохранить DRAFT</button></div>
            </form>
            {actions}
            """
        else:
            retire = ""
            if status == "APPROVED" and is_superadmin:
                retire = f"""
                <form class="inline" method="post" action="/calculator-builder/{item.id}/retire">
                  <button class="retire" type="submit">Вывести из новых расчётов</button>
                </form>
                """
            body = f"""
            <div class="readonly"><strong>Период:</strong> {escape(item.effective_from.isoformat())} — {escape(effective_to or 'без даты окончания')}</div>
            <div class="readonly"><strong>Основание:</strong> {escape(item.note or 'не указано')}</div>
            <pre>{escape(_pretty_json(item.rules))}</pre>
            {retire}
            """
        structured_link = (
            f'<a class="button structured" href="/calculator-builder/v2/{item.id}">Открыть редактор v2</a>'
            if isinstance(item.rules, dict) and item.rules.get("schema_version") == 2
            else ""
        )
        cards.append(
            f"""
            <article class="rule-card">
              <div class="rule-head"><div><h2>{escape(item.revision_key)}</h2><code>id={item.id} · sha256={escape(item.rules_sha256)}</code></div><span class="status {status.lower()}">{escape(status)}</span></div>
              {approved_meta}
              {structured_link}
              {body}
            </article>
            """
        )

    notice_html = (
        f'<div class="notice">{escape(notice)}</div>'
        if notice
        else """
        <div class="notice warning"><strong>Контроль юридической истины.</strong> Здесь нет встроенных ставок, мораториев или коэффициентов. DRAFT валидируется технически; перевод в APPROVED делает редакцию доступной новым расчётам. Перед активацией должны быть подтверждены источник, периоды, формула, коэффициенты и исключения ответственным за юридическое содержание.</div>
        """
    )
    empty = (
        "<div class='empty'>Редакций пока нет. Это безопасное состояние: без APPROVED-ревизии калькулятор не должен подставлять guessed legal values.</div>"
        if not revisions
        else ""
    )
    html = f"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Правила расчёта — Digital Legal Concierge</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--amber:#a15c00;--red:#b42318;--shadow:0 10px 28px rgba(16,24,40,.07)}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}}header{{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 22px}}.head,main{{max-width:1180px;margin:auto}}.head{{display:flex;justify-content:space-between;gap:14px;align-items:center}}h1{{font-size:22px;margin:0 0 4px}}header p{{margin:0;color:#d0d5dd;font-size:13px}}.links{{display:flex;gap:8px;flex-wrap:wrap}}a.button,button{{border:0;border-radius:10px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}}a.secondary{{background:#475467}}main{{padding:20px}}.notice,.empty{{border:1px solid #c7d2fe;background:#eef2ff;border-radius:13px;padding:12px;margin-bottom:14px;line-height:1.5}}.warning{{border-color:#fedf89;background:#fff7e6}}.create,.rule-card{{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:16px;margin-bottom:14px;box-shadow:var(--shadow)}}.create h2,.rule-card h2{{margin:0 0 5px;font-size:18px}}form:not(.inline){{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}}label{{font-size:12px;font-weight:700;color:var(--muted)}}input,textarea{{display:block;width:100%;margin-top:5px;padding:10px;border:1px solid #d0d5dd;border-radius:10px;font:inherit;color:var(--ink);background:#fff}}textarea.rules,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.45}}.wide{{grid-column:1/-1}}.actions{{display:flex;gap:8px}}.rule-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}code,.meta{{font-size:11px;color:var(--muted);word-break:break-all}}.status{{border-radius:999px;padding:5px 9px;font-size:11px;font-weight:800}}.draft{{background:#f2f4f7}}.approved{{background:#ecfdf3;color:#067647}}.retired{{background:#fef3f2;color:#b42318}}pre{{white-space:pre-wrap;overflow:auto;border:1px solid var(--line);padding:12px;border-radius:10px;background:#f8fafc}}.readonly{{margin:9px 0;font-size:13px}}.inline{{display:inline-block;margin:10px 8px 0 0}}button.approve{{background:var(--green)}}button.retire{{background:var(--red)}}a.structured{{display:inline-block;margin:10px 0;background:#14804a}}.schema{{font-size:12px;color:var(--muted);line-height:1.5}}@media(max-width:720px){{.head{{align-items:flex-start;flex-direction:column}}form:not(.inline){{grid-template-columns:1fr}}.wide{{grid-column:1}}main{{padding:12px}}}}
</style></head><body>
<header><div class="head"><div><h1>🧮 Правила предварительного расчёта</h1><p>Версии, effective dates, integrity hash и управляемая активация.</p></div><div class="links"><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a><a class="button secondary" href="/settings-ui">Настройки</a></div></div></header>
<main>{notice_html}
<section class="create"><h2>Рекомендуемый PM-016 v2</h2>
<p class="schema">Создаёт DRAFT из согласованной методологии: формула 214-ФЗ, ставка на договорную дату, моратории, caps, тип участника, уникальный объект, стоп-факторы, история ставки Банка России, контрольные примеры и карточки правовых источников. Ничего не становится APPROVED автоматически.</p>
<form method="post" action="/calculator-builder/v2/template">
<label>Ключ ревизии<input name="revision_key" maxlength="100" value="DDU-214FZ-2026-09-24" required></label>
<label>Действует с<input type="date" name="effective_from" value="{date.today().isoformat()}" required></label>
<label>Действует по<input type="date" name="effective_to"></label>
<div class="wide actions"><button type="submit">Создать согласованную DRAFT v2</button></div>
</form></section>
<section class="create"><h2>Расширенный JSON-режим</h2><p class="schema">JSON должен содержать schema_version, formula_code, delay_start_offset_days, divisor, client_types, money_quant, rounding_mode, rounding_stage, rates и excluded_periods. Значения юридических ставок и исключений здесь намеренно не предлагаются автоматически.</p>
<form method="post" action="/calculator-builder/draft">
<label>Ключ ревизии<input name="revision_key" maxlength="100" required></label>
<label>Действует с<input type="date" name="effective_from" required></label>
<label>Действует по<input type="date" name="effective_to"></label>
<label class="wide">Основание / ссылка на согласование<textarea name="note" rows="2"></textarea></label>
<label class="wide">JSON правил<textarea class="rules" name="rules_json" rows="18" spellcheck="false" placeholder="Вставьте проверенный JSON правил" required></textarea></label>
<div class="wide actions"><button type="submit">Создать DRAFT</button></div>
</form></section>
{empty}{''.join(cards)}</main></body></html>
"""
    return HTMLResponse(html)


@router.post("/calculator-builder/draft")
async def create_rule_draft(
    request: Request,
    revision_key: str = Form(...),
    effective_from: str = Form(...),
    effective_to: str = Form(default=""),
    rules_json: str = Form(...),
    note: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        await CalculationRuleRevisionService(db).create_draft(
            revision_key=revision_key,
            effective_from=_parse_date(effective_from, field="effective_from"),
            effective_to=_parse_date(effective_to, field="effective_to", optional=True),
            rules=_parse_rules_json(rules_json),
            note=note.strip() or None,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except (CalculationRuleRevisionError, IntegrityError) as error:
        await db.rollback()
        detail = "revision_key уже существует" if isinstance(error, IntegrityError) else str(error)
        return _redirect_notice(f"DRAFT не создан: {detail}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    except Exception:
        await db.rollback()
        raise
    return _redirect_notice("DRAFT создан. Он ещё не используется клиентскими расчётами.")


@router.post("/calculator-builder/{revision_id}/edit")
async def edit_rule_draft(
    revision_id: int,
    request: Request,
    effective_from: str = Form(...),
    effective_to: str = Form(default=""),
    rules_json: str = Form(...),
    note: str = Form(default=""),
    expected_updated_at: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        await CalculationRuleRevisionService(db).update_draft(
            revision_id=revision_id,
            effective_from=_parse_date(effective_from, field="effective_from"),
            effective_to=_parse_date(effective_to, field="effective_to", optional=True),
            rules=_parse_rules_json(rules_json),
            note=note.strip() or None,
            expected_updated_at=expected_updated_at,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except CalculationRuleRevisionError as error:
        await db.rollback()
        return _redirect_notice(f"Изменение не сохранено: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    except Exception:
        await db.rollback()
        raise
    return _redirect_notice("DRAFT обновлён. APPROVED-расчёты не изменены.")


@router.post("/calculator-builder/{revision_id}/approve")
async def approve_rule_revision(
    revision_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _superadmin(request, db, x_admin_token)
        revision = await _revision(db, revision_id)
        await CalculationRuleRevisionService(db).approve(
            revision=revision,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except CalculationRuleRevisionError as error:
        await db.rollback()
        return _redirect_notice(f"Редакция не активирована: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    except Exception:
        await db.rollback()
        raise
    return _redirect_notice(
        "Редакция переведена в APPROVED и может применяться к новым расчётам в своём effective-периоде."
    )


@router.post("/calculator-builder/{revision_id}/retire")
async def retire_rule_revision(
    revision_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _superadmin(request, db, x_admin_token)
        revision = await _revision(db, revision_id)
        await CalculationRuleRevisionService(db).retire(
            revision=revision,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except CalculationRuleRevisionError as error:
        await db.rollback()
        return _redirect_notice(f"Редакция не выведена из применения: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    except Exception:
        await db.rollback()
        raise
    return _redirect_notice(
        "Редакция RETIRED: новые расчёты её не используют, исторические доказательства сохранены."
    )
