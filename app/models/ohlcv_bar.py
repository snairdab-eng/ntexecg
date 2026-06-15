import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OhlcvBar(Base):
    """OHLCV bar cache. Placeholder for Phase 5 — table exists, no logic yet."""

    __tablename__ = "ohlcv_bars"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    high: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    low: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    close: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    volume: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "bar_time", "provider",
                         name="uq_ohlcv_symbol_tf_time_provider"),
        Index("ix_ohlcv_bars_symbol_tf_time", "symbol", "timeframe", "bar_time"),
    )
