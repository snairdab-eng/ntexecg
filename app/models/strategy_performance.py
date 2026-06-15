import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyPerformance(Base):
    """Accumulated real metrics. Updated after each StrategyDecision."""

    __tablename__ = "strategy_performance"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    total_signals_received: Mapped[int] = mapped_column(Integer, default=0)
    total_approved: Mapped[int] = mapped_column(Integer, default=0)
    total_blocked: Mapped[int] = mapped_column(Integer, default=0)
    total_signals_sent: Mapped[int] = mapped_column(Integer, default=0)

    filter_pass_rate: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    avg_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))

    blocks_level_1: Mapped[int] = mapped_column(Integer, default=0)
    blocks_level_2: Mapped[int] = mapped_column(Integer, default=0)
    blocks_level_3: Mapped[int] = mapped_column(Integer, default=0)
    blocks_level_4: Mapped[int] = mapped_column(Integer, default=0)
    blocks_level_5: Mapped[int] = mapped_column(Integer, default=0)

    top_block_reasons_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # {"outside_trading_window": 45, "score_below_threshold": 23}

    first_signal_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_signal_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
