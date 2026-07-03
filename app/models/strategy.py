import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="luxalgo")

    # asset_symbol: ticker base ("MES", "MJY") — same as tv_symbol in SymbolMap
    asset_symbol: Mapped[Optional[str]] = mapped_column(String(50))

    timeframe: Mapped[Optional[str]] = mapped_column(String(20))
    strategy_type: Mapped[str] = mapped_column(String(50), default="unknown")
    # trend_following, momentum_continuation, mean_reversion,
    # breakout, scalping, hybrid, unknown

    status: Mapped[str] = mapped_column(String(30), default="candidate")
    # candidate, shadow, paper, micro, limited_live, live,
    # paused, quarantined, retired

    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # NX-22: legacy en claro (se vacía al hashear); el hash es la fuente.
    webhook_token: Mapped[Optional[str]] = mapped_column(String(128))
    webhook_token_hash: Mapped[Optional[str]] = mapped_column(String(64))
    traderspost_webhook_url: Mapped[Optional[str]] = mapped_column(Text)
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("strategy_templates.id"), nullable=True
    )
    luxalgo_metrics_json: Mapped[Optional[dict]] = mapped_column(JSON)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retired_reason: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (Index("ix_strategies_status", "status"),)
