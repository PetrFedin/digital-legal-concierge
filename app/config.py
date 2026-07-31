from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str = "sqlite+aiosqlite:///./legal_bot.db"
    bot_token: str = "CHANGE_ME"
    run_bot: bool = False
    run_scheduler: bool = False

    # Explicit API credential. It is not used for sessions, MFA encryption or
    # internal HMAC once dedicated production keys are configured.
    admin_api_token: str = "dev-admin-token"

    # Independent cryptographic domains. Previous keys use a comma-separated
    # key_id:secret keyring and are verification/decryption-only.
    session_signing_key_id: str = "session-v1"
    session_signing_key: str = ""
    session_signing_previous_keys: str = ""
    security_hmac_key_id: str = "hmac-v1"
    security_hmac_key: str = ""
    security_hmac_previous_keys: str = ""
    mfa_encryption_key_id: str = "mfa-v1"
    mfa_encryption_key: str = ""
    mfa_encryption_previous_keys: str = ""
    audit_integrity_key_id: str = "audit-v1"
    audit_integrity_key: str = ""
    audit_integrity_previous_keys: str = ""
    document_encryption_key_id: str = "documents-v1"
    document_encryption_key: str = ""
    document_encryption_previous_keys: str = ""
    backup_encryption_key_id: str = "backups-v1"
    backup_encryption_key: str = ""
    backup_encryption_previous_keys: str = ""
    allow_legacy_security_key_fallback: bool = False

    legal_key_rate: Decimal = Decimal("0.16")
    storage_dir: str = "./storage"
    max_document_upload_mb: int = 20
    quarantine_rejected_uploads: bool = True
    upload_quarantine_retention_days: int = 7
    document_access_grant_ttl_seconds: int = 180
    document_access_max_active_grants: int = 5
    payment_webhook_secret: str = "dev-payment-secret"
    max_payment_webhook_kb: int = 256
    payment_webhook_processing_timeout_seconds: int = 300
    payment_webhook_max_attempts: int = 8
    public_base_url: str = "http://localhost:8000"
    # Comma-separated CIDRs of reverse proxies that are allowed to supply
    # X-Forwarded-For / X-Real-IP / X-Forwarded-Proto. Empty means trust none.
    trusted_proxy_cidrs: str = ""
    trusted_proxy_max_hops: int = 5
    trust_forwarded_proto: bool = True
    payment_provider: str = "fake"
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    admin_username: str = "admin"
    admin_password: str = ""
    admin_session_cookie: str = "dlc_admin_session"
    allow_token_query: bool = True
    demo_mode: bool = False
    enable_recovery_actions: bool = True
    min_free_disk_mb: int = 500
    backup_dir: str = "./backups"
    max_backup_mb: int = 2048
    backup_retention_days: int = 30
    # Production readiness is fail-closed when no fully verified, restorable
    # encrypted backup exists within this recovery-point objective.
    backup_readiness_required_in_production: bool = True
    backup_max_age_hours: int = 26
    # Full AES-GCM + manifest verification is cached only while every archive
    # and the signed restore-fence retain the same filesystem fingerprint.
    backup_freshness_cache_seconds: int = 300
    backup_future_clock_skew_seconds: int = 300

    # Closed-case content retention. Discovery is non-destructive; deletion
    # always requires two different personal MFA-superadmin accounts.
    closed_case_retention_days: int = 1825
    case_retention_scan_batch_size: int = 100
    case_retention_execution_timeout_seconds: int = 900
    case_retention_dry_run: bool = True


settings = Settings()