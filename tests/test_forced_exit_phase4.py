"""Fase 4 — forced exit dispatch + sweep (autonomous closes via the Fase-2 gate)."""
import uuid
from datetime import datetime, time, timezone, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.global_profile import GlobalProfile
from app.models.position_state import PositionState
from app.models.strategy import Strategy
from app.models.webhook_delivery import WebhookDelivery
from app.services.forced_exit import dispatch_forced_exit, exit_manager_sweep

UTC = timezone.utc


def _long_position(strategy_id="fx", opened_minutes_ago=120, symbol="MESU2026", now=None):
    now = now or datetime.now(UTC)
    return PositionState(
        strategy_id=strategy_id, account_id="paper_default", symbol=symbol,
        state="LONG", state_source="estimated", direction="long", quantity=1,
        risk_plan_json={"opened_at": (now - timedelta(minutes=opened_minutes_ago)).isoformat()},
    )


@pytest.mark.asyncio
async def test_dispatch_forced_exit_dry_run(db: AsyncSession):
    strat = Strategy(strategy_id="fx", name="FX", asset_symbol="MES",
                     timeframe="5m", status="paper", enabled=True)
    pos = _long_position()
    db.add_all([strat, pos])
    await db.flush()

    config = {"traderspost_webhook_url": None, "dry_run": True,
              "traderspost_enabled": False, "sl_atr_multiplier": 1.5,
              "timezone": "America/New_York"}
    result = await dispatch_forced_exit(db, pos, strat, config, "max_holding", settings)

    # Safe mode → DRY_RUN, no real send
    assert result.status == "DRY_RUN"
    # Position now EXITING
    p = (await db.execute(select(PositionState).where(
        PositionState.account_id == "paper_default",
        PositionState.symbol == "MESU2026"))).scalar_one()
    assert p.state == "EXITING"
    # Decision EXIT_ONLY + delivery DRY_RUN + audit FORCED_EXIT
    dec = (await db.execute(select(StrategyDecision).where(
        StrategyDecision.strategy_id == "fx"))).scalars().first()
    assert dec is not None and dec.outcome == "EXIT_ONLY"
    deliv = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.strategy_id == "fx"))).scalars().first()
    assert deliv is not None and deliv.status == "DRY_RUN"
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "FORCED_EXIT"))).scalars().first()
    assert audit is not None


@pytest.mark.asyncio
async def test_sweep_dispatches_due_position(db: AsyncSession):
    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True,
                         max_holding_minutes=60))
    db.add(Strategy(strategy_id="sw", name="SW", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(_long_position(strategy_id="sw", opened_minutes_ago=120))
    await db.commit()

    n = await exit_manager_sweep(db, settings, now=datetime.now(UTC))
    assert n == 1
    p = (await db.execute(select(PositionState).where(
        PositionState.symbol == "MESU2026"))).scalar_one()
    assert p.state == "EXITING"


@pytest.mark.asyncio
async def test_sweep_skips_not_due(db: AsyncSession):
    # NOTE: GlobalProfile.force_flat_time defaults to 15:55 at the column level,
    # so passing None does NOT disable EOD flat — SQLAlchemy applies the default
    # and stores 15:55. The sweep is therefore time-sensitive, so we pin `now`
    # to a deterministic in-session time well before 15:55 ET; otherwise this
    # test is flaky and fails whenever it runs after 15:55 ET.
    now = datetime(2026, 6, 17, 14, 0, tzinfo=UTC)  # Wed 10:00 ET (in session)
    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True,
                         max_holding_minutes=600, force_flat_time=None))
    db.add(Strategy(strategy_id="nd", name="ND", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    # opened 10 min ago → max_holding 600 not due; 10:00 ET < 15:55 EOD; no
    # asset session_config → overnight check skipped. Nothing is due.
    pos = _long_position(strategy_id="nd", opened_minutes_ago=10, now=now)
    db.add(pos)
    await db.commit()

    n = await exit_manager_sweep(db, settings, now=now)
    assert n == 0
    p = (await db.execute(select(PositionState).where(
        PositionState.symbol == "MESU2026"))).scalar_one()
    assert p.state == "LONG"
