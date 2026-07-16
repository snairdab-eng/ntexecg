import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NormalizedSignal(Base):
    __tablename__ = "normalized_signals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    raw_signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_signals.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(50), default="luxalgo")
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # ticker_received = exactly payload["ticker"], never modified
    ticker_received: Mapped[str] = mapped_column(String(50), nullable=False)

    # mapped_symbol = result of SymbolMapper direct DB lookup (NULL if no mapping)
    mapped_symbol: Mapped[Optional[str]] = mapped_column(String(50))

    action: Mapped[str] = mapped_column(String(20), nullable=False)
    sentiment: Mapped[Optional[str]] = mapped_column(String(20))
    quantity: Mapped[Optional[int]] = mapped_column(Integer)
    # FIX-D4-bis — Numeric(20,10): el precio FUENTE de la señal no trunca el 7º
    # decimal de FX (6J tick 5e-7), consistente con las columnas downstream (FIX-D4).
    price: Mapped[Optional[float]] = mapped_column(Numeric(20, 10))
    timeframe: Mapped[Optional[str]] = mapped_column(String(20))
    signal_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    signal_role: Mapped[Optional[str]] = mapped_column(String(30))
    # entry_long, entry_short, exit_long, exit_short,
    # reversal_to_long, reversal_to_short, cancel, unknown

    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    normalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_normalized_signals_strategy_id", "strategy_id"),
        Index("ix_normalized_signals_signal_ts", "signal_ts"),
    )
