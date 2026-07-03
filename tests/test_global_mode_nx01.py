"""NX-01 — the global system brake must survive the StrategyProfile merge.

Regression test for the P0 found in REVISION_ARQUITECTURA_2026-07-02.md:
ConfigResolver overwrote config["mode"] (GlobalProfile.mode: paused/flatten_only)
with StrategyProfile.mode (maturity: paper/micro/...), so Level 1.1 never saw the
global brake. These tests go END-TO-END resolver → pipeline with real DB rows —
unlike the unit tests in test_filter_pipeline.py, which inject the config dict
directly and therefore never caught the bug.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.global_profile import GlobalProfile
from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.filter_pipeline import FilterPipeline

STRATEGY_ID = "nx01_strat"


async def _seed(db: AsyncSession, global_mode: str) -> Strategy:
    """GlobalProfile(mode=global_mode) + Strategy(paper) + StrategyProfile(paper)."""
    db.add(GlobalProfile(profile_name="default", mode=global_mode))
    strategy = Strategy(
        strategy_id=STRATEGY_ID,
        name="NX-01 e2e",
        asset_symbol="MES",
        timeframe="5m",
        status="paper",
        enabled=True,
    )
    db.add(strategy)
    await db.flush()
    # Every real strategy has a profile — the profile merge is what caused the bug.
    db.add(StrategyProfile(strategy_id=STRATEGY_ID, mode="paper"))
    await db.flush()
    return strategy


def _signal(action: str = "buy") -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id=STRATEGY_ID,
        ticker_received="MES",
        mapped_symbol="MESU2025",
        action=action,
        sentiment="long" if action == "buy" else "flat",
        price=5500.0,
        signal_ts=datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex,
    )


@pytest.mark.asyncio
async def test_global_paused_blocks_entry_e2e(db: AsyncSession, market_data_service):
    """GlobalProfile.mode=paused + strategy WITH profile ⇒ entry BLOCK global_paused."""
    strategy = await _seed(db, "paused")
    config = await ConfigResolver().resolve(db, STRATEGY_ID, "MES")

    # The profile merge still owns "mode" (maturity) — the brake is separate.
    assert config["mode"] == "paper"
    assert config["global_mode"] == "paused"

    result = await FilterPipeline(market_data_service).evaluate(
        db, _signal("buy"), strategy, config
    )
    assert result.outcome == "BLOCK"
    assert result.block_reason == "global_paused"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_global_flatten_only_blocks_entry_e2e(db: AsyncSession, market_data_service):
    """GlobalProfile.mode=flatten_only ⇒ entry BLOCK global_flatten_only."""
    strategy = await _seed(db, "flatten_only")
    config = await ConfigResolver().resolve(db, STRATEGY_ID, "MES")

    result = await FilterPipeline(market_data_service).evaluate(
        db, _signal("buy"), strategy, config
    )
    assert result.outcome == "BLOCK"
    assert result.block_reason == "global_flatten_only"
    assert result.block_level == 1


@pytest.mark.asyncio
async def test_global_normal_does_not_block_entry_e2e(db: AsyncSession, market_data_service):
    """GlobalProfile.mode=normal ⇒ the L1.1 brake does not fire."""
    strategy = await _seed(db, "normal")
    config = await ConfigResolver().resolve(db, STRATEGY_ID, "MES")

    result = await FilterPipeline(market_data_service).evaluate(
        db, _signal("buy"), strategy, config
    )
    # Mock provider: active=True, ATR=8.0 → full pipeline approves the entry.
    assert result.outcome == "APPROVE"
    assert result.sl_price is not None  # fail-closed invariant intact


@pytest.mark.asyncio
async def test_global_paused_exit_passes_e2e(db: AsyncSession, market_data_service):
    """Global paused + EXIT ⇒ L1.1 lets it through (exits prioritized)."""
    strategy = await _seed(db, "paused")
    config = await ConfigResolver().resolve(db, STRATEGY_ID, "MES")

    result = await FilterPipeline(market_data_service).evaluate(
        db, _signal("exit"), strategy, config
    )
    assert not (result.outcome == "BLOCK" and result.block_level == 1)
    assert result.outcome == "APPROVE"  # exits skip L3-L5


@pytest.mark.asyncio
async def test_kill_switch_semantics_unchanged(db: AsyncSession):
    """NX-01 must not alter dry_run (OR) / traderspost_enabled (AND) merging."""
    db.add(GlobalProfile(
        profile_name="default", mode="paused",
        dry_run=False, traderspost_enabled=True,
    ))
    strategy = Strategy(
        strategy_id=STRATEGY_ID, name="NX-01 ks", asset_symbol="MES",
        status="paper", enabled=True,
    )
    db.add(strategy)
    await db.flush()
    db.add(StrategyProfile(
        strategy_id=STRATEGY_ID, mode="paper",
        dry_run=True,               # any level asking dry_run wins (OR)
        traderspost_enabled=True,   # AND with global
    ))
    await db.flush()

    config = await ConfigResolver().resolve(db, STRATEGY_ID, "MES")
    assert config["dry_run"] is True            # OR semantics intact
    assert config["traderspost_enabled"] is True  # AND semantics intact
    assert config["global_mode"] == "paused"
