from __future__ import annotations

import json
from datetime import date
from html import escape
from typing import Any, Callable

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.calculator_builder import (
    _admin,
    _auth_recovery,
    _parse_date,
    _redirect_notice,
    _revision,
)
from app.db.session import get_db
from app.domain.calculator.rule_catalog_v2 import (
    approved_v2_rule_template,
    source_registry,
)
from app.domain.calculator.rule_editor_v2 import (
    RuleEditorV2Error,
    delete_item,
    set_core_section,
    set_participant_multiplier,
    set_unique_object_rule,
    update_source_card,
    upsert_control_example,
    upsert_manual_review_condition,
    upsert_period,
    upsert_rate,
)
from app.domain.calculator.rule_revision_service import (
    CalculationRuleRevisionError,
    CalculationRuleRevisionService,
)
from app.models.calculation_rule_revision import CalculationRuleRevision
from app.security.document_access import DocumentAccessError

router = APIRouter(tags=["calculator-builder-v2"])


def _redirect_editor(revision_id: int, text: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse(
        url=f"/calculator-builder/v2/{int(revision_id)}?"
        + urlencode({"notice": text}),
        status_code=303,
    )


def _json_object(raw: str, title: str) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or ""))
    except json.JSONDecodeError as error:
        raise RuleEditorV2Error(
            f"{title}: некорректный JSON ({error.msg})"
        ) from error
    if not isinstance(value, dict):
        raise RuleEditorV2Error(f"{title}: ожидается JSON-объект")
    return value


def _source_links(rules: dict[str, Any], source_ids: object) -> str:
    registry = source_registry(rules)
    if not isinstance(source_ids, list):
        return "<span class='muted'>Источник не привязан</span>"
    pieces: list[str] = []
    for source_id in source_ids:
        sid = str(source_id or "").strip()
        metadata = registry.get(sid)
        if not metadata:
            pieces.append(f"<span class='bad'>⚠ {escape(sid)}</span>")
            continue
        title = escape(str(metadata.get("title") or sid))
        authority = escape(str(metadata.get("authority") or ""))
        url = str(metadata.get("url") or "").strip()
        if url:
            pieces.append(
                f"<a class='source' href='{escape(url, quote=True)}' "
                f"target='_blank' rel='noopener noreferrer'>{title}</a>"
                + (f"<small>{authority}</small>" if authority else "")
            )
        else:
            doc = escape(str(metadata.get("document_ref") or "внутренний документ"))
            pieces.append(
                f"<span class='source internal'>{title}</span><small>{authority} · {doc}</small>"
            )
    return "".join(pieces) or "<span class='muted'>Источник не привязан</span>"


def _source_fields(prefix: str = "") -> str:
    p = escape(prefix, quote=True)
    return f"""
      <div class="source-entry">
        <label>ID источника<input name="{p}source_id" required placeholder="например FZ214_ART6"></label>
        <label>Название<input name="{p}source_title" placeholder="можно оставить пустым при использовании существующего ID"></label>
        <label>Орган / владелец<input name="{p}source_authority"></label>
        <label class="wide">HTTPS-ссылка<input type="url" name="{p}source_url" placeholder="https://..."></label>
        <label class="wide">Внутренний документ<input name="{p}source_document_ref" placeholder="если внешней ссылки нет"></label>
      </div>
    """


async def _save_mutation(
    *,
    db: AsyncSession,
    revision: CalculationRuleRevision,
    actor,
    expected_updated_at: str,
    mutate: Callable[[dict[str, Any]], dict[str, Any]],
) -> CalculationRuleRevision:
    if str(revision.status).upper() != "DRAFT":
        raise RuleEditorV2Error(
            "APPROVED/RETIRED-редакция неизменяема. Создайте новую DRAFT на её основе."
        )
    rules = mutate(dict(revision.rules or {}))
    return await CalculationRuleRevisionService(db).update_draft(
        revision_id=int(revision.id),
        effective_from=revision.effective_from,
        effective_to=revision.effective_to,
        rules=rules,
        note=revision.note,
        expected_updated_at=expected_updated_at,
        actor_type=str(actor.role),
        actor_id=int(actor.account_id),
    )


def _delete_form(
    revision: CalculationRuleRevision,
    *,
    section: str,
    code: str = "",
    participant_type: str = "",
    label: str = "Очистить",
) -> str:
    return f"""
    <form class="inline" method="post" action="/calculator-builder/v2/{revision.id}/delete">
      <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
      <input type="hidden" name="section" value="{escape(section, quote=True)}">
      <input type="hidden" name="code" value="{escape(code, quote=True)}">
      <input type="hidden" name="participant_type" value="{escape(participant_type, quote=True)}">
      <button class="danger" type="submit">{escape(label)}</button>
    </form>
    """


def _core_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    period = rules.get("period") if isinstance(rules.get("period"), dict) else {}
    standard = (
        rules.get("standard_object")
        if isinstance(rules.get("standard_object"), dict)
        else {}
    )
    base_rate = (
        standard.get("base_rate")
        if isinstance(standard.get("base_rate"), dict)
        else {}
    )
    rounding = (
        rules.get("rounding") if isinstance(rules.get("rounding"), dict) else {}
    )
    editable = str(revision.status).upper() == "DRAFT"
    actions = ""
    if editable:
        actions = f"""
        <details><summary>Изменить базовые параметры</summary>
        <form method="post" action="/calculator-builder/v2/{revision.id}/core">
          <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
          <label>Раздел<select name="section">
            <option value="period">Период просрочки</option>
            <option value="standard_formula">Базовая формула</option>
            <option value="base_rate">Источник ставки</option>
            <option value="rounding">Округление</option>
          </select></label>
          <label class="wide">Поля раздела (JSON)<textarea name="values_json" rows="7" required>{{}}</textarea></label>
          {_source_fields()}
          <button type="submit">Сохранить раздел и источник</button>
        </form>
        <div class="row">
          {_delete_form(revision, section="period", label="Обнулить период")}
          {_delete_form(revision, section="standard_formula", label="Обнулить формулу")}
          {_delete_form(revision, section="base_rate", label="Обнулить источник ставки")}
          {_delete_form(revision, section="rounding", label="Обнулить округление")}
        </div>
        </details>
        """
    return f"""
    <section class="panel">
      <h2>1. Базовая формула и период</h2>
      <div class="grid facts">
        <div><b>Формула</b><span>{escape(str(rules.get("formula_code") or "—"))}</span></div>
        <div><b>Делитель</b><span>{escape(str(standard.get("divisor") or "—"))}</span></div>
        <div><b>Начало</b><span>{escape(str(period.get("start") or "—"))}</span></div>
        <div><b>Окончание при передаче</b><span>{escape(str(period.get("end_if_transferred") or "—"))}</span></div>
        <div><b>Ставка определяется</b><span>{escape(str(base_rate.get("basis") or "—"))}</span></div>
        <div><b>Округление</b><span>{escape(str(rounding.get("money_quant") or "—"))} · {escape(str(rounding.get("mode") or "—"))}</span></div>
      </div>
      <h3>Правовые основания периода</h3>{_source_links(rules, period.get("source_ids"))}
      <h3>Правовые основания формулы</h3>{_source_links(rules, standard.get("source_ids"))}
      <h3>Основания выбора ставки</h3>{_source_links(rules, base_rate.get("source_ids"))}
      <h3>Основание округления</h3>{_source_links(rules, rounding.get("source_ids"))}
      {actions}
    </section>
    """


def _rate_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    standard = rules.get("standard_object") if isinstance(rules.get("standard_object"), dict) else {}
    rows = list(standard.get("rate_schedule") or [])
    body = []
    for row in rows:
        code = str(row.get("code") or "")
        body.append(
            "<tr>"
            f"<td>{escape(str(row.get('start') or ''))}</td>"
            f"<td>{escape(str(row.get('rate') or ''))}</td>"
            f"<td>{_source_links(rules, row.get('source_ids'))}</td>"
            + (
                f"<td>{_delete_form(revision, section='rate', code=code, label='Удалить')}</td>"
                if str(revision.status).upper() == "DRAFT"
                else ""
            )
            + "</tr>"
        )
    add = ""
    if str(revision.status).upper() == "DRAFT":
        add = f"""
        <details><summary>Добавить / заменить ставку</summary>
        <form method="post" action="/calculator-builder/v2/{revision.id}/rate">
          <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
          <label>Код<input name="code" required placeholder="CBR-YYYY-MM-DD"></label>
          <label>Действует с<input type="date" name="start" required></label>
          <label>Ставка в долях<input name="rate" required placeholder="0.14"></label>
          {_source_fields()}
          <button type="submit">Сохранить ставку и источник</button>
        </form></details>
        """
    return f"""
    <section class="panel">
      <h2>2. Справочник ставок Банка России</h2>
      <p>Используется значение, действующее на договорную дату исполнения. Источник закреплён за каждой строкой.</p>
      <div class="table-wrap"><table><thead><tr><th>С даты</th><th>Ставка</th><th>Источник</th>{'<th></th>' if str(revision.status).upper() == 'DRAFT' else ''}</tr></thead>
      <tbody>{''.join(body) or '<tr><td colspan="4">Ставок нет — APPROVED невозможен.</td></tr>'}</tbody></table></div>
      {add}
    </section>
    """


def _period_rows(
    revision: CalculationRuleRevision,
    rules: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    delete_section: str,
    cap: bool = False,
) -> str:
    items: list[str] = []
    for row in rows:
        code = str(row.get("code") or "")
        value = f" · cap {escape(str(row.get('cap_rate')))}" if cap else ""
        items.append(
            f"<article class='mini'><b>{escape(code)}</b>"
            f"<div>{escape(str(row.get('start') or ''))} — {escape(str(row.get('end') or ''))}{value}</div>"
            f"<div>{_source_links(rules, row.get('source_ids'))}</div>"
            + (
                _delete_form(revision, section=delete_section, code=code, label="Удалить значение")
                if str(revision.status).upper() == "DRAFT"
                else ""
            )
            + "</article>"
        )
    return "".join(items) or "<div class='empty'>Нет значений.</div>"


def _periods_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    standard = rules.get("standard_object") if isinstance(rules.get("standard_object"), dict) else {}
    unique = rules.get("unique_object") if isinstance(rules.get("unique_object"), dict) else {}
    editable = str(revision.status).upper() == "DRAFT"
    add = ""
    if editable:
        add = f"""
        <details><summary>Добавить / заменить период</summary>
        <form method="post" action="/calculator-builder/v2/{revision.id}/period">
          <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
          <label>Раздел<select name="section">
            <option value="excluded_periods">Мораторий / исключение стандартной ветки</option>
            <option value="rate_cap_periods">Ограничение ставки</option>
            <option value="unique_excluded_periods">Исключение уникального объекта</option>
          </select></label>
          <label>Код<input name="code" required></label>
          <label>С<input type="date" name="start" required></label>
          <label>По<input type="date" name="end" required></label>
          <label>cap_rate (только для ограничения)<input name="numeric_value" placeholder="0.075"></label>
          {_source_fields()}
          <button type="submit">Сохранить период и источник</button>
        </form></details>
        """
    return f"""
    <section class="panel">
      <h2>3. Моратории и исключаемые периоды</h2>
      {_period_rows(revision, rules, rows=list(standard.get('excluded_periods') or []), delete_section='excluded_period')}
      <h2>4. Ограничения ставки</h2>
      {_period_rows(revision, rules, rows=list(standard.get('rate_cap_periods') or []), delete_section='rate_cap', cap=True)}
      <h2>Уникальный объект — отдельные исключения</h2>
      {_period_rows(revision, rules, rows=list(unique.get('excluded_periods') or []), delete_section='unique_excluded_period')}
      {add}
    </section>
    """


def _participants_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    standard = rules.get("standard_object") if isinstance(rules.get("standard_object"), dict) else {}
    participants = standard.get("participant_types") if isinstance(standard.get("participant_types"), dict) else {}
    items = []
    for key, item in participants.items():
        if not isinstance(item, dict):
            continue
        items.append(
            f"<article class='mini'><b>{escape(str(item.get('label') or key))}</b>"
            f"<div>Коэффициент: {escape(str(item.get('multiplier') or '—'))}</div>"
            f"<div>{_source_links(rules, item.get('source_ids'))}</div>"
            + (
                _delete_form(revision, section="participant", participant_type=str(key), label="Обнулить тип клиента")
                if str(revision.status).upper() == "DRAFT"
                else ""
            )
            + "</article>"
        )
    add = ""
    if str(revision.status).upper() == "DRAFT":
        add = f"""
        <details><summary>Добавить / изменить тип клиента</summary>
        <form method="post" action="/calculator-builder/v2/{revision.id}/participant">
          <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
          <label>Тип<select name="participant_type"><option value="consumer_individual">Физлицо</option><option value="other">Иной</option></select></label>
          <label>Название<input name="label" required></label>
          <label>Коэффициент<input name="multiplier" required placeholder="2"></label>
          {_source_fields()}
          <button type="submit">Сохранить тип и источник</button>
        </form></details>
        """
    return f"<section class='panel'><h2>5. Тип клиента</h2>{''.join(items) or '<div class=empty>Нет типов.</div>'}{add}</section>"


def _unique_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    unique = rules.get("unique_object") if isinstance(rules.get("unique_object"), dict) else {}
    edit = ""
    if str(revision.status).upper() == "DRAFT":
        edit = f"""
        <details><summary>Изменить параметры уникального объекта</summary>
        <form method="post" action="/calculator-builder/v2/{revision.id}/unique">
          <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
          <label>ДДУ заключён до<input type="date" name="ddu_signed_before" value="{escape(str(unique.get('ddu_signed_before') or ''), quote=True)}" required></label>
          <label>Макс. просрочка, месяцев<input name="maximum_delay_months" value="{escape(str(unique.get('maximum_delay_months') or ''), quote=True)}" required></label>
          <label>Делитель<input name="divisor" value="{escape(str(unique.get('divisor') or ''), quote=True)}" required></label>
          <label>Коэффициент<input name="multiplier" value="{escape(str(unique.get('multiplier') or ''), quote=True)}" required></label>
          <label>Лимит от цены ДДУ<input name="maximum_penalty_share" value="{escape(str(unique.get('maximum_penalty_share_of_contract_price') or ''), quote=True)}" required></label>
          {_source_fields()}
          <button type="submit">Сохранить ветку и источник</button>
        </form>
        {_delete_form(revision, section="unique_object", label="Обнулить ветку уникального объекта")}
        </details>
        """
    return f"""
    <section class="panel"><h2>6. Уникальный объект</h2>
      <div class="grid facts">
        <div><b>ДДУ заключён до</b><span>{escape(str(unique.get('ddu_signed_before') or '—'))}</span></div>
        <div><b>Макс. просрочка</b><span>{escape(str(unique.get('maximum_delay_months') or '—'))} мес.</span></div>
        <div><b>Делитель</b><span>{escape(str(unique.get('divisor') or '—'))}</span></div>
        <div><b>Коэффициент</b><span>{escape(str(unique.get('multiplier') or '—'))}</span></div>
        <div><b>Лимит</b><span>{escape(str(unique.get('maximum_penalty_share_of_contract_price') or '—'))}</span></div>
      </div>
      {_source_links(rules, unique.get('source_ids'))}{edit}
    </section>
    """


def _manual_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    rows = list(rules.get("manual_review_conditions") or [])
    items = []
    for row in rows:
        code = str(row.get("code") or "")
        items.append(
            f"<article class='mini'><b>{escape(code)}</b><div>{escape(str(row.get('label') or ''))}</div>"
            f"<div>{_source_links(rules, row.get('source_ids'))}</div>"
            + (
                _delete_form(revision, section="manual_review", code=code, label="Удалить стоп-фактор")
                if str(revision.status).upper() == "DRAFT"
                else ""
            )
            + "</article>"
        )
    add = ""
    if str(revision.status).upper() == "DRAFT":
        add = f"""
        <details><summary>Добавить / заменить стоп-фактор</summary>
        <form method="post" action="/calculator-builder/v2/{revision.id}/manual">
          <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
          <label>Код<input name="code" required></label>
          <label class="wide">Когда требуется ручная проверка<textarea name="label" rows="2" required></textarea></label>
          {_source_fields()}
          <button type="submit">Сохранить стоп-фактор и источник</button>
        </form></details>
        """
    return f"<section class='panel'><h2>7. Условия ручной проверки</h2>{''.join(items) or '<div class=empty>Нет стоп-факторов.</div>'}{add}</section>"


def _examples_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    rows = list(rules.get("control_examples") or [])
    items = []
    for row in rows:
        code = str(row.get("code") or "")
        items.append(
            f"<article class='mini'><b>{escape(str(row.get('title') or code))}</b>"
            f"<pre>{escape(json.dumps({'input': row.get('input'), 'expected': row.get('expected')}, ensure_ascii=False, indent=2))}</pre>"
            f"<div>{_source_links(rules, row.get('source_ids'))}</div>"
            + (
                _delete_form(revision, section="control_example", code=code, label="Удалить пример")
                if str(revision.status).upper() == "DRAFT"
                else ""
            )
            + "</article>"
        )
    add = ""
    if str(revision.status).upper() == "DRAFT":
        add = f"""
        <details><summary>Добавить / заменить контрольный пример</summary>
        <form method="post" action="/calculator-builder/v2/{revision.id}/example">
          <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
          <label>Код<input name="code" required></label>
          <label>Название<input name="title" required></label>
          <label class="wide">Вход (JSON)<textarea name="input_json" rows="7" required>{{}}</textarea></label>
          <label class="wide">Ожидаемый результат (JSON)<textarea name="expected_json" rows="7" required>{{}}</textarea></label>
          {_source_fields()}
          <button type="submit">Сохранить контрольный пример</button>
        </form></details>
        """
    return f"<section class='panel'><h2>8. Контрольные примеры</h2>{''.join(items) or '<div class=empty>Нет контрольных примеров — APPROVED не рекомендуется.</div>'}{add}</section>"


def _sources_card(revision: CalculationRuleRevision, rules: dict[str, Any]) -> str:
    registry = source_registry(rules)
    items = []
    for source_id, source in sorted(registry.items()):
        url = str(source.get("url") or "")
        doc = str(source.get("document_ref") or "")
        view = (
            f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>Открыть источник ↗</a>"
            if url
            else f"<span class='muted'>{escape(doc)}</span>"
        )
        form = ""
        if str(revision.status).upper() == "DRAFT":
            form = f"""
            <details><summary>Изменить карточку источника</summary>
            <form method="post" action="/calculator-builder/v2/{revision.id}/source">
              <input type="hidden" name="expected_updated_at" value="{escape(revision.updated_at.isoformat(), quote=True)}">
              <input type="hidden" name="source_id" value="{escape(source_id, quote=True)}">
              <label>Название<input name="title" value="{escape(str(source.get('title') or ''), quote=True)}" required></label>
              <label>Орган / владелец<input name="authority" value="{escape(str(source.get('authority') or ''), quote=True)}" required></label>
              <label class="wide">HTTPS-ссылка<input name="url" value="{escape(url, quote=True)}"></label>
              <label class="wide">Внутренний документ<input name="document_ref" value="{escape(doc, quote=True)}"></label>
              <button type="submit">Сохранить источник</button>
            </form></details>
            """
        items.append(
            f"<article class='mini'><b>{escape(source_id)} · {escape(str(source.get('title') or ''))}</b>"
            f"<div>{escape(str(source.get('authority') or ''))}</div><div>{view}</div>{form}</article>"
        )
    return f"""
    <section class="panel"><h2>9. Реестр правовых источников</h2>
      <p>Источник существует только пока на него ссылается хотя бы один параметр. При очистке последнего значения карточка источника удаляется автоматически. APPROVED запрещает неизвестные и «висячие» источники.</p>
      {''.join(items) or '<div class=empty>Источников нет.</div>'}
    </section>
    """


@router.get("/calculator-builder/v2/{revision_id}", response_class=HTMLResponse)
async def calculator_v2_editor(
    revision_id: int,
    request: Request,
    notice: str | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        revision = await _revision(db, revision_id)
    except (DocumentAccessError, HTTPException) as error:
        recovery = _auth_recovery(error)
        if recovery is not None:
            return recovery
        raise

    rules = dict(revision.rules or {})
    if rules.get("schema_version") != 2:
        return _redirect_notice("Эта редакция использует старую schema_version и открывается в расширенном JSON-редакторе.")

    notice_html = f"<div class='notice'>{escape(notice)}</div>" if notice else ""
    status = escape(str(revision.status))
    immutable = (
        "<div class='notice approved'>Эта редакция неизменяема. Для новых значений создайте новую DRAFT на её основе.</div>"
        if str(revision.status).upper() != "DRAFT"
        else "<div class='notice'>Изменения относятся только к DRAFT. Клиентские расчёты используют исключительно APPROVED.</div>"
    )
    clone = f"""
    <form class="clone" method="post" action="/calculator-builder/v2/{revision.id}/clone">
      <label>Новый ключ<input name="revision_key" required placeholder="DDU-214FZ-YYYY-MM-DD"></label>
      <label>Действует с<input type="date" name="effective_from" value="{date.today().isoformat()}" required></label>
      <button type="submit">Создать новую DRAFT на основе этой редакции</button>
    </form>
    """
    html = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Редактор юридических правил v2</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--red:#b42318;--amber:#a15c00}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}}header{{background:#152038;color:#fff;padding:18px}}main,.head{{max-width:1180px;margin:auto}}.head{{display:flex;justify-content:space-between;gap:12px;align-items:center}}h1{{font-size:23px;margin:0}}h2{{font-size:19px;margin:0 0 10px}}h3{{font-size:13px;margin:14px 0 5px}}main{{padding:18px}}.panel,.notice,.clone{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:14px}}.notice{{background:#eef2ff;border-color:#c7d2fe}}.notice.approved{{background:#ecfdf3;border-color:#abefc6}}.meta,.muted,small{{color:var(--muted);font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.facts>div,.mini{{border:1px solid var(--line);border-radius:12px;padding:11px;background:#fbfcfe}}.facts b,.facts span{{display:block}}.facts span{{margin-top:4px;color:#475467}}.source{{display:inline-block;margin:4px 7px 2px 0;padding:6px 8px;border-radius:9px;background:#eef4ff;color:#2447b8;text-decoration:none}}.source.internal{{background:#f2f4f7;color:#344054}}small{{display:block;margin:0 0 5px}}details{{border-top:1px solid var(--line);margin-top:14px;padding-top:10px}}summary{{font-weight:800;cursor:pointer}}form:not(.inline){{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:10px}}label{{font-size:12px;font-weight:700;color:var(--muted)}}input,select,textarea{{display:block;width:100%;margin-top:4px;padding:9px;border:1px solid #d0d5dd;border-radius:9px;font:inherit}}.wide{{grid-column:1/-1}}button,a.btn{{border:0;border-radius:9px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}}button.danger{{background:#fff;color:var(--red);border:1px solid #fecdca}}.inline{{display:inline-block;margin:7px 6px 0 0}}.row{{display:flex;gap:6px;flex-wrap:wrap}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}.table-wrap{{overflow:auto}}pre{{white-space:pre-wrap;overflow:auto;background:#f8fafc;padding:9px;border-radius:9px;font-size:11px}}.source-entry{{grid-column:1/-1;display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px;border:1px dashed #b2c1ee;border-radius:10px}}.bad{{color:var(--red)}}@media(max-width:720px){{main{{padding:10px}}.grid,form:not(.inline),.source-entry{{grid-template-columns:1fr}}.wide{{grid-column:1}}.head{{align-items:flex-start;flex-direction:column}}}}
</style></head><body>
<header><div class="head"><div><h1>⚖️ Редактор юридических правил v2</h1><div>{escape(revision.revision_key)} · {status} · sha256 {escape(revision.rules_sha256[:12])}…</div></div><a class="btn" href="/calculator-builder/ui">← Все редакции</a></div></header>
<main>{notice_html}{immutable}{clone}
{_core_card(revision, rules)}
{_rate_card(revision, rules)}
{_periods_card(revision, rules)}
{_participants_card(revision, rules)}
{_unique_card(revision, rules)}
{_manual_card(revision, rules)}
{_examples_card(revision, rules)}
{_sources_card(revision, rules)}
<section class="panel"><h2>10. Жизненный цикл</h2><p>DRAFT можно менять и обнулять. APPROVED неизменяем: новый правовой источник или значение оформляется новой DRAFT. Исторические расчёты сохраняют полный snapshot правил и источников.</p></section>
</main></body></html>"""
    return HTMLResponse(html)


@router.post("/calculator-builder/v2/template")
async def create_approved_methodology_draft(
    request: Request,
    revision_key: str = Form(...),
    effective_from: str = Form(...),
    effective_to: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        revision = await CalculationRuleRevisionService(db).create_draft(
            revision_key=revision_key,
            effective_from=_parse_date(effective_from, field="effective_from"),
            effective_to=_parse_date(effective_to, field="effective_to", optional=True),
            rules=approved_v2_rule_template(),
            note=(
                "PM-016 v2: методология согласована юристом; каждая юридическая "
                "настройка связана с источником. Требуется отдельное APPROVED в системе."
            ),
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except Exception as error:
        await db.rollback()
        if isinstance(error, (CalculationRuleRevisionError, RuleEditorV2Error)):
            return _redirect_notice(f"DRAFT v2 не создан: {error}")
        raise
    return RedirectResponse(
        url=f"/calculator-builder/v2/{revision.id}",
        status_code=303,
    )


@router.post("/calculator-builder/v2/{revision_id}/clone")
async def clone_revision_as_draft(
    revision_id: int,
    request: Request,
    revision_key: str = Form(...),
    effective_from: str = Form(...),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor = await _admin(request, db, x_admin_token)
        source = await _revision(db, revision_id)
        revision = await CalculationRuleRevisionService(db).create_draft(
            revision_key=revision_key,
            effective_from=_parse_date(effective_from, field="effective_from"),
            effective_to=None,
            rules=dict(source.rules or {}),
            note=f"Новая редакция на основе {source.revision_key}; источники скопированы вместе со значениями.",
            actor_type=str(actor.role),
            actor_id=int(actor.account_id),
        )
        await db.commit()
    except Exception as error:
        await db.rollback()
        if isinstance(error, CalculationRuleRevisionError):
            return _redirect_editor(revision_id, f"Новая DRAFT не создана: {error}")
        raise
    return RedirectResponse(url=f"/calculator-builder/v2/{revision.id}", status_code=303)


async def _mutating_request(request, db, header_token, revision_id):
    actor = await _admin(request, db, header_token)
    revision = await _revision(db, revision_id)
    return actor, revision


@router.post("/calculator-builder/v2/{revision_id}/core")
async def edit_core(
    revision_id: int,
    request: Request,
    section: str = Form(...),
    values_json: str = Form(...),
    expected_updated_at: str = Form(...),
    source_id: str = Form(...),
    source_title: str = Form(default=""),
    source_authority: str = Form(default=""),
    source_url: str = Form(default=""),
    source_document_ref: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        values = _json_object(values_json, "Поля раздела")
        await _save_mutation(
            db=db, revision=revision, actor=actor,
            expected_updated_at=expected_updated_at,
            mutate=lambda rules: set_core_section(
                rules, section=section, values={k: str(v) for k, v in values.items()},
                source_id=source_id, source_title=source_title,
                source_authority=source_authority, source_url=source_url,
                source_document_ref=source_document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Изменение не сохранено: {error}")
    return _redirect_editor(revision_id, "Раздел и его источник сохранены.")


@router.post("/calculator-builder/v2/{revision_id}/rate")
async def edit_rate(
    revision_id: int, request: Request,
    code: str = Form(...), start: str = Form(...), rate: str = Form(...),
    expected_updated_at: str = Form(...),
    source_id: str = Form(...), source_title: str = Form(default=""),
    source_authority: str = Form(default=""), source_url: str = Form(default=""),
    source_document_ref: str = Form(default=""),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: upsert_rate(
                rules, code=code, start=start, rate=rate, source_id=source_id,
                source_title=source_title, source_authority=source_authority,
                source_url=source_url, source_document_ref=source_document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Ставка не сохранена: {error}")
    return _redirect_editor(revision_id, "Ставка и соответствующий источник сохранены.")


@router.post("/calculator-builder/v2/{revision_id}/period")
async def edit_period(
    revision_id: int, request: Request,
    section: str = Form(...), code: str = Form(...), start: str = Form(...),
    end: str = Form(...), numeric_value: str = Form(default=""),
    expected_updated_at: str = Form(...),
    source_id: str = Form(...), source_title: str = Form(default=""),
    source_authority: str = Form(default=""), source_url: str = Form(default=""),
    source_document_ref: str = Form(default=""),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: upsert_period(
                rules, section=section, code=code, start=start, end=end,
                numeric_value=numeric_value, source_id=source_id,
                source_title=source_title, source_authority=source_authority,
                source_url=source_url, source_document_ref=source_document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Период не сохранён: {error}")
    return _redirect_editor(revision_id, "Период и его правовой источник сохранены.")


@router.post("/calculator-builder/v2/{revision_id}/participant")
async def edit_participant(
    revision_id: int, request: Request,
    participant_type: str = Form(...), label: str = Form(...), multiplier: str = Form(...),
    expected_updated_at: str = Form(...),
    source_id: str = Form(...), source_title: str = Form(default=""),
    source_authority: str = Form(default=""), source_url: str = Form(default=""),
    source_document_ref: str = Form(default=""),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: set_participant_multiplier(
                rules, participant_type=participant_type, label=label,
                multiplier=multiplier, source_id=source_id,
                source_title=source_title, source_authority=source_authority,
                source_url=source_url, source_document_ref=source_document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Тип клиента не сохранён: {error}")
    return _redirect_editor(revision_id, "Тип клиента и источник сохранены.")


@router.post("/calculator-builder/v2/{revision_id}/unique")
async def edit_unique(
    revision_id: int, request: Request,
    ddu_signed_before: str = Form(...), maximum_delay_months: str = Form(...),
    divisor: str = Form(...), multiplier: str = Form(...),
    maximum_penalty_share: str = Form(...), expected_updated_at: str = Form(...),
    source_id: str = Form(...), source_title: str = Form(default=""),
    source_authority: str = Form(default=""), source_url: str = Form(default=""),
    source_document_ref: str = Form(default=""),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: set_unique_object_rule(
                rules, ddu_signed_before=ddu_signed_before,
                maximum_delay_months=maximum_delay_months, divisor=divisor,
                multiplier=multiplier, maximum_penalty_share=maximum_penalty_share,
                source_id=source_id, source_title=source_title,
                source_authority=source_authority, source_url=source_url,
                source_document_ref=source_document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Уникальный объект не сохранён: {error}")
    return _redirect_editor(revision_id, "Правило уникального объекта сохранено.")


@router.post("/calculator-builder/v2/{revision_id}/manual")
async def edit_manual(
    revision_id: int, request: Request,
    code: str = Form(...), label: str = Form(...), expected_updated_at: str = Form(...),
    source_id: str = Form(...), source_title: str = Form(default=""),
    source_authority: str = Form(default=""), source_url: str = Form(default=""),
    source_document_ref: str = Form(default=""),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: upsert_manual_review_condition(
                rules, code=code, label=label, source_id=source_id,
                source_title=source_title, source_authority=source_authority,
                source_url=source_url, source_document_ref=source_document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Стоп-фактор не сохранён: {error}")
    return _redirect_editor(revision_id, "Условие ручной проверки сохранено.")


@router.post("/calculator-builder/v2/{revision_id}/example")
async def edit_example(
    revision_id: int, request: Request,
    code: str = Form(...), title: str = Form(...),
    input_json: str = Form(...), expected_json: str = Form(...),
    expected_updated_at: str = Form(...),
    source_id: str = Form(...), source_title: str = Form(default=""),
    source_authority: str = Form(default=""), source_url: str = Form(default=""),
    source_document_ref: str = Form(default=""),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        input_data = _json_object(input_json, "Вход")
        expected = _json_object(expected_json, "Ожидаемый результат")
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: upsert_control_example(
                rules, code=code, title=title, input_data=input_data,
                expected=expected, source_id=source_id,
                source_title=source_title, source_authority=source_authority,
                source_url=source_url, source_document_ref=source_document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Контрольный пример не сохранён: {error}")
    return _redirect_editor(revision_id, "Контрольный пример сохранён.")


@router.post("/calculator-builder/v2/{revision_id}/source")
async def edit_source(
    revision_id: int, request: Request,
    source_id: str = Form(...), title: str = Form(...), authority: str = Form(...),
    url: str = Form(default=""), document_ref: str = Form(default=""),
    expected_updated_at: str = Form(...),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: update_source_card(
                rules, source_id=source_id, title=title, authority=authority,
                url=url, document_ref=document_ref,
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Источник не сохранён: {error}")
    return _redirect_editor(revision_id, "Карточка источника обновлена во всех связанных правилах.")


@router.post("/calculator-builder/v2/{revision_id}/delete")
async def clear_rule_value(
    revision_id: int, request: Request,
    section: str = Form(...), code: str = Form(default=""),
    participant_type: str = Form(default=""), expected_updated_at: str = Form(...),
    db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None),
):
    try:
        actor, revision = await _mutating_request(request, db, x_admin_token, revision_id)
        await _save_mutation(
            db=db, revision=revision, actor=actor, expected_updated_at=expected_updated_at,
            mutate=lambda rules: delete_item(
                rules, section=section, code=code, participant_type=participant_type
            ),
        )
        await db.commit()
    except (RuleEditorV2Error, CalculationRuleRevisionError) as error:
        await db.rollback()
        return _redirect_editor(revision_id, f"Значение не очищено: {error}")
    return _redirect_editor(
        revision_id,
        "Значение удалено. Источник удалён автоматически, если больше ничего не подтверждает.",
    )


__all__ = ["router"]
