from app.domain.retention import case_retention_service as _base_service
from app.domain.retention.backup_aware_case_retention_service import (
    BackupAwareCaseRetentionService,
)

# Existing modules import CaseRetentionService directly from
# case_retention_service. Replace that public class only after the original
# module is fully initialized, preserving all constants and error types.
_base_service.CaseRetentionService = BackupAwareCaseRetentionService

CaseRetentionError = _base_service.CaseRetentionError
CaseRetentionService = BackupAwareCaseRetentionService

__all__ = ["CaseRetentionError", "CaseRetentionService"]
