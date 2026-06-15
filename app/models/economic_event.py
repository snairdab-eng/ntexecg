import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EconomicEvent(Base):
    """News event cache. TTL managed by NewsFilter service."""

    __tablename__ = "economic_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    impact: Mapped[str] = mapped_column(String(10), nullable=False)
    # high, medium, low
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    actual: Mapped[Optional[str]] = mapped_column(String(50))
    forecast: Mapped[Optional[str]] = mapped_column(String(50))
    previous: Mapped[Optional[str]] = mapped_column(String(50))
    source: Mapped[Optional[str]] = mapped_column(String(50))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_economic_events_event_time", "event_time"),
        Index("ix_economic_events_impact", "impact"),
    )
