from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_env: str = 'local'
    database_url: str = 'sqlite+aiosqlite:///./legal_bot.db'
    bot_token: str = 'CHANGE_ME'
    run_bot: bool = False
    run_scheduler: bool = False
    admin_api_token: str = 'dev-admin-token'
    legal_key_rate: float = 0.16
    storage_dir: str = './storage'
    payment_webhook_secret: str = 'dev-payment-secret'
    public_base_url: str = 'http://localhost:8000'
    payment_provider: str = 'fake'
    yookassa_shop_id: str = ''
    yookassa_secret_key: str = ''
    admin_username: str = 'admin'
    admin_password: str = ''
    admin_session_cookie: str = 'dlc_admin_session'
    allow_token_query: bool = True
    demo_mode: bool = False
    enable_recovery_actions: bool = True
    min_free_disk_mb: int = 500
    backup_dir: str = './backups'

settings = Settings()
