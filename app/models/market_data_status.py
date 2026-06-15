import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketDataStatus(Base):
    """Provider status per symbol. Updated by APScheduler every 30s."""

    __tablename__ = "market_data_status"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    heartbeat_age_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    last_atr_5m: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    last_atr_15m: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    last_atr_1h: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    bars_available_json: Mapped[Optional[dict]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
