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
    allow_legacy_security_key_fallback: bool = False

    legal_key_rate: float = 0.16
    storage_dir: str = "./storage"
    max_document_upload_mb: int = 20
    quarantine_rejected_uploads: bool = True
    upload_quarantine_retention_days: int = 7
    document_access_grant_ttl_seconds: int = 180
    document_access_max_active_grants: int = 5
    payment_webhook_secret: str = "dev-payment-secret"
    public_base_url: str = "http://localhost:8000"
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


settings = Settings()
