from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.db.session import get_db
from app.domain.cases.assignment_policy import automatic_assignment_required
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.service_contract import SERVICE_CONTRACT_TYPE
from app.domain.documents.document_service import document_is_usable
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User

router = APIRouter(tags=["admin-workdesk-integrity"])

MAX_SCAN_CASES = 5000
TERMINAL_CASE_STATUSES = {
    CaseStatus.M1_CLOSED.value,
    CaseStatus.M2_CLOSED.value,
    CaseStatus.ARCHIVED.value,
}
TERMINAL_CONSULTATION_STATUSES = {
    ConsultationStatus.DONE.value,
    ConsultationStatus.CLIENT_NO_SHOW.value,
    ConsultationStatus.LAWYER_NO_SHOW.value,
    ConsultationStatus.CANCELLED.value,
    ConsultationStatus.CLOSED.value,
    ConsultationStatus.RESCHEDULED.value,
}
OPEN_PAYMENT_STATUSES = {
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_CONFIRMATION.value,
    PaymentStatus.PAID_REVIEW.value,
}
TERMINAL_CASE_PAYMENT_ATTENTION = OPEN_PAYMENT_STATUSES | {
    PaymentStatus.REFUND_PENDING.value,
}
PROOF_ACTIONS = {
    "CLIENT_SERVICE_CONTRACT_CONFIRMED",
    "M1_POA_RECEIVED_CONFIRMED",
    "M1_COURT_DECISION_RECORDED",
}

WAITING_PAYMENT_CODES = {
    CaseStatus.M1_WAITING_PAYMENT_30000.value: PaymentCode.M1_INITIAL_PAYMENT.value,
    CaseStatus.M1_WAITING_PAYMENT_70000.value: PaymentCode.M1_COURT_PAYMENT.value,
    CaseStatus.M1_WAITING_SUCCESS_FEE.value: PaymentCode.M1_SUCCESS_FEE.value,
    CaseStatus.M2_PAYMENT_PENDING.value: PaymentCode.M2_CONSULTATION_PAYMENT.value,
}

M1_CONTRACT_PROOF_REQUIRED = {
    CaseStatus.M1_WAITING_PAYMENT_30000.value,
    CaseStatus.M1_PAYMENT_30000_RECEIVED.value,
    CaseStatus.M1_POWER_OF_ATTORNEY.value,
    CaseStatus.M1_POA_RECEIVED.value,
    CaseStatus.M1_CLAIM_PREPARATION.value,
    CaseStatus.M1_CLAIM_SENT.value,
    CaseStatus.M1_WAITING_30_DAYS.value,
    CaseStatus.M1_COURT_STAGE.value,
    CaseStatus.M1_WAITING_PAYMENT_70000.value,
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value,
    CaseStatus.M1_ENFORCEMENT.value,
    CaseStatus.M1_MONEY_RECEIVED.value,
    CaseStatus.M1_WAITING_SUCCESS_FEE.value,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
}
M1_INITIAL_PAYMENT_REQUIRED = M1_CONTRACT_PROOF_REQUIRED - {
    CaseStatus.M1_WAITING_PAYMENT_30000.value,
}
M1_POA_PROOF_REQUIRED = {
    CaseStatus.M1_POA_RECEIVED.value,
    CaseStatus.M1_CLAIM_PREPARATION.value,
    CaseStatus.M1_CLAIM_SENT.value,
    CaseStatus.M1_WAITING_30_DAYS.value,
    CaseStatus.M1_COURT_STAGE.value,
    CaseStatus.M1_WAITING_PAYMENT_70000.value,
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value,
    CaseStatus.M1_ENFORCEMENT.value,
    CaseStatus.M1_MONEY_RECEIVED.value,
    CaseStatus.M1_WAITING_SUCCESS_FEE.value,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
}
M1_COURT_DECISION_REQUIRED = {
    CaseStatus.M1_WAITING_PAYMENT_70000.value,
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value,
    CaseStatus.M1_ENFORCEMENT.value,
    CaseStatus.M1_MONEY_RECEIVED.value,
    CaseStatus.M1_WAITING_SUCCESS_FEE.value,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
}
M1_COURT_PAYMENT_REQUIRED = M1_COURT_DECISION_REQUIRED - {
    CaseStatus.M1_WAITING_PAYMENT_70000.value,
}
M1_SUCCESS_FEE_PAID_REQUIRED = {
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value,
}
M2_CASES_EXPECTING_CONSULTATION = {
    CaseStatus.M2_DESCRIPTION_PENDING.value,
    CaseStatus.M2_DOCUMENTS_OPTIONAL.value,
    CaseStatus.M2_SLOT_PENDING.value,
    CaseStatus.M2_PAYMENT_PENDING.value,
    CaseStatus.M2_CONSULTATION_BOOKED.value,
    CaseStatus.M2_CONSULTATION_DONE.value,
}

_SEVERITY_RANK = {"critical": 0, "warning": 1}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_status_label(status: object) -> str:
    try:
        return get_client_visible_status(str(status))
    except Exception:
        return str(status or "Неизвестный статус")


def _add_issue(
    target: list[dict[str, object]],
    *,
    code: str,
    severity: str,
    title: str,
    detail: str,
    action_label: str | None = None,
    action_href: str | None = None,
    action_kind: str | None = None,
) -> None:
    if any(item.get("code") == code for item in target):
        return
    target.append(
        {
            "code": code,
            "severity": severity,
            "title": title,
            "detail": detail,
            "action_label": action_label,
            "action_href": action_href,
            "action_kind": action_kind,
        }
    )


def _latest(items):
    return items[-1] if items else None


def _latest_payment(payments: list[Payment], code: str) -> Payment | None:
    matching = [item for item in payments if str(item.payment_code) == str(code)]
    return _latest(matching)


def _has_paid(payments: list[Payment], code: str) -> bool:
    return any(
        str(item.payment_code) == str(code)
        and str(item.status) == PaymentStatus.PAID.value
        for item in payments
    )


def _current_contract(documents: list[Document]) -> Document | None:
    candidates = [
        item
        for item in documents
        if str(item.document_type) == SERVICE_CONTRACT_TYPE
        and str(item.status) == DocumentStatus.APPROVED.value
        and document_is_usable(item)
    ]
    candidates.sort(key=lambda item: (int(item.version or 0), int(item.id or 0)))
    return _latest(candidates)


def _open_service_contracts(documents: list[Document]) -> list[Document]:
    return [
        item
        for item in documents
        if str(item.document_type) == SERVICE_CONTRACT_TYPE
        and str(item.status) != DocumentStatus.ARCHIVED.value
    ]


def _active_consultations(items: list[Consultation]) -> list[Consultation]:
    return [
        item
        for item in items
        if str(item.status) not in TERMINAL_CONSULTATION_STATUSES
    ]


def _same_moment(left: datetime | None, right: datetime | None) -> bool:
    left_utc = _as_utc(left)
    right_utc = _as_utc(right)
    if left_utc is None or right_utc is None:
        return left_utc is right_utc
    return abs((left_utc - right_utc).total_seconds()) <= 1


def _action_for_case(case_id: int, area: str) -> tuple[str | None, str | None, str | None]:
    if area == "technical":
        return "Разобрать технически", f"/admin/technical-cases/ui?case_id={case_id}", None
    if area == "documents":
        return "Проверить материалы", f"/document-access/ui?case_id={case_id}", None
    if area == "contract":
        return "Открыть договор", f"/contracts/ui?case_id={case_id}", None
    if area == "consultation":
        return (
            "Проверить консультацию",
            f"/admin/workdesk/cases/{case_id}/action/consultation",
            None,
        )
    if area == "refund":
        return "Открыть возвраты", f"/admin/refunds/ui?case_id={case_id}", None
    if area == "payment":
        return "Проверить финансы", f"/admin/payment-reviews/ui?case_id={case_id}", None
    if area == "messages":
        return "Открыть переписку", f"/message-center/ui?case_id={case_id}", None
    if area == "assign":
        return "Назначить юриста", None, "assign"
    return "Открыть карточку", None, "case"


def _issue_with_action(
    target: list[dict[str, object]],
    *,
    case_id: int,
    code: str,
    severity: str,
    title: str,
    detail: str,
    area: str,
) -> None:
    label, href, kind = _action_for_case(case_id, area)
    _add_issue(
        target,
        code=code,
        severity=severity,
        title=title,
        detail=detail,
        action_label=label,
        action_href=href,
        action_kind=kind,
    )


async def _candidate_case_rows(db: AsyncSession):
    active_rows = list(
        (
            await db.execute(
                select(Case, User)
                .join(User, User.id == Case.client_id)
                .where(Case.status.notin_(tuple(TERMINAL_CASE_STATUSES)))
                .order_by(Case.updated_at.asc(), Case.id.asc())
                .limit(MAX_SCAN_CASES + 1)
            )
        ).all()
    )
    truncated = len(active_rows) > MAX_SCAN_CASES
    active_rows = active_rows[:MAX_SCAN_CASES]

    terminal_payment_ids = set(
        int(value)
        for value in (
            await db.execute(
                select(Payment.case_id)
                .join(Case, Case.id == Payment.case_id)
                .where(Case.status.in_(tuple(TERMINAL_CASE_STATUSES)))
                .where(Payment.status.in_(tuple(TERMINAL_CASE_PAYMENT_ATTENTION)))
                .distinct()
            )
        ).scalars().all()
    )
    terminal_consultation_ids = set(
        int(value)
        for value in (
            await db.execute(
                select(Consultation.case_id)
                .join(Case, Case.id == Consultation.case_id)
                .where(Case.status.in_(tuple(TERMINAL_CASE_STATUSES)))
                .where(
                    Consultation.status.notin_(
                        tuple(TERMINAL_CONSULTATION_STATUSES)
                    )
                )
                .distinct()
            )
        ).scalars().all()
    )
    terminal_ids = terminal_payment_ids | terminal_consultation_ids
    terminal_rows = []
    if terminal_ids:
        terminal_rows = list(
            (
                await db.execute(
                    select(Case, User)
                    .join(User, User.id == Case.client_id)
                    .where(Case.id.in_(terminal_ids))
                    .order_by(Case.updated_at.asc(), Case.id.asc())
                )
            ).all()
        )

    rows_by_id = {int(case.id): (case, user) for case, user in active_rows}
    for case, user in terminal_rows:
        rows_by_id[int(case.id)] = (case, user)
    return list(rows_by_id.values()), truncated


async def build_workdesk_integrity(db: AsyncSession) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    rows, truncated = await _candidate_case_rows(db)
    if not rows:
        return {
            "count": 0,
            "critical_count": 0,
            "warning_count": 0,
            "scanned_cases": 0,
            "truncated": truncated,
            "items": [],
            "generated_at": now.isoformat(),
        }

    cases = [case for case, _user in rows]
    users = {int(case.id): user for case, user in rows}
    case_ids = [int(case.id) for case in cases]

    documents_by_case: dict[int, list[Document]] = defaultdict(list)
    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id.in_(case_ids))
                .order_by(Document.created_at.asc(), Document.id.asc())
            )
        ).scalars().all()
    )
    for document in documents:
        documents_by_case[int(document.case_id)].append(document)

    payments_by_case: dict[int, list[Payment]] = defaultdict(list)
    payments = list(
        (
            await db.execute(
                select(Payment)
                .where(Payment.case_id.in_(case_ids))
                .order_by(Payment.created_at.asc(), Payment.id.asc())
            )
        ).scalars().all()
    )
    for payment in payments:
        payments_by_case[int(payment.case_id)].append(payment)

    consultations_by_case: dict[int, list[Consultation]] = defaultdict(list)
    consultations = list(
        (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id.in_(case_ids))
                .order_by(Consultation.created_at.asc(), Consultation.id.asc())
            )
        ).scalars().all()
    )
    slot_ids = set()
    lawyer_ids = {
        int(case.assigned_lawyer_id)
        for case in cases
        if case.assigned_lawyer_id is not None
    }
    for consultation in consultations:
        consultations_by_case[int(consultation.case_id)].append(consultation)
        if consultation.slot_id is not None:
            slot_ids.add(int(consultation.slot_id))
        if consultation.lawyer_id is not None:
            lawyer_ids.add(int(consultation.lawyer_id))

    slots: dict[int, ConsultationSlot] = {}
    if slot_ids:
        slot_rows = list(
            (
                await db.execute(
                    select(ConsultationSlot).where(ConsultationSlot.id.in_(slot_ids))
                )
            ).scalars().all()
        )
        slots = {int(item.id): item for item in slot_rows}

    lawyers: dict[int, Lawyer] = {}
    if lawyer_ids:
        lawyer_rows = list(
            (
                await db.execute(select(Lawyer).where(Lawyer.id.in_(lawyer_ids)))
            ).scalars().all()
        )
        lawyers = {int(item.id): item for item in lawyer_rows}

    audit_by_case_action: dict[tuple[int, str], AuditLog] = {}
    audit_rows = list(
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "case")
                .where(AuditLog.entity_id.in_(case_ids))
                .where(AuditLog.action.in_(tuple(PROOF_ACTIONS)))
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()
    )
    for event in audit_rows:
        if event.entity_id is not None:
            audit_by_case_action[(int(event.entity_id), str(event.action))] = event

    output: list[dict[str, object]] = []
    for case in cases:
        case_id = int(case.id)
        status = str(case.status)
        route = str(case.route or "")
        case_documents = documents_by_case.get(case_id, [])
        case_payments = payments_by_case.get(case_id, [])
        case_consultations = consultations_by_case.get(case_id, [])
        active_consultations = _active_consultations(case_consultations)
        consultation = _latest(case_consultations)
        issues: list[dict[str, object]] = []

        if status in TERMINAL_CASE_STATUSES:
            if case.closed_at is None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="terminal_without_closed_at",
                    severity="warning",
                    title="Закрытый статус без отметки времени закрытия",
                    detail="Статус терминальный, но closed_at не заполнен. Архив и сроки хранения могут считаться неверно.",
                    area="case",
                )
            live_payments = [
                item
                for item in case_payments
                if str(item.status) in TERMINAL_CASE_PAYMENT_ATTENTION
            ]
            for payment in live_payments:
                if str(payment.status) == PaymentStatus.REFUND_PENDING.value:
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="terminal_refund_pending",
                        severity="warning",
                        title="Закрытое дело ждёт фактического возврата",
                        detail=f"Платёж #{payment.id} находится в REFUND_PENDING. Закрытие дела не должно скрывать финансовую очередь.",
                        area="refund",
                    )
                else:
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="terminal_live_payment",
                        severity="critical",
                        title="У закрытого дела остался активный платёж",
                        detail=f"Платёж #{payment.id} имеет статус {payment.status}; терминальное дело не должно оставлять оплачиваемую или проверяемую обязанность без решения.",
                        area="payment",
                    )
            if active_consultations:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="terminal_live_consultation",
                    severity="critical",
                    title="У закрытого дела осталась незавершённая консультация",
                    detail=f"Консультация #{active_consultations[-1].id} имеет статус {active_consultations[-1].status}. Сначала завершите или отмените записной контур.",
                    area="consultation",
                )
        else:
            if case.closed_at is not None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="active_case_has_closed_at",
                    severity="critical",
                    title="Активное дело помечено как закрытое",
                    detail="Статус активный, но closed_at заполнен. Это создаёт конфликт для архива, retention и клиентского кабинета.",
                    area="case",
                )
            if case.content_deleted_at is not None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="active_case_content_deleted",
                    severity="critical",
                    title="Активное дело уже имеет отметку удаления содержимого",
                    detail="Такое дело нельзя безопасно продолжать: материалы могли быть уничтожены по retention-процессу.",
                    area="technical",
                )

        if status == CaseStatus.ERROR.value:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="technical_error",
                severity="critical",
                title="Дело остановлено технической защитой",
                detail="ERROR не является бизнес-этапом. Восстанавливайте только последний подтверждённый аудитом безопасный статус.",
                area="technical",
            )

        if status.startswith("M1_") and route != "M1":
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_route_status_mismatch",
                severity="critical",
                title="M1-статус не совпадает с маршрутом дела",
                detail=f"Статус {status} требует route=M1, фактически route={route or 'не задан'}.",
                area="case",
            )
        if status.startswith("M2_") and route != "M2":
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m2_route_status_mismatch",
                severity="critical",
                title="M2-статус не совпадает с маршрутом дела",
                detail=f"Статус {status} требует route=M2, фактически route={route or 'не задан'}.",
                area="case",
            )

        if automatic_assignment_required(status):
            if case.assigned_lawyer_id is None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m1_assignment_missing",
                    severity="warning",
                    title="На рабочем M1-этапе нет ответственного юриста",
                    detail="После передачи документов M1 должен иметь назначенного ответственного до дальнейших юридических действий.",
                    area="assign",
                )
            else:
                assigned = lawyers.get(int(case.assigned_lawyer_id))
                if assigned is None or not assigned.is_active:
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="m1_assignment_inactive",
                        severity="critical",
                        title="Дело назначено отсутствующему или неактивному юристу",
                        detail="Ответственный не может безопасно выполнять действия, SLA и доступ к материалам требуют переназначения.",
                        area="case",
                    )

        non_archived_documents = [
            item
            for item in case_documents
            if str(item.status) != DocumentStatus.ARCHIVED.value
        ]
        on_review = [
            item
            for item in non_archived_documents
            if str(item.status) == DocumentStatus.ON_REVIEW.value
        ]
        if status == CaseStatus.M1_DOCUMENTS_RECEIVED.value and not on_review:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_received_without_review_documents",
                severity="critical",
                title="Документы отмечены полученными, но пакет не находится в очереди проверки",
                detail="Клиентская передача должна атомарно переводить новые проверенные файлы в ON_REVIEW. Иначе юрист не увидит материал в своей очереди.",
                area="documents",
            )
        if status == CaseStatus.M1_LAWYER_REVIEW.value:
            client_documents = [
                item
                for item in non_archived_documents
                if str(item.document_type) != SERVICE_CONTRACT_TYPE
            ]
            if not client_documents:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m1_review_without_documents",
                    severity="critical",
                    title="Проверка юристом открыта без материалов",
                    detail="M1_LAWYER_REVIEW допустим только после фактической передачи клиентских документов.",
                    area="documents",
                )
            if any(
                str(item.status)
                in {
                    DocumentStatus.NEEDS_REUPLOAD.value,
                    DocumentStatus.REJECTED.value,
                }
                for item in client_documents
            ):
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m1_review_has_reupload_request",
                    severity="critical",
                    title="Юрист уже запросил исправление, но дело осталось в стадии проверки",
                    detail="После REJECTED/NEEDS_REUPLOAD M1 должен перейти в M1_DOCS_REQUESTED, чтобы клиент получил точное следующее действие.",
                    area="documents",
                )

        open_contracts = _open_service_contracts(case_documents)
        usable_contracts = [
            item
            for item in open_contracts
            if str(item.status) == DocumentStatus.APPROVED.value
            and document_is_usable(item)
        ]
        if len(usable_contracts) > 1:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="multiple_current_service_contracts",
                severity="critical",
                title="Одновременно доступно несколько текущих договоров",
                detail="Клиент должен подтверждать только одну конкретную защищённую версию. Предыдущие версии необходимо вывести из текущего действия.",
                area="contract",
            )
        contract = _current_contract(case_documents)
        if status == CaseStatus.M1_CONTRACT_READY.value and contract is None:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_contract_not_published",
                severity="warning",
                title="Этап договора открыт, но текущая защищённая версия ещё не опубликована",
                detail="Это незавершённый рабочий шаг: клиент не должен переходить к 30 000 ₽ до публикации проверенного договора.",
                area="contract",
            )

        if status in M1_CONTRACT_PROOF_REQUIRED:
            confirmation = audit_by_case_action.get(
                (case_id, "CLIENT_SERVICE_CONTRACT_CONFIRMED")
            )
            if contract is None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m1_progressed_without_current_contract",
                    severity="critical",
                    title="M1 продвинулся после договора, но текущая подтверждаемая версия не найдена",
                    detail="Платёжные и юридические этапы должны иметь трассируемый защищённый SERVICE_CONTRACT.",
                    area="contract",
                )
            if confirmation is None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m1_contract_confirmation_missing",
                    severity="critical",
                    title="Не найдено подтверждение конкретной версии договора клиентом",
                    detail="Перед первым платежом должен существовать CLIENT_SERVICE_CONTRACT_CONFIRMED с document_id, version и SHA-256.",
                    area="contract",
                )
            elif contract is not None:
                value = confirmation.new_value if isinstance(confirmation.new_value, dict) else {}
                try:
                    confirmed_id = int(value.get("document_id") or 0)
                    confirmed_version = int(value.get("version") or 0)
                except (TypeError, ValueError):
                    confirmed_id = 0
                    confirmed_version = 0
                confirmed_sha = str(value.get("sha256") or "")
                if (
                    confirmed_id != int(contract.id)
                    or confirmed_version != int(contract.version or 1)
                    or (confirmed_sha and confirmed_sha != str(contract.sha256 or ""))
                ):
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="m1_contract_confirmation_drift",
                        severity="critical",
                        title="Подтверждение клиента не совпадает с текущей версией договора",
                        detail="document_id/version/SHA из аудита расходятся с текущим SERVICE_CONTRACT. Финансовый этап нельзя считать доказанным без ручной проверки.",
                        area="contract",
                    )

        for code, code_payments in defaultdict(list, {
            payment_code: [
                item
                for item in case_payments
                if str(item.payment_code) == payment_code
                and str(item.status) in OPEN_PAYMENT_STATUSES
            ]
            for payment_code in {
                PaymentCode.M1_INITIAL_PAYMENT.value,
                PaymentCode.M1_COURT_PAYMENT.value,
                PaymentCode.M1_SUCCESS_FEE.value,
                PaymentCode.M2_CONSULTATION_PAYMENT.value,
            }
        }).items():
            if len(code_payments) > 1:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code=f"duplicate_live_payment_{code.lower()}",
                    severity="critical",
                    title="По одному этапу одновременно открыто несколько платежей",
                    detail=f"Код {code}: активных записей {len(code_payments)}. Клиент не должен получать конкурирующие платёжные ссылки.",
                    area="payment",
                )

        expected_payment_code = WAITING_PAYMENT_CODES.get(status)
        if expected_payment_code:
            if status == CaseStatus.M2_PAYMENT_PENDING.value and payments_disabled():
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_payment_stage_while_payments_disabled",
                    severity="critical",
                    title="M2 ожидает оплату при отключённом платёжном контуре",
                    detail="В режиме без онлайн-платежей консультация должна использовать no-payment booking, а не оставаться в M2_PAYMENT_PENDING.",
                    area="consultation",
                )
            elif not (
                status == CaseStatus.M2_PAYMENT_PENDING.value and payments_disabled()
            ):
                relevant = [
                    item
                    for item in case_payments
                    if str(item.payment_code) == expected_payment_code
                ]
                live = [
                    item
                    for item in relevant
                    if str(item.status) in OPEN_PAYMENT_STATUSES
                ]
                if not live:
                    paid = next(
                        (
                            item
                            for item in reversed(relevant)
                            if str(item.status) == PaymentStatus.PAID.value
                        ),
                        None,
                    )
                    refund = next(
                        (
                            item
                            for item in reversed(relevant)
                            if str(item.status) == PaymentStatus.REFUND_PENDING.value
                        ),
                        None,
                    )
                    if paid is not None:
                        _issue_with_action(
                            issues,
                            case_id=case_id,
                            code="paid_payment_not_applied_to_case",
                            severity="critical",
                            title="Платёж уже PAID, но дело осталось в ожидании оплаты",
                            detail=f"Платёж #{paid.id} подтверждён, однако case status не продвинулся. Повторно платить клиенту нельзя.",
                            area="payment",
                        )
                    elif refund is not None:
                        _issue_with_action(
                            issues,
                            case_id=case_id,
                            code="waiting_stage_has_refund_pending",
                            severity="critical",
                            title="Дело ждёт оплату, хотя этот платёж уже уходит в возврат",
                            detail=f"Платёж #{refund.id} находится в REFUND_PENDING. Сначала завершите финансовое решение.",
                            area="refund",
                        )
                    else:
                        _issue_with_action(
                            issues,
                            case_id=case_id,
                            code="waiting_stage_without_payable_payment",
                            severity="critical",
                            title="Платёжный этап открыт без действующего платежа",
                            detail=f"Для {expected_payment_code} нет PENDING/WAITING_CONFIRMATION/PAID_REVIEW записи. Клиент может попасть в тупик без рабочей оплаты.",
                            area="payment",
                        )

        if status in M1_INITIAL_PAYMENT_REQUIRED and not _has_paid(
            case_payments, PaymentCode.M1_INITIAL_PAYMENT.value
        ):
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_initial_payment_evidence_missing",
                severity="critical",
                title="M1 продвинулся после первого платежа без PAID-записи",
                detail="Этапы после M1_PAYMENT_30000_RECEIVED требуют подтверждённого M1_INITIAL_PAYMENT.",
                area="payment",
            )
        if status in M1_POA_PROOF_REQUIRED and (
            case_id,
            "M1_POA_RECEIVED_CONFIRMED",
        ) not in audit_by_case_action:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_poa_confirmation_missing",
                severity="critical",
                title="Юридический этап продвинулся без подтверждения получения доверенности",
                detail="Клиентское «оформил» не доказывает получение. Нужен staff audit M1_POA_RECEIVED_CONFIRMED.",
                area="documents",
            )
        if status in M1_COURT_DECISION_REQUIRED and (
            case_id,
            "M1_COURT_DECISION_RECORDED",
        ) not in audit_by_case_action:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_court_decision_evidence_missing",
                severity="critical",
                title="Второй платёж/исполнение открыты без доказательства судебного акта",
                detail="Перед 70 000 ₽ должны быть зафиксированы дата и идентификатор судебного акта в M1_COURT_DECISION_RECORDED.",
                area="messages",
            )
        if status in M1_COURT_PAYMENT_REQUIRED and not _has_paid(
            case_payments, PaymentCode.M1_COURT_PAYMENT.value
        ):
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_court_payment_evidence_missing",
                severity="critical",
                title="M1 продвинулся после 70 000 ₽ без PAID-записи",
                detail="Этап исполнения не должен открываться без подтверждённого M1_COURT_PAYMENT.",
                area="payment",
            )
        if status in M1_SUCCESS_FEE_PAID_REQUIRED and not _has_paid(
            case_payments, PaymentCode.M1_SUCCESS_FEE.value
        ):
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m1_success_fee_evidence_missing",
                severity="critical",
                title="Финальный процент отмечен полученным без PAID-записи",
                detail="M1_SUCCESS_FEE_RECEIVED требует подтверждённого M1_SUCCESS_FEE.",
                area="payment",
            )

        refund_pending = [
            item
            for item in case_payments
            if str(item.status) == PaymentStatus.REFUND_PENDING.value
        ]
        if refund_pending and status not in TERMINAL_CASE_STATUSES:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="active_refund_pending",
                severity="warning",
                title="Возврат ещё не завершён",
                detail=f"Платежей в REFUND_PENDING: {len(refund_pending)}. Дело не должно незаметно обойти финансовую очередь.",
                area="refund",
            )

        if route == "M2" and case.assigned_lawyer_id is not None:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m2_legacy_case_assignment",
                severity="warning",
                title="У M2 сохранено case-level назначение юриста",
                detail="Ответственность M2 определяется Consultation.lawyer_id. Старое M1-assignment не должно управлять доступом и очередями консультации.",
                area="consultation",
            )

        if status in M2_CASES_EXPECTING_CONSULTATION and consultation is None:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="m2_consultation_missing",
                severity="warning",
                title="M2-этап существует без объекта консультации",
                detail="При следующем действии бот может восстановить intake, но текущий сохранённый процесс неполон и требует проверки.",
                area="consultation",
            )

        if len(active_consultations) > 1:
            _issue_with_action(
                issues,
                case_id=case_id,
                code="multiple_active_consultations",
                severity="critical",
                title="У одного M2-дела несколько активных консультаций",
                detail=f"Активных Consultation: {len(active_consultations)}. Это создаёт конфликт слотов, оплаты и владельца консультации.",
                area="consultation",
            )

        if consultation is not None and consultation.slot_id is not None:
            slot = slots.get(int(consultation.slot_id))
            if slot is None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="consultation_slot_missing",
                    severity="critical",
                    title="Консультация ссылается на отсутствующий слот",
                    detail=f"Consultation #{consultation.id} содержит slot_id={consultation.slot_id}, но слот не найден.",
                    area="consultation",
                )
            else:
                if int(slot.consultation_id or 0) != int(consultation.id):
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="consultation_slot_backlink_mismatch",
                        severity="critical",
                        title="Нарушена двусторонняя связь Consultation ↔ Slot",
                        detail=f"Consultation #{consultation.id} указывает слот #{slot.id}, но slot.consultation_id={slot.consultation_id or 'пусто'}.",
                        area="consultation",
                    )
                if consultation.lawyer_id is not None and int(consultation.lawyer_id) != int(slot.lawyer_id):
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="consultation_slot_lawyer_mismatch",
                        severity="critical",
                        title="Юрист консультации не совпадает с владельцем слота",
                        detail=f"Consultation.lawyer_id={consultation.lawyer_id}, slot.lawyer_id={slot.lawyer_id}.",
                        area="consultation",
                    )
                if consultation.scheduled_at is not None and not _same_moment(
                    consultation.scheduled_at, slot.starts_at
                ):
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="consultation_schedule_mismatch",
                        severity="critical",
                        title="Время консультации расходится со временем слота",
                        detail="Telegram, кабинет юриста и календарная очередь могут показать разные дату/время.",
                        area="consultation",
                    )

        if consultation is not None and consultation.lawyer_id is not None:
            consultation_lawyer = lawyers.get(int(consultation.lawyer_id))
            if consultation_lawyer is None or not consultation_lawyer.is_active:
                if str(consultation.status) not in TERMINAL_CONSULTATION_STATUSES:
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="active_consultation_inactive_lawyer",
                        severity="critical",
                        title="Активная консультация назначена неактивному юристу",
                        detail="До проведения или переноса консультации нужен действующий ответственный.",
                        area="consultation",
                    )

        if status == CaseStatus.M2_PAYMENT_PENDING.value and consultation is not None:
            slot = slots.get(int(consultation.slot_id or 0)) if consultation.slot_id else None
            if str(consultation.status) != ConsultationStatus.PAYMENT_PENDING.value:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_payment_case_consultation_status_mismatch",
                    severity="critical",
                    title="Case ждёт оплату, а Consultation находится на другом этапе",
                    detail=f"Case={status}, Consultation={consultation.status}. Оплата не должна применяться к несогласованной записи.",
                    area="consultation",
                )
            if consultation.lawyer_id is None:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_payment_without_lawyer",
                    severity="critical",
                    title="Оплата консультации открыта без выбранного юриста",
                    detail="Платёж M2 должен быть привязан к конкретному held-слоту и его юристу.",
                    area="consultation",
                )
            if slot is None or str(slot.status) != "held":
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_payment_without_live_hold",
                    severity="critical",
                    title="Оплата M2 открыта без действующего held-слота",
                    detail="Перед оплатой слот должен оставаться зарезервированным именно за этой консультацией.",
                    area="consultation",
                )
            elif (
                slot.hold_expires_at is None
                or (_as_utc(slot.hold_expires_at) or now) < now
            ):
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_expired_hold_not_reconciled",
                    severity="critical",
                    title="Резерв слота истёк, но дело всё ещё предлагает оплату",
                    detail="Нужно вернуть Consultation и Case в выбор слота; старая платёжная кнопка должна быть fail-closed.",
                    area="consultation",
                )
            elif int(slot.held_by_user_id or 0) != int(case.client_id):
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_hold_wrong_client",
                    severity="critical",
                    title="Held-слот принадлежит другому клиенту",
                    detail="Оплата и бронирование нельзя продолжать при несовпадении held_by_user_id и владельца дела.",
                    area="consultation",
                )

        if status == CaseStatus.M2_SLOT_PENDING.value and consultation is not None:
            if consultation.slot_id is not None:
                slot = slots.get(int(consultation.slot_id))
                if slot is not None and str(slot.status) in {"held", "booked"}:
                    _issue_with_action(
                        issues,
                        case_id=case_id,
                        code="m2_slot_pending_has_live_slot",
                        severity="critical",
                        title="Case просит выбрать слот, хотя Consultation уже держит активный слот",
                        detail="Case и Consultation должны быть синхронизированы, иначе клиент может создать вторую бронь.",
                        area="consultation",
                    )

        if status == CaseStatus.M2_CONSULTATION_BOOKED.value and consultation is not None:
            consultation_status = str(consultation.status)
            slot = slots.get(int(consultation.slot_id or 0)) if consultation.slot_id else None
            if consultation_status == ConsultationStatus.LAWYER_NO_SHOW.value:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_lawyer_no_show_unresolved",
                    severity="warning",
                    title="Зафиксирована неявка юриста — требуется решение администратора",
                    detail="Нужно предложить бесплатный перенос или возврат; кейс не должен исчезнуть из рабочего контроля.",
                    area="consultation",
                )
            elif consultation_status != ConsultationStatus.BOOKED.value:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_booked_case_consultation_status_mismatch",
                    severity="critical",
                    title="Case считает консультацию забронированной, а Consultation — нет",
                    detail=f"Consultation #{consultation.id} имеет статус {consultation.status}.",
                    area="consultation",
                )
            elif slot is None or str(slot.status) != "booked":
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_booked_without_booked_slot",
                    severity="critical",
                    title="Подтверждённая консультация не имеет booked-слота",
                    detail="После оплаты Case, Consultation и Slot должны атомарно отражать одну бронь.",
                    area="consultation",
                )

        if status == CaseStatus.M2_CONSULTATION_DONE.value and consultation is not None:
            consultation_status = str(consultation.status)
            if consultation_status not in {
                ConsultationStatus.DONE.value,
                ConsultationStatus.CLIENT_NO_SHOW.value,
            }:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_done_case_consultation_status_mismatch",
                    severity="critical",
                    title="Case завершил консультацию, но Consultation не содержит допустимого результата",
                    detail=f"Consultation #{consultation.id}: {consultation.status}.",
                    area="consultation",
                )
            if consultation_status == ConsultationStatus.CLIENT_NO_SHOW.value:
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_client_no_show_resolution_required",
                    severity="warning",
                    title="Неявка клиента ждёт конечного решения",
                    detail="Администратор должен либо открыть новую платную запись, либо закрыть обращение.",
                    area="consultation",
                )
            if str(consultation.decision or "").lower() == "other":
                _issue_with_action(
                    issues,
                    case_id=case_id,
                    code="m2_unsupported_legacy_outcome",
                    severity="warning",
                    title="Сохранён устаревший неопределённый результат консультации",
                    detail="Текущий MVP допускает close, to_m1 или follow_up. Решение other не должно оставаться без конкретного следующего шага.",
                    area="consultation",
                )

        if issues:
            issues.sort(key=lambda item: (_SEVERITY_RANK[str(item["severity"])], str(item["code"])))
            highest = str(issues[0]["severity"])
            user = users.get(case_id)
            output.append(
                {
                    "case_id": case_id,
                    "case_number": case.case_number,
                    "client_name": user.full_name if user else "Клиент не найден",
                    "route": case.route,
                    "status": status,
                    "status_label": _safe_status_label(status),
                    "updated_at": case.updated_at.isoformat() if case.updated_at else None,
                    "severity": highest,
                    "issue_count": len(issues),
                    "issues": issues,
                    "primary_action": {
                        "label": issues[0].get("action_label") or "Открыть карточку",
                        "href": issues[0].get("action_href"),
                        "kind": issues[0].get("action_kind") or "case",
                    },
                }
            )

    output.sort(
        key=lambda item: (
            _SEVERITY_RANK[str(item["severity"])],
            str(item.get("updated_at") or ""),
            int(item["case_id"]),
        )
    )
    critical_count = sum(1 for item in output if item["severity"] == "critical")
    warning_count = len(output) - critical_count
    return {
        "count": len(output),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "scanned_cases": len(cases),
        "truncated": truncated,
        "items": output,
        "generated_at": now.isoformat(),
    }


@router.get("/admin/workdesk/integrity")
async def workdesk_integrity(
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    result = await build_workdesk_integrity(db)
    items = list(result["items"])
    result["total"] = len(items)
    result["items"] = items[:limit]
    result["count"] = len(result["items"])
    result["result_truncated"] = len(items) > limit
    return result


_WORKDESK_INTEGRITY_PATCH = r"""
<script>
(function(){
  const host=document.getElementById('processIntegrityBanner');
  if(!host)return;
  let integrityTimer=null;
  const escIntegrity=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function issueAction(item,issue){
    const id=Number(item.case_id),kind=issue?.action_kind||item.primary_action?.kind||'case',href=issue?.action_href||item.primary_action?.href,label=issue?.action_label||item.primary_action?.label||'Открыть карточку';
    if(kind==='assign')return `<button onclick="assign(${id},this);setTimeout(loadProcessIntegrity,900)">${escIntegrity(label)}</button>`;
    if(href)return `<a class="button secondary" href="${escIntegrity(href)}">${escIntegrity(label)}</a>`;
    return `<button class="secondary" onclick="openIntegrityCase(${id})">${escIntegrity(label)}</button>`;
  }
  function issueList(item){
    return (item.issues||[]).map(x=>`<div style="padding:9px 0;border-top:1px solid #e4e7ec"><b>${escIntegrity(x.title)}</b><div class="muted" style="margin-top:3px">${escIntegrity(x.detail)}</div><div class="row" style="margin-top:7px">${issueAction(item,x)}</div></div>`).join('');
  }
  function integrityRow(item){
    const critical=item.severity==='critical',tone=critical?'#fef3f2':'#fff7e6',border=critical?'#fecdca':'#fedf89',ink=critical?'#b42318':'#a15c00',first=(item.issues||[])[0]||{};
    return `<article style="background:${tone};border:1px solid ${border};border-radius:12px;padding:12px;margin-top:9px"><div class="head"><div><b>${escIntegrity(item.case_number)} · ${escIntegrity(item.client_name)}</b><div class="muted">${escIntegrity(item.status_label)} · проблем: ${Number(item.issue_count||0)}</div></div><span class="badge" style="background:#fff;color:${ink};border:1px solid ${border}">${critical?'Критично':'Проверить'}</span></div><div style="margin-top:8px;color:${ink}"><b>${escIntegrity(first.title||'Требуется проверка')}</b></div><div class="muted" style="margin-top:3px">${escIntegrity(first.detail||'')}</div><div class="row" style="margin-top:9px"><button onclick="openIntegrityCase(${Number(item.case_id)})">Карточка дела</button>${issueAction(item,first)}</div>${Number(item.issue_count||0)>1?`<details style="margin-top:9px"><summary style="cursor:pointer;font-weight:700">Все найденные несоответствия (${Number(item.issue_count)})</summary>${issueList(item)}</details>`:''}</article>`;
  }
  window.openIntegrityCase=function(id){
    if(typeof openCase==='function')openCase(Number(id));
    const side=document.getElementById('caseView');if(side&&window.innerWidth<1180)setTimeout(()=>side.scrollIntoView({behavior:'smooth',block:'start'}),80);
  };
  window.loadProcessIntegrity=async function(){
    try{
      const sessionResponse=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});
      if(!sessionResponse.ok)return;
      const session=await sessionResponse.json();
      if(!(session.roles||[session.role]).includes('admin'))return;
      const response=await fetch('/admin/workdesk/integrity?limit=100',{credentials:'same-origin',cache:'no-store',headers:{'x-admin-token':session.api_token||''}});
      if(!response.ok)return;
      const data=await response.json(),rows=data.items||[];
      if(!rows.length){
        host.innerHTML=`<section style="margin-bottom:14px;background:#ecfdf3;border:1px solid #abefc6;border-radius:14px;padding:12px 14px;color:#067647"><div class="head"><div><b>✓ Контроль целостности: явных разрывов не найдено</b><div style="font-size:12px;margin-top:3px">Проверено дел: ${Number(data.scanned_cases||0)} · ${escIntegrity(new Date(data.generated_at).toLocaleString('ru-RU'))}</div></div><button class="ghost" onclick="loadProcessIntegrity()">Проверить снова</button></div></section>`;
        return;
      }
      const critical=Number(data.critical_count||0),warnings=Number(data.warning_count||0),preview=rows.slice(0,4).map(integrityRow).join(''),rest=rows.length>4?rows.slice(4).map(integrityRow).join(''):'';
      const incomplete=data.truncated||data.result_truncated?'<div style="margin-top:8px;font-size:12px">⚠ Результат ограничен. Откройте повторную проверку после устранения верхних проблем.</div>':'';
      host.innerHTML=`<section style="margin-bottom:14px;background:#fff;border:1px solid ${critical?'#fecdca':'#fedf89'};border-radius:14px;padding:14px;box-shadow:0 8px 22px rgba(16,24,40,.05)"><div class="head"><div><div class="eyebrow">E2E-контроль процесса</div><b style="font-size:16px">${critical?'⛔':'⚠'} Несогласованных дел: ${Number(data.total||rows.length)}</b><div class="muted" style="margin-top:3px">Критично: ${critical} · проверить: ${warnings} · просканировано: ${Number(data.scanned_cases||0)}</div></div><button class="secondary" onclick="loadProcessIntegrity()">Обновить контроль</button></div>${incomplete}${preview}${rest?`<details style="margin-top:10px"><summary style="cursor:pointer;font-weight:800">Показать остальные дела (${rows.length-4})</summary>${rest}</details>`:''}</section>`;
    }catch(_){host.innerHTML='<section style="margin-bottom:14px" class="error"><b>Контроль целостности временно не загрузился</b><p>Основной workdesk остаётся доступным. Повторите только эту проверку.</p><button onclick="loadProcessIntegrity()">Повторить контроль</button></section>'}
  };
  loadProcessIntegrity();
  integrityTimer=setInterval(loadProcessIntegrity,60000);
  window.addEventListener('beforeunload',()=>{if(integrityTimer)clearInterval(integrityTimer)},{once:true});
})();
</script>
"""


def inject_workdesk_integrity(html: str) -> str:
    marker = "<main>"
    if html.count(marker) != 1:
        raise RuntimeError("Workdesk template contract changed: <main> marker")
    html = html.replace(marker, '<main><div id="processIntegrityBanner"></div>', 1)
    end = "</body>"
    if html.count(end) != 1:
        raise RuntimeError("Workdesk template contract changed: </body> marker")
    return html.replace(end, _WORKDESK_INTEGRITY_PATCH + end, 1)


__all__ = ["build_workdesk_integrity", "inject_workdesk_integrity", "router"]
