import uuid
from datetime import datetime, time, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrategyProfile(Base):
    """Per-strategy config. Overrides AssetProfile + GlobalProfile."""

    __tablename__ = "strategy_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.strategy_id"), nullable=False, unique=True
    )

    # TradersPost dispatch
    traderspost_webhook_url: Mapped[Optional[str]] = mapped_column(Text)
    traderspost_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(20), default="paper")
    # paper, micro, limited_live, live

    # Pipeline filter config
    pipeline_config_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # {"score_minimum": 70, "filters": {
    #   "volume_relative": {"enabled": false, "weight": 30},
    #   "atr_normalized":  {"enabled": false, "weight": 25},
    #   "vwap_position":   {"enabled": false, "weight": 25},
    #   "time_of_day":     {"enabled": false, "weight": 20},
    #   "hmm_regime":      {"enabled": false}
    # }}

    # SL/TP (overrides asset_profile)
    sl_atr_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), default=1.5)
    tp_atr_multiplier: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    atr_period: Mapped[Optional[int]] = mapped_column(Integer)
    atr_timeframe: Mapped[Optional[str]] = mapped_column(String(10))

    # Schedule overrides (only define what differs from asset_profile)
    # NX-23: columnas muertas eliminadas (profile_name/routing/allowed_*/
    # timezone/days/entry_*_time/cooldown/daily_profit_lock)
    allow_exits_outside_window: Mapped[Optional[bool]] = mapped_column(Boolean)
    allow_overnight: Mapped[Optional[bool]] = mapped_column(Boolean)
    force_flat_time: Mapped[Optional[time]] = mapped_column(Time)
    max_holding_minutes: Mapped[Optional[int]] = mapped_column(Integer)

    # Risk overrides
    max_trades_day: Mapped[Optional[int]] = mapped_column(Integer)
    daily_loss_stop: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    max_quantity: Mapped[Optional[int]] = mapped_column(Integer)
    max_open_positions_symbol: Mapped[Optional[int]] = mapped_column(Integer)
    allow_reversal: Mapped[Optional[bool]] = mapped_column(Boolean)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(100))
