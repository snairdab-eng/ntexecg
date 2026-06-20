import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SymbolMap(Base):
    """Direct lookup table: tv_symbol → mapped_symbol.
    No string manipulation, no prefix logic. Exact match only.
    """

    __tablename__ = "symbol_maps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Exactly what arrives in payload["ticker"]: "MES", "6J" — NOT "MES1!" or "M6J"
    tv_symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)

    # Active contract: "MESU2025", "6JU2025"
    mapped_symbol: Mapped[str] = mapped_column(String(50), nullable=False)

    # Market-data alias (Anexo A.9.1; reglas 36, 38).
    # Read-only symbol substitution for the bridge: a micro contract reads the
    # bridge files of its more-liquid parent (e.g. MES → ES). NULL/empty means
    # "use tv_symbol itself". NEVER affects decisions or the TradersPost payload —
    # those keep using mapped_symbol. Does NOT transform prices.
    market_data_symbol: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(30), nullable=False)
    underlying_name: Mapped[Optional[str]] = mapped_column(String(100))

    # Instrument catalog (Anexo 08 #4): fixed contract properties. tick_value =
    # USD per tick; tick_size = minimum price increment. Reference data shown in
    # the strategy ficha (NTEXECG does not gate on monetary risk). Nullable.
    tick_value: Mapped[Optional[float]] = mapped_column(Numeric(12, 4))
    tick_size: Mapped[Optional[float]] = mapped_column(Numeric(14, 8))

    # '"ticker": "MES"' — exact instruction for LuxAlgo JSON alert config
    pine_script_config: Mapped[str] = mapped_column(String(100), nullable=False)

    expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(100))

    __table_args__ = (Index("ix_symbol_maps_tv_symbol_active", "tv_symbol", "active"),)
