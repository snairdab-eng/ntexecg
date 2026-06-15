import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    """Immutable. Written by audit_service on every config change.
    Never raise on failure — log the error and continue.
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor: Mapped[str] = mapped_column(String(100), default="system")

    action: Mapped[str] = mapped_column(String(50), nullable=False)
    # CREATE, UPDATE, DELETE, STATUS_CHANGE, ENABLE, DISABLE,
    # PAUSE, RESUME, QUARANTINE, RETIRE, FLATTEN, LOCK, UNLOCK,
    # TOKEN_GENERATED, WEBHOOK_BLOCKED, GLOBAL_MODE_CHANGE, CLONE

    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Strategy, StrategyProfile, AssetProfile, GlobalProfile,
    # SymbolMap, GlobalSetting, PositionState, System

    object_id: Mapped[Optional[str]] = mapped_column(String(100))
    old_value_json: Mapped[Optional[dict]] = mapped_column(JSON)
    new_value_json: Mapped[Optional[dict]] = mapped_column(JSON)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_audit_logs_actor", "actor"),
        Index("ix_audit_logs_object_type_id", "object_type", "object_id"),
        Index("ix_audit_logs_created_at", "created_at"),
    )
