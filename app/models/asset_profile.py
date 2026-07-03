import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssetProfile(Base):
    """Per-asset config. Overrides GlobalProfile where defined."""

    __tablename__ = "asset_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # ticker exacto en LuxAlgo: "MES", "MJY", "6J"
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    name: Mapped[Optional[str]] = mapped_column(String(200))
    # '"ticker": "MJY"' — shown in UI when creating a strategy
    pine_script_config: Mapped[Optional[str]] = mapped_column(String(100))
    contract_type: Mapped[Optional[str]] = mapped_column(String(30))
    # futures_micro, futures_large, stocks

    session_config_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # days_enabled CONVENTION: Sunday=0 (cron/%w). 0=Sun,1=Mon,...,6=Sat.
    # So [1,2,3,4,5]=Mon-Fri and [0,1,2,3,4,5]=Sun-Fri. SessionValidator
    # reads the current day with strftime("%w") to match this.
    # MES pit session:
    # {"timezone": "America/New_York", "days_enabled": [1,2,3,4,5],
    #  "entry_start": "09:30", "entry_end": "15:45",
    #  "next_day_end": false, "avoid_open_minutes": 30,
    #  "force_flat_time": "15:55", "allow_overnight": false,
    #  "allow_exits_outside_window": true}
    # MJY 24h session:
    # {"timezone": "America/New_York", "days_enabled": [0,1,2,3,4,5],
    #  "entry_start": "18:00", "entry_end": "17:00",
    #  "next_day_end": true, "allow_overnight": true,
    #  "allow_exits_outside_window": true}

    allowed_days_json: Mapped[Optional[dict]] = mapped_column(JSON)

    sl_atr_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), default=2.0)
    tp_atr_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    atr_period: Mapped[int] = mapped_column(Integer, default=14)
    atr_timeframe: Mapped[Optional[str]] = mapped_column(String(10))

    max_trades_day: Mapped[Optional[int]] = mapped_column(Integer)
    daily_loss_stop: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    max_quantity: Mapped[Optional[int]] = mapped_column(Integer)
    max_open_positions_symbol: Mapped[int] = mapped_column(Integer, default=1)
    score_minimum: Mapped[Optional[int]] = mapped_column(Integer)
    allow_reversal: Mapped[bool] = mapped_column(Boolean, default=False)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(100))
