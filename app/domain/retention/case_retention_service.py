from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_transition_policy import TERMINAL_STATUSES
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.document_access_grant import DocumentAccessGrant
from app.models.message import Message
from app.models.notification import Notification
from app.models.payment import Payment

POLICY_VERSION = "case-content-v1"
STATUS_DISCOVERED = "DISCOVERED"
STATUS_REQUESTED = "REQUESTED"
STATUS_APPROVED = "APPROVED"
STATUS_EXECUTING = "EXECUTING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"

UNRESOLVED_PAYMENT_STATUSES = {
    PaymentStatus.PENDING,
    PaymentStatus.WAITING_CONFIRMATION,
    PaymentStatus.PAID_REVIEW,
    PaymentStatus.REFUND_PENDING,
}
KNOWN_TERMINAL_PAYMENT_STATUSES = {
    PaymentStatus.PAID,
    PaymentStatus.REFUND_DECLINED,
    PaymentStatus.FAILED,
    PaymentStatus.CANCELLED,
    PaymentStatus.REFUNDED,
    PaymentStatus.EXPIRED,
}
ACTIVE_CONSULTATION_STATUSES = {
    ConsultationStatus.DESCRIPTION_PENDING,
    ConsultationStatus.DOCUMENTS_OPTIONAL,
    ConsultationStatus.SLOT_PENDING,
    ConsultationStatus.SLOT_RESERVED,
    ConsultationStatus.PAYMENT_PENDING,
    ConsultationStatus.BOOKED,
}


class CaseRetentionError(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _required_reason(value: str | None, field: str) -> str:
    result = str(value or "").strip()
    if len(result) < 10:
        raise CaseRetentionError(f"{field} должен содержать не менее 10 символов")
    return result[:2000]


def _error_code(error: Exception) -> str:
    return error.__class__.__name__[:100]


def _safe_error_message(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return message[:500]


class CaseRetentionService:
    """Controlled deletion of closed-case content.

    The service intentionally preserves the case tombstone, payment ledger and
    tamper-evident audit chain. It deletes encrypted document files and clears
    operational case content only after a two-person approval. File removal is
    an unlink + directory fsync operation; it does not make false guarantees
    about physical block overwriting on SSD/COW filesystems.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    @property
    def retention_days(self) -> int:
        return min(max(int(settings.closed_case_retention_days), 30), 36500)

    async def _load_case(self, case_id: int) -> Case:
        case = await self.db.get(Case, int(case_id))
        if not case:
            raise CaseRetentionError("Дело не найдено")
        return case

    async def _load_record(self, record_id: int) -> CaseRetentionRecord:
        record = await self.db.get(CaseRetentionRecord, int(record_id))
        if not record:
            raise CaseRetentionError("Запись политики хранения не найдена")
        return record

    def _closed_at(self, case: Case) -> datetime:
        if case.closed_at:
            return _utc(case.closed_at)
        if str(case.status) not in {status.value for status in TERMINAL_STATUSES}:
            raise CaseRetentionError("Политика хранения применяется только к закрытым делам")
        fallback = case.updated_at or case.created_at or _now()
        case.closed_at = _utc(fallback)
        return case.closed_at

    def retention_due_at(self, case: Case) -> datetime:
        return self._closed_at(case) + timedelta(days=self.retention_days)

    async def ensure_record(self, case: Case) -> CaseRetentionRecord:
        existing = (
            await self.db.execute(
                select(CaseRetentionRecord).where(
                    CaseRetentionRecord.case_id == case.id
                )
            )
        ).scalar_one_or_none()
        due_at = self.retention_due_at(case)
        if existing:
            if existing.status == STATUS_DISCOVERED and not existing.legal_hold:
                existing.retention_due_at = due_at
            return existing
        record = CaseRetentionRecord(
            case_id=case.id,
            policy_version=POLICY_VERSION,
            status=STATUS_DISCOVERED,
            retention_due_at=due_at,
        )
        self.db.add(record)
        await self.db.flush()
        return record

    async def discover_due_cases(
        self,
        *,
        now: datetime | None = None,
        persist: bool | None = None,
    ) -> dict[str, int | bool]:
        current = _utc(now or _now())
        should_persist = (
            not bool(settings.case_retention_dry_run)
            if persist is None
            else bool(persist)
        )
        cutoff = current - timedelta(days=self.retention_days)
        batch_size = min(
            max(int(settings.case_retention_scan_batch_size), 1),
            1000,
        )
        cases = (
            await self.db.execute(
                select(Case)
                .where(
                    Case.status.in_([status.value for status in TERMINAL_STATUSES]),
                    Case.content_deleted_at.is_(None),
                    Case.closed_at.is_not(None),
                    Case.closed_at <= cutoff,
                )
                .order_by(Case.closed_at.asc(), Case.id.asc())
                .limit(batch_size)
            )
        ).scalars().all()

        due_count = len(cases)
        created = 0
        already_tracked = 0
        for case in cases:
            existing = (
                await self.db.execute(
                    select(CaseRetentionRecord.id).where(
                        CaseRetentionRecord.case_id == case.id
                    )
                )
            ).scalar_one_or_none()
            if existing:
                already_tracked += 1
                continue
            if not should_persist:
                continue
            record = await self.ensure_record(case)
            created += 1
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=case.id,
                action="CASE_RETENTION_DISCOVERED",
                new_value={
                    "record_id": record.id,
                    "policy_version": POLICY_VERSION,
                    "retention_due_at": record.retention_due_at.isoformat(),
                },
                comment="Дело достигло настраиваемого срока хранения",
            )
        await self.db.flush()
        return {
            "dry_run": not should_persist,
            "due_count": due_count,
            "created": created,
            "already_tracked": already_tracked,
        }

    async def _assert_operationally_settled(self, case: Case) -> None:
        payments = (
            await self.db.execute(
                select(Payment.status).where(Payment.case_id == case.id)
            )
        ).scalars().all()
        unknown_or_unresolved = [
            str(status)
            for status in payments
            if status in UNRESOLVED_PAYMENT_STATUSES
            or status not in KNOWN_TERMINAL_PAYMENT_STATUSES
        ]
        if unknown_or_unresolved:
            raise CaseRetentionError(
                "Удаление заблокировано: у дела есть незавершённые или неизвестные "
                "платёжные статусы"
            )

        consultations = (
            await self.db.execute(
                select(Consultation.status).where(Consultation.case_id == case.id)
            )
        ).scalars().all()
        if any(status in ACTIVE_CONSULTATION_STATUSES for status in consultations):
            raise CaseRetentionError(
                "Удаление заблокировано: у дела есть незавершённая консультация"
            )

    async def _assert_eligible(
        self,
        record: CaseRetentionRecord,
        case: Case,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _utc(now or _now())
        if str(case.status) not in {status.value for status in TERMINAL_STATUSES}:
            raise CaseRetentionError("Удаление возможно только для закрытого дела")
        if case.content_deleted_at is not None:
            if record.status == STATUS_COMPLETED:
                return
            raise CaseRetentionError("Содержимое дела уже удалено")
        if record.legal_hold:
            raise CaseRetentionError("Удаление заблокировано legal hold")
        if _utc(record.retention_due_at) > current:
            raise CaseRetentionError("Срок хранения дела ещё не истёк")
        await self._assert_operationally_settled(case)

    async def set_legal_hold(
        self,
        *,
        case_id: int,
        actor_id: int,
        reason: str,
    ) -> CaseRetentionRecord:
        case = await self._load_case(case_id)
        record = await self.ensure_record(case)
        if record.status == STATUS_COMPLETED:
            raise CaseRetentionError("Нельзя установить hold после удаления содержимого")
        if record.status == STATUS_EXECUTING:
            raise CaseRetentionError("Нельзя установить hold во время удаления")
        hold_reason = _required_reason(reason, "Причина legal hold")
        now = _now()
        record.legal_hold = True
        record.legal_hold_reason = hold_reason
        record.legal_hold_set_at = now
        record.legal_hold_set_by = actor_id
        record.legal_hold_released_at = None
        record.legal_hold_released_by = None
        # Any prior approval becomes stale when a hold is introduced.
        record.status = STATUS_DISCOVERED
        record.requested_at = None
        record.requested_by = None
        record.request_reason = None
        record.approved_at = None
        record.approved_by = None
        record.approval_comment = None
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_LEGAL_HOLD_SET",
            new_value={"record_id": record.id, "legal_hold": True},
            comment=hold_reason,
        )
        await self.db.flush()
        return record

    async def release_legal_hold(
        self,
        *,
        case_id: int,
        actor_id: int,
        reason: str,
    ) -> CaseRetentionRecord:
        case = await self._load_case(case_id)
        record = await self.ensure_record(case)
        if record.status == STATUS_EXECUTING:
            raise CaseRetentionError("Нельзя изменить hold во время удаления")
        if not record.legal_hold:
            return record
        release_reason = _required_reason(reason, "Причина снятия legal hold")
        record.legal_hold = False
        record.legal_hold_reason = None
        record.legal_hold_released_at = _now()
        record.legal_hold_released_by = actor_id
        record.status = STATUS_DISCOVERED
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_LEGAL_HOLD_RELEASED",
            new_value={"record_id": record.id, "legal_hold": False},
            comment=release_reason,
        )
        await self.db.flush()
        return record

    async def request_deletion(
        self,
        *,
        case_id: int,
        actor_id: int,
        reason: str,
        now: datetime | None = None,
    ) -> CaseRetentionRecord:
        case = await self._load_case(case_id)
        record = await self.ensure_record(case)
        if record.status == STATUS_COMPLETED:
            return record
        await self._assert_eligible(record, case, now=now)
        request_reason = _required_reason(reason, "Обоснование удаления")
        if record.status not in {STATUS_DISCOVERED, STATUS_FAILED, STATUS_REQUESTED}:
            raise CaseRetentionError(
                f"Запрос нельзя создать из статуса {record.status}"
            )
        if record.status == STATUS_REQUESTED:
            if record.requested_by == actor_id:
                return record
            raise CaseRetentionError(
                "Для дела уже существует запрос другого суперадминистратора"
            )
        record.status = STATUS_REQUESTED
        record.requested_at = _utc(now or _now())
        record.requested_by = actor_id
        record.request_reason = request_reason
        record.approved_at = None
        record.approved_by = None
        record.approval_comment = None
        record.last_error = None
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_RETENTION_DELETION_REQUESTED",
            new_value={"record_id": record.id, "status": record.status},
            comment=request_reason,
        )
        await self.db.flush()
        return record

    async def approve_deletion(
        self,
        *,
        record_id: int,
        actor_id: int,
        comment: str,
        now: datetime | None = None,
    ) -> CaseRetentionRecord:
        record = await self._load_record(record_id)
        case = await self._load_case(record.case_id)
        if record.status == STATUS_COMPLETED:
            return record
        await self._assert_eligible(record, case, now=now)
        if record.status != STATUS_REQUESTED:
            raise CaseRetentionError("Сначала другой администратор должен создать запрос")
        if int(record.requested_by or 0) == int(actor_id):
            raise CaseRetentionError(
                "Запрос и одобрение должны выполнить разные суперадминистраторы"
            )
        approval_comment = _required_reason(comment, "Комментарий одобрения")
        record.status = STATUS_APPROVED
        record.approved_at = _utc(now or _now())
        record.approved_by = actor_id
        record.approval_comment = approval_comment
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_RETENTION_DELETION_APPROVED",
            new_value={
                "record_id": record.id,
                "requested_by": record.requested_by,
                "approved_by": actor_id,
            },
            comment=approval_comment,
        )
        await self.db.flush()
        return record

    def _storage_root(self) -> Path:
        return Path(settings.storage_dir).resolve(strict=False)

    def _resolve_document_path(self, raw_path: str) -> Path:
        root = self._storage_root()
        candidate = Path(str(raw_path or ""))
        if not candidate.is_absolute():
            candidate = root / candidate
        lexical = Path(os.path.abspath(candidate))
        try:
            relative = lexical.relative_to(root)
        except ValueError as error:
            raise CaseRetentionError(
                "Документ находится вне настроенного хранилища"
            ) from error

        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise CaseRetentionError(
                    "Удаление через символические ссылки запрещено"
                )
        resolved = lexical.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise CaseRetentionError(
                "Путь документа выходит за границы хранилища"
            ) from error
        if resolved.exists() and not resolved.is_file():
            raise CaseRetentionError("Путь документа не является обычным файлом")
        return resolved

    def _preflight_documents(self, documents: list[Document]) -> list[Path]:
        return [self._resolve_document_path(document.file_path) for document in documents]

    @staticmethod
    def _content_digest(documents: list[Document]) -> str:
        payload = [
            {
                "document_id": int(document.id),
                "sha256": str(document.sha256 or ""),
                "size": int(document.file_size or 0),
            }
            for document in sorted(documents, key=lambda item: int(item.id))
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _unlink_and_sync(path: Path) -> bool:
        if not path.exists():
            return False
        if path.is_symlink() or not path.is_file():
            raise CaseRetentionError("Небезопасный тип файла при удалении")
        parent = path.parent
        path.unlink()
        try:
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
        return True

    async def execute_deletion(
        self,
        *,
        record_id: int,
        actor_id: int,
        now: datetime | None = None,
    ) -> CaseRetentionRecord:
        current = _utc(now or _now())
        record = await self._load_record(record_id)
        case = await self._load_case(record.case_id)
        if record.status == STATUS_COMPLETED and case.content_deleted_at is not None:
            return record
        if record.status not in {STATUS_APPROVED, STATUS_EXECUTING, STATUS_FAILED}:
            raise CaseRetentionError("Удаление не одобрено")
        if record.status == STATUS_FAILED and record.approved_at is None:
            raise CaseRetentionError("Повторное удаление требует действующего одобрения")
        if record.status == STATUS_EXECUTING and record.execution_started_at:
            timeout = timedelta(
                seconds=min(
                    max(int(settings.case_retention_execution_timeout_seconds), 60),
                    86400,
                )
            )
            if _utc(record.execution_started_at) + timeout > current:
                raise CaseRetentionError("Удаление уже выполняется другим процессом")
        await self._assert_eligible(record, case, now=current)

        # Validate every path before revoking access or changing durable state.
        # Filesystem calls run outside the event loop to avoid blocking API and bot work.
        preflight_documents = (
            await self.db.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .order_by(Document.id.asc())
            )
        ).scalars().all()
        try:
            await asyncio.to_thread(
                self._preflight_documents, list(preflight_documents)
            )
        except Exception as error:
            record.status = STATUS_FAILED
            record.failed_at = current
            record.last_error = (
                f"{_error_code(error)}: {_safe_error_message(error)}"
            )
            record.attempt_count = int(record.attempt_count or 0) + 1
            await add_case_history_event(
                self.db,
                actor_type="admin",
                actor_id=actor_id,
                case_id=case.id,
                action="CASE_RETENTION_PREFLIGHT_FAILED",
                new_value={
                    "record_id": record.id,
                    "attempt": record.attempt_count,
                    "error_code": _error_code(error),
                },
                comment="Предварительная проверка файлов не пройдена",
            )
            await self.db.commit()
            if isinstance(error, CaseRetentionError):
                raise
            raise CaseRetentionError(
                "Предварительная проверка удаления завершилась ошибкой"
            ) from error

        claim = await self.db.execute(
            update(CaseRetentionRecord)
            .where(
                CaseRetentionRecord.id == record.id,
                CaseRetentionRecord.status == record.status,
                CaseRetentionRecord.legal_hold.is_(False),
            )
            .values(
                status=STATUS_EXECUTING,
                execution_started_at=current,
                failed_at=None,
                last_error=None,
                attempt_count=CaseRetentionRecord.attempt_count + 1,
            )
        )
        if int(claim.rowcount or 0) != 1:
            await self.db.rollback()
            latest = await self._load_record(record_id)
            if latest.status == STATUS_COMPLETED:
                return latest
            raise CaseRetentionError(
                "Состояние удаления изменилось параллельно; обновите страницу"
            )
        await self.db.refresh(record)
        grants = (
            await self.db.execute(
                select(DocumentAccessGrant).where(
                    DocumentAccessGrant.case_id == case.id,
                    DocumentAccessGrant.revoked_at.is_(None),
                )
            )
        ).scalars().all()
        for grant in grants:
            grant.revoked_at = current
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_RETENTION_EXECUTION_STARTED",
            new_value={
                "record_id": record.id,
                "attempt": record.attempt_count,
                "grants_revoked": len(grants),
            },
            comment="Запущено одобренное удаление содержимого закрытого дела",
        )
        # Persist EXECUTING before touching the filesystem. If the process stops
        # after unlink but before the final commit, a retry accepts missing files.
        await self.db.commit()

        try:
            record = await self._load_record(record_id)
            case = await self._load_case(record.case_id)
            documents = (
                await self.db.execute(
                    select(Document)
                    .where(Document.case_id == case.id)
                    .order_by(Document.id.asc())
                )
            ).scalars().all()
            paths = await asyncio.to_thread(
                self._preflight_documents, list(documents)
            )
            digest = self._content_digest(list(documents))
            for path in paths:
                await asyncio.to_thread(self._unlink_and_sync, path)

            await self.db.execute(
                delete(DocumentAccessGrant).where(
                    DocumentAccessGrant.case_id == case.id
                )
            )
            await self.db.execute(delete(Document).where(Document.case_id == case.id))
            message_result = await self.db.execute(
                delete(Message).where(Message.case_id == case.id)
            )
            notification_result = await self.db.execute(
                delete(Notification).where(Notification.case_id == case.id)
            )
            await self.db.execute(
                delete(Calculation).where(Calculation.case_id == case.id)
            )

            primary_consultations = (
                await self.db.execute(
                    select(Consultation).where(Consultation.case_id == case.id)
                )
            ).scalars().all()
            for consultation in primary_consultations:
                consultation.related_case_id = None
                consultation.lawyer_id = None
                consultation.slot_id = None
                consultation.scheduled_at = None
                consultation.client_description = None
                consultation.lawyer_result = None
                consultation.decision = None
                consultation.status = "CONTENT_DELETED"
                consultation.consultation_type = "retained_tombstone"
                consultation.subject_type = "retained_tombstone"

            related_consultations = (
                await self.db.execute(
                    select(Consultation).where(
                        Consultation.related_case_id == case.id,
                        Consultation.case_id != case.id,
                    )
                )
            ).scalars().all()
            for consultation in related_consultations:
                consultation.related_case_id = None

            case.title = "Содержимое удалено по политике хранения"
            case.internal_comment = None
            case.assigned_lawyer_id = None
            case.assigned_at = None
            case.first_lawyer_response_at = None
            case.last_lawyer_activity_at = None
            case.sla_due_at = None
            case.sla_status = "NOT_STARTED"
            case.escalation_level = 0
            case.next_action = "Содержимое дела удалено"
            case.content_deleted_at = current

            record.status = STATUS_COMPLETED
            record.executed_at = current
            record.failed_at = None
            record.last_error = None
            record.documents_deleted = len(documents)
            record.messages_deleted = int(message_result.rowcount or 0)
            record.notifications_deleted = int(notification_result.rowcount or 0)
            record.consultations_anonymized = len(primary_consultations)
            record.content_digest = digest

            await add_case_history_event(
                self.db,
                actor_type="admin",
                actor_id=actor_id,
                case_id=case.id,
                action="CASE_RETENTION_CONTENT_DELETED",
                new_value={
                    "record_id": record.id,
                    "documents_deleted": record.documents_deleted,
                    "messages_deleted": record.messages_deleted,
                    "notifications_deleted": record.notifications_deleted,
                    "consultations_anonymized": record.consultations_anonymized,
                    "content_digest": digest,
                    "payments_preserved": True,
                    "audit_preserved": True,
                },
                comment=(
                    "Удалено содержимое закрытого дела; финансовый ledger и "
                    "неизменяемый аудит сохранены"
                ),
            )
            await self.db.commit()
            return record
        except Exception as error:
            await self.db.rollback()
            record = await self._load_record(record_id)
            case = await self._load_case(record.case_id)
            record.status = STATUS_FAILED
            record.failed_at = _now()
            record.last_error = f"{_error_code(error)}: {_safe_error_message(error)}"
            await add_case_history_event(
                self.db,
                actor_type="admin",
                actor_id=actor_id,
                case_id=case.id,
                action="CASE_RETENTION_EXECUTION_FAILED",
                new_value={
                    "record_id": record.id,
                    "attempt": record.attempt_count,
                    "error_code": _error_code(error),
                },
                comment="Удаление прервано и может быть безопасно повторено",
            )
            await self.db.commit()
            if isinstance(error, CaseRetentionError):
                raise
            raise CaseRetentionError("Удаление содержимого завершилось ошибкой") from error


__all__ = [
    "CaseRetentionError",
    "CaseRetentionService",
    "STATUS_APPROVED",
    "STATUS_COMPLETED",
    "STATUS_DISCOVERED",
    "STATUS_EXECUTING",
    "STATUS_FAILED",
    "STATUS_REQUESTED",
]
