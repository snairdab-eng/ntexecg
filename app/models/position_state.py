import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PositionState(Base):
    """Estimated position state based on signals sent.
    State is ESTIMATED in MVP — not confirmed by broker.
    UI always indicates this explicitly.
    """

    __tablename__ = "position_states"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[Optional[str]] = mapped_column(String(100))
    account_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Active contract symbol: "MJYU2025", "MESU2025"
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)

    state: Mapped[str] = mapped_column(String(30), default="FLAT")
    # FLAT, PENDING_LONG, LONG, PENDING_SHORT, SHORT,
    # EXITING, REVERSING, LOCKED, UNKNOWN

    # "estimated" (MVP) or "confirmed" (future broker API)
    state_source: Mapped[str] = mapped_column(String(20), default="estimated")

    direction: Mapped[Optional[str]] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    # FIX-D4 — Numeric(20,10): el 7º decimal de FX (6J tick 5e-7) no se trunca.
    entry_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 10))
    entry_signal_id: Mapped[Optional[uuid.UUID]] = mapped_column()
    risk_plan_json: Mapped[Optional[dict]] = mapped_column(JSON)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (UniqueConstraint("account_id", "symbol", name="uq_position_account_symbol"),)
