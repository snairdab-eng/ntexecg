import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawSignal(Base):
    __tablename__ = "raw_signals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), default="luxalgo")
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Individual payload fields stored exactly as received (never modified)
    ticker_received: Mapped[Optional[str]] = mapped_column(String(50))
    action: Mapped[Optional[str]] = mapped_column(String(20))
    sentiment: Mapped[Optional[str]] = mapped_column(String(20))
    quantity_raw: Mapped[Optional[str]] = mapped_column(String(20))
    price_raw: Mapped[Optional[str]] = mapped_column(String(50))
    time_raw: Mapped[Optional[str]] = mapped_column(String(50))
    interval_raw: Mapped[Optional[str]] = mapped_column(String(20))

    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    headers_json: Mapped[Optional[dict]] = mapped_column(JSON)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    token_valid: Mapped[bool] = mapped_column(Boolean, default=False)

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_raw_signals_strategy_id", "strategy_id"),
        Index("ix_raw_signals_received_at", "received_at"),
    )
