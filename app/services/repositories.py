"""Database repository functions.
One function per query pattern. No business logic here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.global_profile import GlobalProfile
from app.models.asset_profile import AssetProfile
from app.models.market_data_status import MarketDataStatus
from app.models.position_state import PositionState
from app.models.strategy import Strategy
from app.models.symbol_map import SymbolMap


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

async def get_strategy_by_id(db: AsyncSession, strategy_id: str) -> Strategy | None:
    result = await db.execute(
        select(Strategy).where(Strategy.strategy_id == strategy_id)
    )
    return result.scalar_one_or_none()


async def create_strategy(
    db: AsyncSession,
    strategy_id: str,
    name: str,
    asset_symbol: str | None = None,
) -> Strategy:
    strategy = Strategy(
        strategy_id=strategy_id,
        name=name,
        asset_symbol=asset_symbol,
        status="candidate",
        enabled=False,
    )
    db.add(strategy)
    await db.flush()
    return strategy


# ---------------------------------------------------------------------------
# Symbol Mapper
# CRITICAL: exact match only — WHERE tv_symbol = :tv_symbol AND active = true
# NO string manipulation. NO prefix logic. NO transformations.
# ---------------------------------------------------------------------------

async def get_active_symbol_map(db: AsyncSession, tv_symbol: str) -> SymbolMap | None:
    result = await db.execute(
        select(SymbolMap).where(
            SymbolMap.tv_symbol == tv_symbol,
            SymbolMap.active.is_(True),
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Global Profile
# ---------------------------------------------------------------------------

async def get_global_profile(db: AsyncSession) -> GlobalProfile | None:
    result = await db.execute(
        select(GlobalProfile).where(GlobalProfile.active.is_(True)).limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Asset Profile
# ---------------------------------------------------------------------------

async def get_asset_profile(db: AsyncSession, symbol: str) -> AssetProfile | None:
    result = await db.execute(
        select(AssetProfile).where(
            AssetProfile.symbol == symbol,
            AssetProfile.active.is_(True),
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Position State
# ---------------------------------------------------------------------------

async def get_position_state(
    db: AsyncSession,
    strategy_id: str,
    account_id: str,
    symbol: str,
) -> PositionState:
    """Return existing position state or a transient FLAT default (not persisted)."""
    result = await db.execute(
        select(PositionState).where(
            PositionState.account_id == account_id,
            PositionState.symbol == symbol,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    return PositionState(
        account_id=account_id,
        symbol=symbol,
        strategy_id=strategy_id,
        state="FLAT",
        state_source="estimated",
    )


# ---------------------------------------------------------------------------
# Strategy Decision
# ---------------------------------------------------------------------------

async def create_strategy_decision(db: AsyncSession, **kwargs: Any) -> StrategyDecision:
    decision = StrategyDecision(**kwargs)
    db.add(decision)
    await db.flush()
    return decision


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

async def create_audit_log(db: AsyncSession, **kwargs: Any) -> AuditLog:
    log = AuditLog(**kwargs)
    db.add(log)
    await db.flush()
    return log


# ---------------------------------------------------------------------------
# Market Data Status
# ---------------------------------------------------------------------------

async def upsert_market_data_status(
    db: AsyncSession,
    symbol: str,
    **kwargs: Any,
) -> MarketDataStatus:
    result = await db.execute(
        select(MarketDataStatus).where(MarketDataStatus.symbol == symbol)
    )
    status = result.scalar_one_or_none()
    if status is None:
        status = MarketDataStatus(symbol=symbol, **kwargs)
        db.add(status)
    else:
        for key, value in kwargs.items():
            setattr(status, key, value)
        status.updated_at = _utcnow()
    await db.flush()
    return status
