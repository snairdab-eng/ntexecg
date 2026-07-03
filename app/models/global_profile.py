import uuid
from datetime import datetime, time, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Time
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GlobalProfile(Base):
    """System-wide config base. Single active row."""

    __tablename__ = "global_profile"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_name: Mapped[str] = mapped_column(String(50), default="default")

    # Schedule defaults
    timezone: Mapped[str] = mapped_column(String(50), default="America/New_York")
    allow_exits_outside_window: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_overnight: Mapped[bool] = mapped_column(Boolean, default=False)
    force_flat_time: Mapped[Optional[time]] = mapped_column(Time, default=time(15, 55))

    # News filter
    news_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    news_window_minutes: Mapped[int] = mapped_column(Integer, default=30)

    # Risk
    max_open_positions: Mapped[int] = mapped_column(Integer, default=5)
    daily_loss_stop: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    max_holding_minutes: Mapped[Optional[int]] = mapped_column(Integer)

    # Score
    score_minimum: Mapped[int] = mapped_column(Integer, default=70)

    # System mode
    mode: Mapped[str] = mapped_column(String(30), default="normal")
    # normal, defensive, flatten_only, paused
    traderspost_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    # TradersPost retry
    retry_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, default=1)
    entry_signal_timeout_secs: Mapped[int] = mapped_column(Integer, default=30)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
