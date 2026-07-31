from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.domain.retention.case_retention_service import (
    STATUS_APPROVED,
    STATUS_COMPLETED,
    STATUS_EXECUTING,
    STATUS_FAILED,
    CaseRetentionError,
    CaseRetentionService as BaseCaseRetentionService,
)
from app.security.backup_restore_fence import (
    BackupMaintenanceLock,
    BackupRestoreFenceError,
    advance_backup_restore_fence,
    purge_revoked_backups,
)


class BackupAwareCaseRetentionService(BaseCaseRetentionService):
    """Fail-closed retention execution coordinated with backup/restore.

    The exclusive backup maintenance lock is acquired before the base service
    can move the record to EXECUTING. Therefore no supported backup operation
    can snapshot a still-wrapped document DEK while retention is erasing it.
    A signed restore fence is persisted and stale archives are removed before
    the database transition that destroys wrapped keys.
    """

    async def execute_deletion(
        self,
        *,
        record_id: int,
        actor_id: int,
        now: datetime | None = None,
    ):
        operation_now = now or datetime.now(timezone.utc)
        record = await self._load_record(record_id)
        case = await self._load_case(record.case_id)

        if record.status == STATUS_COMPLETED and case.content_deleted_at is not None:
            return await super().execute_deletion(
                record_id=record_id,
                actor_id=actor_id,
                now=operation_now,
            )
        if record.status not in {STATUS_APPROVED, STATUS_EXECUTING, STATUS_FAILED}:
            return await super().execute_deletion(
                record_id=record_id,
                actor_id=actor_id,
                now=operation_now,
            )

        lock = BackupMaintenanceLock()
        await asyncio.to_thread(lock.acquire)
        try:
            # A backup may have completed while this operation was waiting for
            # the lock. Use the acquisition-time wall clock, not the earlier
            # request timestamp, so that backup is revoked before key erasure.
            fence_cutoff = datetime.now(timezone.utc)
            try:
                await asyncio.to_thread(
                    advance_backup_restore_fence,
                    fence_cutoff,
                    reason="Case content cryptographic erasure",
                    event_id=f"case:{case.id}:retention:{record.id}",
                )
                await asyncio.to_thread(purge_revoked_backups)
            except BackupRestoreFenceError as error:
                raise CaseRetentionError(
                    "Удаление заблокировано: не удалось безопасно отозвать "
                    "резервные копии, созданные до уничтожения ключей"
                ) from error

            result = await super().execute_deletion(
                record_id=record_id,
                actor_id=actor_id,
                now=operation_now,
            )

            completed_at = result.executed_at or datetime.now(timezone.utc)
            final_cutoff = max(fence_cutoff, completed_at)
            try:
                await asyncio.to_thread(
                    advance_backup_restore_fence,
                    final_cutoff,
                    reason="Case content cryptographic erasure completed",
                    event_id=f"case:{case.id}:retention:{record.id}:completed",
                )
                await asyncio.to_thread(purge_revoked_backups)
            except BackupRestoreFenceError as error:
                raise CaseRetentionError(
                    "Содержимое удалено, но финальная синхронизация backup-fence "
                    "не завершена; восстановление заблокировано до исправления"
                ) from error
            return result
        finally:
            await asyncio.to_thread(lock.release)


__all__ = ["BackupAwareCaseRetentionService"]
