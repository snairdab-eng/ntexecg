"""PositionService tests — estimated state machine."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.services.position_service import PositionService

_STRAT = "mes_strat"
_ACCT = "paper_1"
_SYM = "MESU2025"


@pytest.mark.asyncio
async def test_get_state_creates_flat_default(db: AsyncSession) -> None:
    svc = PositionService()
    ps = await svc.get_state(db, _STRAT, _ACCT, _SYM)
    assert ps.state == "FLAT"
    assert ps.state_source == "estimated"
    assert ps.id is not None  # persisted


@pytest.mark.asyncio
async def test_get_state_returns_existing(db: AsyncSession) -> None:
    svc = PositionService()
    first = await svc.get_state(db, _STRAT, _ACCT, _SYM)
    second = await svc.get_state(db, _STRAT, _ACCT, _SYM)
    assert first.id == second.id


@pytest.mark.asyncio
async def test_entry_approved_long_to_pending_long(db: AsyncSession) -> None:
    svc = PositionService()
    ps = await svc.on_entry_approved(
        db, _STRAT, _ACCT, _SYM, "long", 1, 5500.0, uuid.uuid4()
    )
    assert ps.state == "PENDING_LONG"
    assert ps.direction == "long"
    assert ps.quantity == 1


@pytest.mark.asyncio
async def test_entry_approved_short_to_pending_short(db: AsyncSession) -> None:
    svc = PositionService()
    ps = await svc.on_entry_approved(
        db, _STRAT, _ACCT, _SYM, "short", 2, 5500.0, uuid.uuid4()
    )
    assert ps.state == "PENDING_SHORT"
    assert ps.quantity == 2


@pytest.mark.asyncio
async def test_delivery_confirmed_pending_long_to_long(db: AsyncSession) -> None:
    svc = PositionService()
    await svc.on_entry_approved(db, _STRAT, _ACCT, _SYM, "long", 1, 5500.0, uuid.uuid4())
    ps = await svc.on_delivery_confirmed(db, _STRAT, _ACCT, _SYM)
    assert ps.state == "LONG"


@pytest.mark.asyncio
async def test_delivery_confirmed_exiting_to_flat(db: AsyncSession) -> None:
    svc = PositionService()
    await svc.on_exit_approved(db, _STRAT, _ACCT, _SYM)
    ps = await svc.on_delivery_confirmed(db, _STRAT, _ACCT, _SYM)
    assert ps.state == "FLAT"
    assert ps.quantity == 0
    assert ps.direction is None


@pytest.mark.asyncio
async def test_exit_approved_to_exiting(db: AsyncSession) -> None:
    svc = PositionService()
    await svc.on_entry_approved(db, _STRAT, _ACCT, _SYM, "long", 1, 5500.0, uuid.uuid4())
    ps = await svc.on_exit_approved(db, _STRAT, _ACCT, _SYM)
    assert ps.state == "EXITING"


@pytest.mark.asyncio
async def test_flatten_manual_creates_audit(db: AsyncSession) -> None:
    svc = PositionService()
    await svc.on_entry_approved(db, _STRAT, _ACCT, _SYM, "long", 1, 5500.0, uuid.uuid4())
    ps = await svc.on_flatten_manual(db, _STRAT, _ACCT, _SYM, actor="admin")
    assert ps.state == "EXITING"

    result = await db.execute(select(AuditLog).where(AuditLog.action == "FLATTEN"))
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.actor == "admin"


@pytest.mark.asyncio
async def test_lock_then_unlock_restores_previous_state(db: AsyncSession) -> None:
    svc = PositionService()
    # Put into LONG
    await svc.on_entry_approved(db, _STRAT, _ACCT, _SYM, "long", 1, 5500.0, uuid.uuid4())
    await svc.on_delivery_confirmed(db, _STRAT, _ACCT, _SYM)  # → LONG

    locked = await svc.on_lock(db, _STRAT, _ACCT, _SYM, actor="admin")
    assert locked.state == "LOCKED"

    unlocked = await svc.on_unlock(db, _STRAT, _ACCT, _SYM, actor="admin")
    assert unlocked.state == "LONG"  # restored

    # Both audit entries written
    result = await db.execute(select(AuditLog))
    actions = {log.action for log in result.scalars().all()}
    assert "LOCK" in actions
    assert "UNLOCK" in actions


@pytest.mark.asyncio
async def test_unlock_noop_when_not_locked(db: AsyncSession) -> None:
    svc = PositionService()
    await svc.on_entry_approved(db, _STRAT, _ACCT, _SYM, "long", 1, 5500.0, uuid.uuid4())
    ps = await svc.on_unlock(db, _STRAT, _ACCT, _SYM, actor="admin")
    # Was PENDING_LONG, not LOCKED → unchanged
    assert ps.state == "PENDING_LONG"
