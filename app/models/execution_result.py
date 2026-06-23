import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExecutionResult(Base):
    """A real executed round-trip trade, imported from the weekly broker report."""

    __tablename__ = "execution_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    signal_id: Mapped[Optional[str]] = mapped_column(String(64))
    strategy_id: Mapped[Optional[str]] = mapped_column(String(100))
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    entry_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))

    pnl: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    pnl_calc: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))
    exit_reason: Mapped[Optional[str]] = mapped_column(String(20))
    fees: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))

    matched_decision_id: Mapped[Optional[uuid.UUID]] = mapped_column()
    match_method: Mapped[str] = mapped_column(String(20), default="unmatched")

    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("row_hash", name="uq_execution_result_row_hash"),
        Index("ix_execution_results_strategy", "strategy_id"),
        Index("ix_execution_results_symbol_entry", "symbol", "entry_time"),
    )
