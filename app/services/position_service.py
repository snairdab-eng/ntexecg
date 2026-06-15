"""PositionService — estimated position state machine (REQ-0701).

All states are state_source="estimated" in Phase 1 MVP. NTEXECG does not
receive execution confirmations from brokers; the UI must always indicate
that position state is estimated.

State transitions:
  on_entry_approved   FLAT → PENDING_LONG / PENDING_SHORT
  on_delivery_confirmed  PENDING_LONG → LONG, PENDING_SHORT → SHORT, EXITING → FLAT
  on_exit_approved    any → EXITING
  on_flatten_manual   any → EXITING (+ AuditLog FLATTEN)
  on_lock             any → LOCKED (+ AuditLog LOCK), prev state saved
  on_unlock           LOCKED → previous state (+ AuditLog UNLOCK)
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position_state import PositionState
from app.services.audit_service import AuditService


class PositionService:
    def __init__(self, audit: AuditService | None = None) -> None:
        self._audit = audit or AuditService()

    async def get_state(
        self, db: AsyncSession, strategy_id: str, account_id: str, symbol: str
    ) -> PositionState:
        """Return existing position state, or create a persisted FLAT default."""
        existing = await self._find(db, account_id, symbol)
        if existing is not None:
            return existing

        position = PositionState(
            strategy_id=strategy_id,
            account_id=account_id,
            symbol=symbol,
            state="FLAT",
            state_source="estimated",
            quantity=0,
        )
        db.add(position)
        await db.flush()
        return position

    async def on_entry_approved(
        self,
        db: AsyncSession,
        strategy_id: str,
        account_id: str,
        symbol: str,
        direction: str,
        qty: int,
        price: float | None,
        signal_id,
    ) -> PositionState:
        """FLAT → PENDING_LONG / PENDING_SHORT."""
        position = await self.get_state(db, strategy_id, account_id, symbol)
        position.state = "PENDING_LONG" if direction == "long" else "PENDING_SHORT"
        position.direction = direction
        position.quantity = qty
        position.entry_price = price
        position.entry_signal_id = signal_id
        await db.flush()
        return position

    async def on_delivery_confirmed(
        self, db: AsyncSession, strategy_id: str, account_id: str, symbol: str
    ) -> PositionState:
        """PENDING_LONG → LONG, PENDING_SHORT → SHORT, EXITING → FLAT."""
        position = await self.get_state(db, strategy_id, account_id, symbol)
        transitions = {
            "PENDING_LONG": "LONG",
            "PENDING_SHORT": "SHORT",
            "EXITING": "FLAT",
        }
        new_state = transitions.get(position.state)
        if new_state:
            position.state = new_state
            if new_state == "FLAT":
                position.quantity = 0
                position.direction = None
                position.entry_price = None
                position.entry_signal_id = None
        await db.flush()
        return position

    async def on_exit_approved(
        self, db: AsyncSession, strategy_id: str, account_id: str, symbol: str
    ) -> PositionState:
        """Any state → EXITING."""
        position = await self.get_state(db, strategy_id, account_id, symbol)
        position.state = "EXITING"
        await db.flush()
        return position

    async def on_flatten_manual(
        self, db: AsyncSession, strategy_id: str, account_id: str, symbol: str,
        actor: str,
    ) -> PositionState:
        """Any state → EXITING + AuditLog(FLATTEN)."""
        position = await self.get_state(db, strategy_id, account_id, symbol)
        old_state = position.state
        position.state = "EXITING"
        await db.flush()
        await self._audit.log(
            db, actor=actor, action="FLATTEN", object_type="PositionState",
            object_id=f"{account_id}:{symbol}",
            old_value={"state": old_state}, new_value={"state": "EXITING"},
        )
        return position

    async def on_lock(
        self, db: AsyncSession, strategy_id: str, account_id: str, symbol: str,
        actor: str,
    ) -> PositionState:
        """Any state → LOCKED + AuditLog(LOCK). Previous state saved for unlock."""
        position = await self.get_state(db, strategy_id, account_id, symbol)
        old_state = position.state
        # Save prev state in risk_plan_json (no dedicated column in MVP schema)
        plan = dict(position.risk_plan_json or {})
        plan["prev_state_before_lock"] = old_state
        position.risk_plan_json = plan
        position.state = "LOCKED"
        await db.flush()
        await self._audit.log(
            db, actor=actor, action="LOCK", object_type="PositionState",
            object_id=f"{account_id}:{symbol}",
            old_value={"state": old_state}, new_value={"state": "LOCKED"},
        )
        return position

    async def on_unlock(
        self, db: AsyncSession, strategy_id: str, account_id: str, symbol: str,
        actor: str,
    ) -> PositionState:
        """LOCKED → previous state + AuditLog(UNLOCK)."""
        position = await self.get_state(db, strategy_id, account_id, symbol)
        if position.state != "LOCKED":
            return position
        plan = dict(position.risk_plan_json or {})
        prev_state = plan.pop("prev_state_before_lock", "FLAT")
        position.risk_plan_json = plan
        position.state = prev_state
        await db.flush()
        await self._audit.log(
            db, actor=actor, action="UNLOCK", object_type="PositionState",
            object_id=f"{account_id}:{symbol}",
            old_value={"state": "LOCKED"}, new_value={"state": prev_state},
        )
        return position

    @staticmethod
    async def _find(
        db: AsyncSession, account_id: str, symbol: str
    ) -> PositionState | None:
        result = await db.execute(
            select(PositionState).where(
                PositionState.account_id == account_id,
                PositionState.symbol == symbol,
            )
        )
        return result.scalar_one_or_none()
