import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyDecision(Base):
    __tablename__ = "strategy_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    normalized_signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("normalized_signals.id"), nullable=False
    )
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)

    outcome: Mapped[str] = mapped_column(String(30), nullable=False)
    # APPROVE, BLOCK, PAPER_ONLY, MICRO_ONLY, REDUCE_SIZE,
    # IGNORE_DUPLICATE, EXIT_ONLY, FLATTEN_ONLY, QUEUE_FOR_REVIEW, ERROR

    block_reason: Mapped[Optional[str]] = mapped_column(String(100))
    block_level: Mapped[Optional[int]] = mapped_column(Integer)  # 1-5
    reason_detail: Mapped[Optional[str]] = mapped_column(Text)
    score: Mapped[Optional[int]] = mapped_column(Integer)
    score_breakdown_json: Mapped[Optional[dict]] = mapped_column(JSON)

    # Full per-level pipeline execution trace
    pipeline_execution_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # {"level_1": {"passed": true, "checks": {...}},
    #  "level_2": {"passed": false, "failed_at": "2.2",
    #              "reason": "outside_trading_window"},
    #  "level_3": {"skipped": true}, ...}

    # SL/TP calculated (only when outcome=APPROVE)
    # FIX-D4 — Numeric(20,10): el 7º decimal de FX (6J tick 5e-7) no se trunca.
    sl_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 10))
    tp_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 10))
    atr_value: Mapped[Optional[float]] = mapped_column(Numeric(20, 10))
    sl_multiplier_used: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    market_data_provider: Mapped[Optional[str]] = mapped_column(String(50))

    config_snapshot_json: Mapped[Optional[dict]] = mapped_column(JSON)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_strategy_decisions_normalized_signal_id", "normalized_signal_id"),
        Index("ix_strategy_decisions_strategy_id", "strategy_id"),
        Index("ix_strategy_decisions_outcome", "outcome"),
        Index("ix_strategy_decisions_created_at", "created_at"),
    )
