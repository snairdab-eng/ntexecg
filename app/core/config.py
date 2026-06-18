import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# The test suite sets APP_ENV=test (in tests/conftest.py, BEFORE this module is
# imported) so the suite NEVER loads the production .env. It reads .env.test
# instead — settings become deterministic regardless of the host pytest runs on.
_ENV_FILE = ".env.test" if os.getenv("APP_ENV") == "test" else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"
    APP_NAME: str = "NTEXECG"
    APP_VERSION: str = "1.0.0"

    DATABASE_URL: str = "postgresql+asyncpg://ntexecg:password_dev@localhost:5432/ntexecg"
    POSTGRES_USER: str = "ntexecg"
    POSTGRES_PASSWORD: str = "password_dev"
    POSTGRES_DB: str = "ntexecg"

    SECRET_KEY: str = "dev_secret_key_change_in_production_min_32_chars"
    WEBHOOK_TOKEN_SALT: str = "dev_salt_change_in_production_min_32_chars"
    LUXALGO_WEBHOOK_SECRET: str = "dev_global_token"

    # Web UI authentication
    UI_USERNAME: str = "admin"
    UI_PASSWORD: str = ""          # bcrypt hash of the admin password
    SESSION_SECRET: str = ""       # HS256 signing key for session JWTs

    TRADERSPOST_ENABLED: bool = False
    DRY_RUN: bool = True

    DEFAULT_TIMEZONE: str = "America/New_York"
    LOG_LEVEL: str = "DEBUG"

    MAX_RETRY_ATTEMPTS: int = 3
    RETRY_BACKOFF_SECONDS: int = 1

    MARKET_DATA_PROVIDER: str = "yfinance"
    NTBRIDGE_PATH: str = "/mnt/ntbridge"
    NTBRIDGE_HEARTBEAT_MAX_AGE: int = 60
    MARKET_DATA_FALLBACK_ENABLED: bool = False
    NEWS_CACHE_TTL_MINUTES: int = 60


settings = Settings()
