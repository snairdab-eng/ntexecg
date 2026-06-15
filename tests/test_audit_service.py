"""AuditService tests — never raises (REQ-0801)."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.services.audit_service import AuditService


@pytest.mark.asyncio
async def test_log_creates_entry(db: AsyncSession) -> None:
    svc = AuditService()
    entry = await svc.log(
        db, actor="admin", action="UPDATE", object_type="Strategy",
        object_id="mes_strat", reason="changed mode",
    )
    assert entry is not None
    assert entry.actor == "admin"
    assert entry.action == "UPDATE"


@pytest.mark.asyncio
async def test_log_strategy_change(db: AsyncSession) -> None:
    svc = AuditService()
    entry = await svc.log_strategy_change(
        db, actor="admin", strategy_id="mes_strat",
        old_data={"status": "paper"}, new_data={"status": "live"},
        action="STATUS_CHANGE",
    )
    assert entry.object_type == "Strategy"
    assert entry.old_value_json == {"status": "paper"}
    assert entry.new_value_json == {"status": "live"}


@pytest.mark.asyncio
async def test_log_system_event_actor_is_system(db: AsyncSession) -> None:
    svc = AuditService()
    entry = await svc.log_system_event(
        db, action="WEBHOOK_BLOCKED", object_type="System", object_id="strat_x",
        details={"reason": "invalid_token"},
    )
    assert entry.actor == "system"
    assert entry.new_value_json == {"reason": "invalid_token"}


@pytest.mark.asyncio
async def test_log_never_raises_on_failure(db: AsyncSession) -> None:
    """If the write fails, log() returns None instead of raising."""
    svc = AuditService()
    # action is NOT NULL — pass None to force a DB-level failure on flush
    result = await svc.log(
        db, actor="system", action=None, object_type="System",  # type: ignore[arg-type]
    )
    # Must not raise; returns None
    assert result is None


@pytest.mark.asyncio
async def test_entries_persisted(db: AsyncSession) -> None:
    svc = AuditService()
    await svc.log(db, actor="admin", action="LOCK", object_type="PositionState",
                  object_id="acct:MESU2025")
    result = await db.execute(select(AuditLog).where(AuditLog.action == "LOCK"))
    assert result.scalar_one_or_none() is not None
