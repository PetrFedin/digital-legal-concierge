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
    run_control_examples,
    validate_rule_payload,
)
from app.models.calculation_rule_revision import CalculationRuleRevision
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
)
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["calculator-builder"])

_SECTION_SPECS = (
    ("formula", "1. Базовая формула", "object"),
    ("rate_policy", "2. Источник ставки", "object"),
    ("rate_directory", "3. Справочник ставок ЦБ", "list"),
    ("moratoria", "4. Моратории / исключённые периоды", "list"),
    ("rate_caps", "5. Ограничения ставки (caps)", "list"),
    ("client_types", "6. Тип клиента", "object"),
    ("unique_object", "7. Уникальный объект", "object"),
    ("stop_factors", "8. Условия ручной проверки / стоп-факторы", "list"),
    ("control_examples", "9. Контрольные примеры", "list"),
    ("sources", "10. Реестр правовых и расчётных источников", "object"),
)


async def _viewer(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER}:
        raise HTTPException(status_code=403, detail="Недостаточно прав для правил расчёта")
    return actor


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await _viewer(request, db, header_token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(
            status_code=403,
            detail="Редактирование DRAFT доступно администратору",
        )
    return actor


async def _legal_reviewer(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await _viewer(request, db, header_token)
    if actor.role not in {ROLE_LAWYER, ROLE_SUPERADMIN}:
        raise HTTPException(
            status_code=403,
            detail="Юридическое подтверждение доступно юристу или SUPERADMIN",
        )
    return actor


async def _superadmin(request: Request, db: AsyncSession, header_token: str | None):
    actor = await _viewer(request, db, header_token)
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="APPROVED / production / RETIRED доступны только SUPERADMIN",
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


def _parse_fragment(raw: str, *, key: str, kind: str):
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise CalculationRuleRevisionError(
            f"{key}: некорректный JSON ({error.msg})"
        ) from error
    expected = dict if kind == "object" else list
    if not isinstance(parsed, expected):
        expected_ru = "объект" if kind == "object" else "список"
        raise CalculationRuleRevisionError(f"{key}: ожидается JSON-{expected_ru}")
    return parsed


def _pretty_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _structured_rules(
    *,
    formula_json: str,
    rate_policy_json: str,
    rate_directory_json: str,
    moratoria_json: str,
    rate_caps_json: str,
    client_types_json: str,
    unique_object_json: str,
    stop_factors_json: str,
    control_examples_json: str,
    sources_json: str,
) -> dict:
    raw_values = {
        "formula": formula_json,
        "rate_policy": rate_policy_json,
        "rate_directory": rate_directory_json,
        "moratoria": moratoria_json,
        "rate_caps": rate_caps_json,
        "client_types": client_types_json,
        "unique_object": unique_object_json,
        "stop_factors": stop_factors_json,
        "control_examples": control_examples_json,
        "sources": sources_json,
    }
    kinds = {key: kind for key, _, kind in _SECTION_SPECS}
    rules: dict = {"schema_version": 2}
    for key, raw in raw_values.items():
        parsed = _parse_fragment(raw, key=key, kind=kinds[key])
        if parsed is not None:
            rules[key] = parsed
    return rules


async def _revision(db: AsyncSession, revision_id: int) -> CalculationRuleRevision:
    item = await db.get(CalculationRuleRevision, int(revision_id))
    if item is None:
        raise CalculationRuleRevisionError("Редакция правил не найдена")
    return item


def _source_links(rules: dict) -> str:
    sources = rules.get("sources")
    if not isinstance(sources, dict) or not sources:
        return "<div class='source-empty'>Источники пока не зафиксированы.</div>"
    rows: list[str] = []
    for code, raw in sorted(sources.items()):
        if not isinstance(raw, dict):
            continue
        title = escape(str(raw.get("title") or code))
        url = str(raw.get("url") or "")
        url_html = (
            f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(url)}</a>'
            if url.startswith("https://")
            else escape(url or "ссылка не указана")
        )
        locator = escape(str(raw.get("locator") or ""))
        checked = escape(str(raw.get("checked_at") or ""))
        rows.append(
            "<tr>"
            f"<td><code>{escape(str(code))}</code></td>"
            f"<td>{title}</td>"
            f"<td>{locator or '—'}</td>"
            f"<td>{url_html}</td>"
            f"<td>{checked or '—'}</td>"
            "</tr>"
        )
    return (
        "<div class='source-table'><table><thead><tr>"
        "<th>ID</th><th>Источник</th><th>Точное основание</th><th>Ссылка</th><th>Проверено</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _review_meta(item: CalculationRuleRevision) -> str:
    parts: list[str] = []
    if item.legal_reviewed_at:
        parts.append(
            "Юридически подтверждено: "
            f"{escape(item.legal_reviewed_at.isoformat())}; "
            f"actor={escape(str(item.legal_reviewed_by_actor_type or ''))}:"
            f"{escape(str(item.legal_reviewed_by_actor_id or ''))}; "
            f"sha={escape(str(item.legal_review_sha256 or ''))}"
        )
    if item.approved_at:
        parts.append(
            "APPROVED: "
            f"{escape(item.approved_at.isoformat())}; "
            f"actor={escape(str(item.approved_by_actor_type or ''))}:"
            f"{escape(str(item.approved_by_actor_id or ''))}"
        )
    if item.published_at:
        parts.append(
            "PRODUCTION: "
            f"{escape(item.published_at.isoformat())}; "
            f"actor={escape(str(item.published_by_actor_type or ''))}:"
            f"{escape(str(item.published_by_actor_id or ''))}"
        )
    if item.legal_review_comment:
        parts.append("Комментарий юриста: " + escape(item.legal_review_comment))
    return "".join(f"<div class='meta'>{part}</div>" for part in parts)


def _section_editor(item: CalculationRuleRevision) -> str:
    rules = item.rules if isinstance(item.rules, dict) else {}
    blocks: list[str] = []
    for key, label, kind in _SECTION_SPECS:
        value = rules.get(key)
        shown = "" if value is None else _pretty_json(value)
        placeholder = "{}" if kind == "object" else "[]"
        clear = ""
        if key != "sources" and value is not None:
            clear = f"""
            <button class="danger ghost" type="submit"
              formaction="/calculator-builder/{item.id}/clear-section"
              formmethod="post"
              name="section" value="{escape(key, quote=True)}">
              Обнулить раздел + отвязать источник
            </button>
            """
        blocks.append(
            f"""
            <section class="editor-section">
              <div class="section-head">
                <div><h3>{escape(label)}</h3><code>{escape(key)}</code></div>
                {clear}
              </div>
              <textarea name="{escape(key)}_json" rows="8" spellcheck="false"
                placeholder="{escape(placeholder, quote=True)}">{escape(shown)}</textarea>
            </section>
            """
        )
    return "".join(blocks)


def _readonly_sections(item: CalculationRuleRevision) -> str:
    rules = item.rules if isinstance(item.rules, dict) else {}
    blocks: list[str] = []
    for key, label, _ in _SECTION_SPECS:
        if key not in rules:
            continue
        blocks.append(
            f"""
            <details class="readonly-section">
              <summary>{escape(label)}</summary>
              <pre>{escape(_pretty_json(rules[key]))}</pre>
            </details>
            """
        )
    return "".join(blocks)


@router.get("/calculator-builder/status")
async def calculator_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _viewer(request, db, x_admin_token)
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
        "status": "versioned_calculation_rules_v2",
        "draft_revisions": int(counts.get("DRAFT", 0)),
        "legal_reviewed_revisions": int(counts.get("LEGAL_REVIEWED", 0)),
        "approved_revisions": int(counts.get("APPROVED", 0)),
        "production_revisions": int(counts.get("PRODUCTION", 0)),
        "retired_revisions": int(counts.get("RETIRED", 0)),
        "rule_management": "/calculator-builder/ui",
        "workdesk": "/admin/workdesk/ui",
        "integrity": "/admin/workdesk/integrity",
        "production_note": (
            "Новый расчёт использует только PRODUCTION-ревизию schema v2. "
            "DRAFT → LEGAL_REVIEWED → APPROVED → PRODUCTION; каждый переход "
            "привязан к точному SHA-256 и журналируется."
        ),
    }


@router.get("/calculator-builder/rules")
async def calculator_rules(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _viewer(request, db, x_admin_token)
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
                "legal_reviewed_by_actor_type": item.legal_reviewed_by_actor_type,
                "legal_reviewed_by_actor_id": item.legal_reviewed_by_actor_id,
                "legal_reviewed_at": (
                    item.legal_reviewed_at.isoformat() if item.legal_reviewed_at else None
                ),
                "legal_review_sha256": item.legal_review_sha256,
                "approved_by_actor_type": item.approved_by_actor_type,
                "approved_by_actor_id": item.approved_by_actor_id,
                "approved_at": item.approved_at.isoformat() if item.approved_at else None,
                "published_by_actor_type": item.published_by_actor_type,
                "published_by_actor_id": item.published_by_actor_id,
                "published_at": item.published_at.isoformat() if item.published_at else None,
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
        actor = await _viewer(request, db, x_admin_token)
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
    is_admin = actor.role in {ROLE_ADMIN, ROLE_SUPERADMIN}
    is_legal_reviewer = actor.role in {ROLE_LAWYER, ROLE_SUPERADMIN}
    is_superadmin = actor.role == ROLE_SUPERADMIN

    cards: list[str] = []
    for item in revisions:
        status = str(item.status).upper()
        effective_to = item.effective_to.isoformat() if item.effective_to else ""
        lifecycle_actions: list[str] = []

        if status == "DRAFT" and is_legal_reviewer:
            lifecycle_actions.append(
                f"""
                <form class="inline review-form" method="post" action="/calculator-builder/{item.id}/legal-review">
                  <input name="comment" placeholder="Комментарий юридического подтверждения" required>
                  <button class="review" type="submit">Юридически подтвердить SHA</button>
                </form>
                """
            )
        if status in {"LEGAL_REVIEWED", "APPROVED"} and is_admin:
            lifecycle_actions.append(
                f"""
                <form class="inline" method="post" action="/calculator-builder/{item.id}/return-draft">
                  <input name="comment" value="Требуется изменение редакции" required>
                  <button class="danger ghost" type="submit">Вернуть в DRAFT</button>
                </form>
                """
            )
        if status == "LEGAL_REVIEWED" and is_superadmin:
            lifecycle_actions.append(
                f"""
                <form class="inline" method="post" action="/calculator-builder/{item.id}/approve">
                  <button class="approve" type="submit">Перевести в APPROVED</button>
                </form>
                """
            )
        if status == "APPROVED" and is_superadmin:
            lifecycle_actions.append(
                f"""
                <form class="inline" method="post" action="/calculator-builder/{item.id}/publish">
                  <button class="publish" type="submit">Опубликовать в production</button>
                </form>
                """
            )
        if status == "PRODUCTION" and is_superadmin:
            lifecycle_actions.append(
                f"""
                <form class="inline" method="post" action="/calculator-builder/{item.id}/retire">
                  <button class="retire" type="submit">Вывести из новых расчётов</button>
                </form>
                """
            )

        if status == "DRAFT" and is_admin and item.rules.get("schema_version") == 2:
            body = f"""
            <form class="structured-form" method="post" action="/calculator-builder/{item.id}/edit">
              <input type="hidden" name="expected_updated_at" value="{escape(item.updated_at.isoformat(), quote=True)}">
              <div class="period-grid">
                <label>Действует с<input type="date" name="effective_from" value="{escape(item.effective_from.isoformat(), quote=True)}" required></label>
                <label>Действует по<input type="date" name="effective_to" value="{escape(effective_to, quote=True)}"></label>
              </div>
              <label class="wide">Комментарий / основание редакции<textarea name="note" rows="2">{escape(item.note or '')}</textarea></label>
              {_section_editor(item)}
              <input type="hidden" name="rules_json" value="">
              <div class="sticky-actions">
                <button type="submit">Сохранить DRAFT</button>
                <button class="validate" type="submit" formaction="/calculator-builder/{item.id}/validate">Проверить сохранённую DRAFT</button>
              </div>
            </form>
            """
        elif status == "DRAFT" and is_admin:
            body = f"""
            <div class="notice warning">Эта старая DRAFT-редакция использует schema v1. Для production v2 создайте новую редакцию. Исторический JSON сохранён без автоконвертации.</div>
            <form method="post" action="/calculator-builder/{item.id}/edit">
              <input type="hidden" name="expected_updated_at" value="{escape(item.updated_at.isoformat(), quote=True)}">
              <label>Действует с<input type="date" name="effective_from" value="{escape(item.effective_from.isoformat(), quote=True)}" required></label>
              <label>Действует по<input type="date" name="effective_to" value="{escape(effective_to, quote=True)}"></label>
              <label class="wide">Комментарий<textarea name="note" rows="2">{escape(item.note or '')}</textarea></label>
              <label class="wide">Legacy JSON<textarea class="rules" name="rules_json" rows="18" spellcheck="false">{escape(_pretty_json(item.rules))}</textarea></label>
              <div class="wide actions"><button type="submit">Сохранить legacy DRAFT</button></div>
            </form>
            """
        else:
            body = f"""
            <div class="readonly"><strong>Период:</strong> {escape(item.effective_from.isoformat())} — {escape(effective_to or 'без даты окончания')}</div>
            <div class="readonly"><strong>Комментарий:</strong> {escape(item.note or 'не указано')}</div>
            {_readonly_sections(item)}
            """

        cards.append(
            f"""
            <article class="rule-card">
              <div class="rule-head">
                <div><h2>{escape(item.revision_key)}</h2>
                <code>id={item.id} · sha256={escape(item.rules_sha256)}</code></div>
                <span class="status {status.lower()}">{escape(status)}</span>
              </div>
              {_review_meta(item)}
              <div class="source-title"><strong>Зафиксированные источники</strong></div>
              {_source_links(item.rules)}
              {body}
              <div class="lifecycle">{''.join(lifecycle_actions)}</div>
              <details><summary>Полный неизбыточный JSON-снимок</summary><pre>{escape(_pretty_json(item.rules))}</pre></details>
            </article>
            """
        )

    notice_html = (
        f'<div class="notice">{escape(notice)}</div>'
        if notice
        else """
        <div class="notice warning">
          <strong>PM-016 v2 — юридическая authority.</strong>
          Новый расчёт не получает «текущую ставку» из настройки и не угадывает нормы.
          Редактор хранит значение вместе с source_refs и проверяемой HTTPS-ссылкой.
          Удаление раздела удаляет его ссылки и автоматически очищает больше не используемые
          источники. Production допускает только цепочку DRAFT → LEGAL_REVIEWED → APPROVED → PRODUCTION.
        </div>
        """
    )
    empty = (
        "<div class='empty'>Редакций пока нет. Это безопасное состояние: без PRODUCTION-ревизии калькулятор fail-closed и не подставляет юридические значения.</div>"
        if not revisions
        else ""
    )
    create = ""
    if is_admin:
        create = """
<section class="create">
  <h2>Новая DRAFT-редакция schema v2</h2>
  <p class="schema">Создаётся пустой юридический контейнер. Ставки, моратории, caps и источники не заполняются автоматически: их нужно внести в разделы ниже и связать через source_refs.</p>
  <form method="post" action="/calculator-builder/draft">
    <label>Ключ ревизии<input name="revision_key" maxlength="100" required></label>
    <label>Действует с<input type="date" name="effective_from" required></label>
    <label>Действует по<input type="date" name="effective_to"></label>
    <label class="wide">Комментарий / основание редакции<textarea name="note" rows="2"></textarea></label>
    <div class="wide actions"><button type="submit">Создать DRAFT v2</button></div>
  </form>
</section>
"""

    html = f"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Правила расчёта — Digital Legal Concierge</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--amber:#a15c00;--red:#b42318;--purple:#6941c6;--shadow:0 10px 28px rgba(16,24,40,.07)}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}}header{{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 22px}}.head,main{{max-width:1240px;margin:auto}}.head{{display:flex;justify-content:space-between;gap:14px;align-items:center}}h1{{font-size:22px;margin:0 0 4px}}header p{{margin:0;color:#d0d5dd;font-size:13px}}.links{{display:flex;gap:8px;flex-wrap:wrap}}a.button,button{{border:0;border-radius:10px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}}a.secondary{{background:#475467}}main{{padding:20px}}.notice,.empty{{border:1px solid #c7d2fe;background:#eef2ff;border-radius:13px;padding:12px;margin-bottom:14px;line-height:1.5}}.warning{{border-color:#fedf89;background:#fff7e6}}.create,.rule-card{{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:16px;margin-bottom:14px;box-shadow:var(--shadow)}}.create h2,.rule-card h2{{margin:0 0 5px;font-size:18px}}form:not(.inline):not(.review-form){{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}}label{{font-size:12px;font-weight:700;color:var(--muted)}}input,textarea{{display:block;width:100%;margin-top:5px;padding:10px;border:1px solid #d0d5dd;border-radius:10px;font:inherit;color:var(--ink);background:#fff}}textarea{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.45}}.wide{{grid-column:1/-1}}.actions,.sticky-actions{{display:flex;gap:8px;flex-wrap:wrap}}.rule-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}code,.meta{{font-size:11px;color:var(--muted);word-break:break-all}}.status{{border-radius:999px;padding:5px 9px;font-size:11px;font-weight:800}}.draft{{background:#f2f4f7}}.legal_reviewed{{background:#f4f3ff;color:#5925dc}}.approved{{background:#ecfdf3;color:#067647}}.production{{background:#dcfae6;color:#05603a}}.retired{{background:#fef3f2;color:#b42318}}pre{{white-space:pre-wrap;overflow:auto;border:1px solid var(--line);padding:12px;border-radius:10px;background:#f8fafc}}.readonly{{margin:9px 0;font-size:13px}}.inline{{display:inline-block;margin:10px 8px 0 0}}.review-form{{display:flex;gap:8px;align-items:end;margin:10px 8px 0 0}}.review-form input{{min-width:280px;margin:0}}button.approve,button.review{{background:var(--green)}}button.publish{{background:var(--purple)}}button.retire,button.danger{{background:var(--red)}}button.ghost{{background:#fff;color:var(--red);border:1px solid #fda29b}}button.validate{{background:#475467}}.schema{{font-size:12px;color:var(--muted);line-height:1.5}}.editor-section{{grid-column:1/-1;border:1px solid var(--line);border-radius:12px;padding:12px;background:#fafbfc}}.editor-section textarea{{min-height:140px}}.section-head{{display:flex;justify-content:space-between;gap:10px;align-items:center}}.section-head h3{{margin:0 0 3px;font-size:15px}}.period-grid{{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:10px}}.sticky-actions{{grid-column:1/-1;position:sticky;bottom:8px;background:rgba(255,255,255,.95);padding:10px;border:1px solid var(--line);border-radius:12px;z-index:2}}.source-title{{margin-top:12px}}.source-table{{overflow:auto;margin:8px 0 12px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{text-align:left;border:1px solid var(--line);padding:7px;vertical-align:top}}th{{background:#f8fafc}}.source-empty{{color:var(--muted);font-size:12px;margin:8px 0 12px}}.readonly-section{{border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin:8px 0}}.readonly-section summary,details summary{{cursor:pointer;font-weight:700}}.lifecycle{{border-top:1px solid var(--line);margin-top:12px;padding-top:4px}}@media(max-width:760px){{.head{{align-items:flex-start;flex-direction:column}}form:not(.inline):not(.review-form){{grid-template-columns:1fr}}.wide,.editor-section,.sticky-actions,.period-grid{{grid-column:1}}.period-grid{{grid-template-columns:1fr}}.review-form{{display:block}}.review-form input{{min-width:0;margin-bottom:6px}}main{{padding:12px}}}}
</style></head><body>
<header><div class="head"><div><h1>⚖️ Редактор юридических правил расчёта</h1><p>Формула → ставка на дату исполнения → моратории → caps → тип клиента → уникальный объект → stop-factors → контрольные примеры → legal review → APPROVED → production.</p></div><div class="links"><a class="button secondary" href="/admin/workdesk/ui">Рабочий стол</a><a class="button secondary" href="/settings-ui">Настройки</a></div></div></header>
<main>{notice_html}{create}{empty}{''.join(cards)}</main></body></html>
"""
    return HTMLResponse(html)


@router.post("/calculator-builder/draft")
async def create_rule_draft(
    request: Request,
    revision_key: str = Form(...),
    effective_from: str = Form(...),
    effective_to: str = Form(default=""),
    note: str = Form(default=""),
    rules_json: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        rules = (
            _parse_rules_json(rules_json)
            if str(rules_json or "").strip()
            else {"schema_version": 2, "sources": {}}
        )
        await CalculationRuleRevisionService(db).create_draft(
            revision_key=revision_key,
            effective_from=_parse_date(effective_from, field="effective_from"),
            effective_to=_parse_date(effective_to, field="effective_to", optional=True),
            rules=rules,
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
    return _redirect_notice(
        "DRAFT v2 создан. Он не влияет на клиентские расчёты до юридического подтверждения, APPROVED и публикации."
    )


@router.post("/calculator-builder/{revision_id}/edit")
async def edit_rule_draft(
    revision_id: int,
    request: Request,
    effective_from: str = Form(...),
    effective_to: str = Form(default=""),
    note: str = Form(default=""),
    expected_updated_at: str = Form(...),
    rules_json: str = Form(default=""),
    formula_json: str = Form(default=""),
    rate_policy_json: str = Form(default=""),
    rate_directory_json: str = Form(default=""),
    moratoria_json: str = Form(default=""),
    rate_caps_json: str = Form(default=""),
    client_types_json: str = Form(default=""),
    unique_object_json: str = Form(default=""),
    stop_factors_json: str = Form(default=""),
    control_examples_json: str = Form(default=""),
    sources_json: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        rules = (
            _parse_rules_json(rules_json)
            if str(rules_json or "").strip()
            else _structured_rules(
                formula_json=formula_json,
                rate_policy_json=rate_policy_json,
                rate_directory_json=rate_directory_json,
                moratoria_json=moratoria_json,
                rate_caps_json=rate_caps_json,
                client_types_json=client_types_json,
                unique_object_json=unique_object_json,
                stop_factors_json=stop_factors_json,
                control_examples_json=control_examples_json,
                sources_json=sources_json,
            )
        )
        await CalculationRuleRevisionService(db).update_draft(
            revision_id=revision_id,
            effective_from=_parse_date(effective_from, field="effective_from"),
            effective_to=_parse_date(effective_to, field="effective_to", optional=True),
            rules=rules,
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
    return _redirect_notice(
        "DRAFT обновлён. Неиспользуемые источники автоматически удалены; production не изменён."
    )


@router.post("/calculator-builder/{revision_id}/clear-section")
async def clear_rule_section(
    revision_id: int,
    request: Request,
    section: str = Form(...),
    expected_updated_at: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        await CalculationRuleRevisionService(db).clear_draft_section(
            revision_id=revision_id,
            section=section,
            expected_updated_at=expected_updated_at,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except CalculationRuleRevisionError as error:
        await db.rollback()
        return _redirect_notice(f"Раздел не очищен: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    return _redirect_notice(
        f"Раздел {section} очищен. Ссылки из него удалены; источники без других привязок удалены автоматически."
    )


@router.post("/calculator-builder/{revision_id}/validate")
async def validate_rule_revision(
    revision_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _viewer(request, db, x_admin_token)
        item = await _revision(db, revision_id)
        validate_rule_payload(item.rules)
        outcomes = run_control_examples(item.rules)
    except CalculationRuleRevisionError as error:
        return _redirect_notice(f"Проверка не пройдена: {error}")
    except (DocumentAccessError, HTTPException) as error:
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    return _redirect_notice(
        f"Проверка пройдена: схема валидна, контрольных примеров успешно {len(outcomes)}."
    )


@router.post("/calculator-builder/{revision_id}/legal-review")
async def legal_review_rule_revision(
    revision_id: int,
    request: Request,
    comment: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _legal_reviewer(request, db, x_admin_token)
        revision = await _revision(db, revision_id)
        await CalculationRuleRevisionService(db).confirm_legal_review(
            revision=revision,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
            comment=comment,
        )
        await db.commit()
    except CalculationRuleRevisionError as error:
        await db.rollback()
        return _redirect_notice(f"Юридическое подтверждение не зафиксировано: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    return _redirect_notice(
        "Юридическое подтверждение зафиксировано на точный SHA-256. Любое изменение теперь требует возврата в DRAFT."
    )


@router.post("/calculator-builder/{revision_id}/return-draft")
async def return_rule_to_draft(
    revision_id: int,
    request: Request,
    comment: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        revision = await _revision(db, revision_id)
        await CalculationRuleRevisionService(db).return_to_draft(
            revision=revision,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
            comment=comment,
        )
        await db.commit()
    except CalculationRuleRevisionError as error:
        await db.rollback()
        return _redirect_notice(f"Возврат в DRAFT не выполнен: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    return _redirect_notice(
        "Редакция возвращена в DRAFT. Предыдущее юридическое подтверждение и APPROVED сняты."
    )


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
        return _redirect_notice(f"APPROVED не зафиксирован: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    return _redirect_notice(
        "Редакция APPROVED, но ещё не используется клиентскими расчётами. Следующий отдельный шаг — production."
    )


@router.post("/calculator-builder/{revision_id}/publish")
async def publish_rule_revision(
    revision_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _superadmin(request, db, x_admin_token)
        revision = await _revision(db, revision_id)
        await CalculationRuleRevisionService(db).publish(
            revision=revision,
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except CalculationRuleRevisionError as error:
        await db.rollback()
        return _redirect_notice(f"Production-публикация не выполнена: {error}")
    except (DocumentAccessError, HTTPException) as error:
        await db.rollback()
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise
    return _redirect_notice(
        "Редакция опубликована в PRODUCTION и теперь может применяться к новым расчётам своего effective-периода."
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
    return _redirect_notice(
        "Редакция RETIRED: новые расчёты её не используют, исторические снимки и ссылки сохранены."
    )
