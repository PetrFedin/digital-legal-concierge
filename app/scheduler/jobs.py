import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.cases.sla_service import CaseSLAService
from app.domain.consultations.slot_service import SlotService
from app.domain.notifications.client_inactivity_service import ClientInactivityReminderService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.retention.case_retention_service import CaseRetentionService
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment
from app.security.backup_freshness import backup_freshness_status
from app.security.backup_restore_fence import (
    backup_maintenance_lock,
    purge_revoked_backups,
)
from app.security.backup_retention import cleanup_authenticated_backups
from app.security.backup_service import create_provider_encrypted_backup
from app.security.document_access import cleanup_document_access_grants
from app.security.document_key_rotation import migrate_document_encryption
from app.security.document_scanning import rescan_legacy_documents
from app.security.file_uploads import cleanup_quarantine
from app.security.key_rotation import reencrypt_mfa_secrets
from app.security.login_throttle import LoginThrottleService
from app.security.token_revocation import cleanup_revoked_tokens


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _create_encrypted_backup() -> dict[str, object]:
    with backup_maintenance_lock(settings.backup_dir):
        purge_revoked_backups(settings.backup_dir)
        result = create_provider_encrypted_backup(
            database_url=settings.database_url,
            storage_dir=settings.storage_dir,
            backup_dir=settings.backup_dir,
        )
    return {
        "created": True,
        "archive": Path(result.path).name,
        "key_id": result.key_id,
        "created_at": result.created_at,
        "encrypted_size": result.encrypted_size,
        "verified": result.verified,
    }


class SchedulerJobs:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.notifications = NotificationEngine(db)

    async def create_encrypted_backup_if_due(self) -> dict[str, object]:
        if settings.app_env != "production":
            return {"created": False, "reason": "not_production"}
        if not settings.automatic_encrypted_backups_enabled:
            return {"created": False, "reason": "disabled"}

        freshness = await asyncio.to_thread(
            backup_freshness_status,
            required=True,
        )
        if freshness.ok:
            return {
                "created": False,
                "reason": "fresh_backup_exists",
                "archive": freshness.archive,
                "age_seconds": freshness.age_seconds,
            }
        if freshness.reason in {
            "invalid_max_age_configuration",
            "invalid_freshness_configuration",
            "restore_fence_invalid",
        }:
            raise RuntimeError(
                f"Automatic backup blocked by {freshness.reason}"
            )
        return await asyncio.to_thread(_create_encrypted_backup)

    async def check_client_inactivity_reminders(self) -> int:
        return await ClientInactivityReminderService(self.db).run()

    async def check_unpaid_payments(self) -> int:
        now = datetime.now(timezone.utc)
        reminder_cutoff = now - timedelta(minutes=15)
        payments = (
            await self.db.execute(
                select(Payment).where(
                    Payment.status.in_(
                        [
                            PaymentStatus.PENDING,
                            PaymentStatus.WAITING_CONFIRMATION,
                        ]
                    ),
                    Payment.created_at <= reminder_cutoff,
                )
            )
        ).scalars().all()

        count = 0
        day_key = now.date().isoformat()
        for payment in payments:
            case = await self.db.get(Case, payment.case_id)
            created = await self.notifications.emit(
                event_code="PAYMENT_REMINDER",
                case_id=payment.case_id,
                payload={
                    "case_number": case.case_number if case else payment.case_id
                },
                dedupe_key=f"payment:{payment.id}:reminder:{day_key}",
            )
            if created:
                count += 1
        return count

    async def release_unpaid_consultation_slots(self) -> int:
        return await SlotService(self.db).release_expired_holds()

    async def check_consultation_reminders(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=24)
        consultations = (
            await self.db.execute(
                select(Consultation)
                .where(
                    Consultation.status == ConsultationStatus.BOOKED,
                    Consultation.scheduled_at.is_not(None),
                    Consultation.scheduled_at > now,
                    Consultation.scheduled_at <= horizon,
                )
                .order_by(Consultation.scheduled_at.asc())
            )
        ).scalars().all()

        result = {"within_24h": 0, "within_2h": 0}
        for consultation in consultations:
            scheduled_at = as_utc(consultation.scheduled_at)
            remaining = scheduled_at - now
            case = await self.db.get(Case, consultation.case_id)
            payload = {
                "case_number": (
                    case.case_number if case else consultation.case_id
                ),
                "date": scheduled_at.strftime("%d.%m.%Y %H:%M UTC"),
            }
            if remaining <= timedelta(hours=2):
                event_code = "CONSULTATION_REMINDER_2H"
                bucket = "within_2h"
                key_suffix = "2h"
            else:
                event_code = "CONSULTATION_REMINDER_24H"
                bucket = "within_24h"
                key_suffix = "24h"

            created = await self.notifications.emit(
                event_code=event_code,
                case_id=consultation.case_id,
                payload=payload,
                dedupe_key=(
                    f"consultation:{consultation.id}:reminder:{key_suffix}"
                ),
            )
            if created:
                result[bucket] += 1
        return result

    async def check_consultation_completion_overdue(self) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=15)
        rows = (
            await self.db.execute(
                select(Consultation, ConsultationSlot)
                .join(
                    ConsultationSlot,
                    ConsultationSlot.id == Consultation.slot_id,
                )
                .where(
                    Consultation.status == ConsultationStatus.BOOKED,
                    ConsultationSlot.status == "booked",
                    ConsultationSlot.ends_at <= cutoff,
                )
                .order_by(ConsultationSlot.ends_at.asc())
            )
        ).all()

        count = 0
        for consultation, slot in rows:
            case = await self.db.get(Case, consultation.case_id)
            created = await self.notifications.emit(
                event_code="CONSULTATION_COMPLETION_OVERDUE",
                case_id=consultation.case_id,
                payload={
                    "case_number": (
                        case.case_number if case else consultation.case_id
                    ),
                    "date": as_utc(slot.starts_at).strftime(
                        "%d.%m.%Y %H:%M UTC"
                    ),
                },
                dedupe_key=(
                    f"consultation:{consultation.id}:completion-overdue"
                ),
            )
            if created:
                count += 1
        return count

    async def check_case_sla(self) -> dict:
        return await CaseSLAService(self.db).escalate_overdue_cases()

    async def discover_due_case_retention(self) -> dict[str, int | bool]:
        return await CaseRetentionService(self.db).discover_due_cases()

    async def cleanup_security_state(self) -> dict[str, object]:
        return {
            "login_states": await LoginThrottleService(self.db).cleanup(),
            "revoked_tokens": await cleanup_revoked_tokens(self.db),
            "document_access_grants": await cleanup_document_access_grants(self.db),
            "mfa_secrets_reencrypted": await reencrypt_mfa_secrets(self.db),
            "document_rescan": await rescan_legacy_documents(self.db),
            "document_encryption": await migrate_document_encryption(self.db),
            "encrypted_backups_removed": await asyncio.to_thread(
                cleanup_authenticated_backups,
                retention_days=settings.backup_retention_days,
            ),
            "quarantine_files_removed": cleanup_quarantine(
                Path(settings.storage_dir),
                retention_days=settings.upload_quarantine_retention_days,
            ),
        }

    async def check_claim_waiting_30_days(self) -> int:
        deadline = datetime.now(timezone.utc) - timedelta(days=30)
        cases = (
            await self.db.execute(
                select(Case)
                .where(Case.status == "M1_WAITING_30_DAYS")
                .where(Case.updated_at < deadline)
            )
        ).scalars().all()

        count = 0
        for case in cases:
            created = await self.notifications.emit(
                event_code="CLAIM_30_DAYS_EXPIRED",
                case_id=case.id,
                payload={"case_number": case.case_number},
                dedupe_key=f"case:{case.id}:claim-30-days-expired",
            )
            if created:
                count += 1
        return count