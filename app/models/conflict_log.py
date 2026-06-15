import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConflictLog(Base):
    __tablename__ = "conflict_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    strategy_id_a: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_id_b: Mapped[Optional[str]] = mapped_column(String(100))
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)

    direction_a: Mapped[Optional[str]] = mapped_column(String(10))
    direction_b: Mapped[Optional[str]] = mapped_column(String(10))
    score_a: Mapped[Optional[int]] = mapped_column(Integer)
    score_b: Mapped[Optional[int]] = mapped_column(Integer)

    signal_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("normalized_signals.id"), nullable=False
    )
    signal_b_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("normalized_signals.id"), nullable=True
    )

    conflict_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resolution: Mapped[str] = mapped_column(String(50), nullable=False)
    # a_wins, b_wins, both_rejected
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
