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
    # NX-21 — señales con strategy_id desconocido: en prod poner false para
    # que un token global filtrado no pueda crear estrategias basura.
    ALLOW_STRATEGY_AUTOCREATE: bool = True

    # Web UI authentication
    UI_USERNAME: str = "admin"
    UI_PASSWORD: str = ""          # bcrypt hash of the admin password
    SESSION_SECRET: str = ""       # HS256 signing key for session JWTs
    # SEC-1 Tarea 2 — 2FA TOTP opcional. Vacío = apagado (comportamiento actual).
    # Provisioning: `python -m scripts.setup_totp`.
    UI_TOTP_SECRET: str = ""

    TRADERSPOST_ENABLED: bool = False
    DRY_RUN: bool = True

    LOG_LEVEL: str = "DEBUG"

    # NX-23: MAX_RETRY_ATTEMPTS / RETRY_BACKOFF_SECONDS / DEFAULT_TIMEZONE /
    # MARKET_DATA_FALLBACK_ENABLED / NEWS_CACHE_TTL_MINUTES eliminados — sin
    # lectores (los reintentos viven en GlobalProfile desde NX-15).

    MARKET_DATA_PROVIDER: str = "yfinance"
    NTBRIDGE_PATH: str = "/mnt/ntbridge"
    NTBRIDGE_HEARTBEAT_MAX_AGE: int = 60

    # How often the MarketBarsUpdater persists fresh bridge bars into ohlcv_bars.
    MARKET_BARS_UPDATE_MINUTES: int = 15

    # HMM market-regime (Fase 6).
    MODELS_DIR: str = "models"            # where trained HMM models are stored
    HMM_REGIME_TIMEFRAME: str = "1h"      # timeframe the regime is detected on
    HMM_N_STATES: int = 3                 # trending_bull / trending_bear / ranging
    HMM_TRAIN_ENABLED: bool = True        # weekly training job on/off
    HMM_TRAIN_DAY_OF_WEEK: str = "sun"    # APScheduler cron day_of_week
    HMM_TRAIN_HOUR: int = 2               # local-UTC hour for the weekly retrain


settings = Settings()
