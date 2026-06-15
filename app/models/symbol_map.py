import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Index, String, Text
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

    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(30), nullable=False)
    underlying_name: Mapped[Optional[str]] = mapped_column(String(100))

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
