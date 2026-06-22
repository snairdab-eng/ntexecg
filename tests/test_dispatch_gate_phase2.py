"""Fase 2 — layered dispatch gate.

Real HTTP send only when: env TRADERSPOST_ENABLED ∧ traderspost_enabled
(global ∧ strategy) ∧ ¬dry_run. Otherwise dry-run (safe by default).
"""
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import resolve_effective_dry_run
from app.models.global_profile import GlobalProfile
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver


def _st(env, tp_enabled, dry_run):
    return resolve_effective_dry_run(
        SimpleNamespace(TRADERSPOST_ENABLED=env),
        {"traderspost_enabled": tp_enabled, "dry_run": dry_run},
    )


# ---------------------------------------------------------------------------
# Gate function truth table
# ---------------------------------------------------------------------------

def test_gate_real_send_only_when_all_open():
    # The ONLY combination that produces a real send (effective dry_run False)
    assert _st(env=True, tp_enabled=True, dry_run=False) is False


@pytest.mark.parametrize("env,tp,dry", [
    (False, True, False),   # env kill-switch off → dry
    (True, False, False),   # traderspost disabled → dry
    (True, True, True),     # dry_run on → dry
    (False, False, True),   # all off → dry
])
def test_gate_dry_when_any_lock_closed(env, tp, dry):
    assert _st(env=env, tp_enabled=tp, dry_run=dry) is True


def test_gate_defaults_are_safe():
    # Missing keys → treated as dry-run.
    assert resolve_effective_dry_run(SimpleNamespace(), {}) is True


# ---------------------------------------------------------------------------
# ConfigResolver kill-switch merge (global ∧ strategy / dry_run OR)
# ---------------------------------------------------------------------------

async def _seed(db, *, g_dry, g_tp, s_dry, s_tp):
    db.add(GlobalProfile(mode="normal", dry_run=g_dry, traderspost_enabled=g_tp,
                         score_minimum=70, active=True))
    db.add(Strategy(strategy_id="gx", name="GX", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="gx", mode="paper",
                           dry_run=s_dry, traderspost_enabled=s_tp))
    await db.commit()


@pytest.mark.asyncio
async def test_resolver_both_enabled(db: AsyncSession):
    await _seed(db, g_dry=False, g_tp=True, s_dry=False, s_tp=True)
    c = await ConfigResolver().resolve(db, "gx", "MES")
    assert c["dry_run"] is False and c["traderspost_enabled"] is True


@pytest.mark.asyncio
async def test_resolver_strategy_restricts_dry(db: AsyncSession):
    # global allows real, strategy keeps dry_run → effective dry_run True
    await _seed(db, g_dry=False, g_tp=True, s_dry=True, s_tp=True)
    c = await ConfigResolver().resolve(db, "gx", "MES")
    assert c["dry_run"] is True


@pytest.mark.asyncio
async def test_resolver_strategy_cannot_escalate_enabled(db: AsyncSession):
    # global disabled, strategy enabled → effective traderspost_enabled False
    await _seed(db, g_dry=False, g_tp=False, s_dry=False, s_tp=True)
    c = await ConfigResolver().resolve(db, "gx", "MES")
    assert c["traderspost_enabled"] is False


@pytest.mark.asyncio
async def test_resolver_global_dry_cannot_be_escalated(db: AsyncSession):
    # global dry_run True, strategy dry_run False → effective dry_run stays True
    await _seed(db, g_dry=True, g_tp=True, s_dry=False, s_tp=True)
    c = await ConfigResolver().resolve(db, "gx", "MES")
    assert c["dry_run"] is True


@pytest.mark.asyncio
async def test_resolver_no_profiles_safe_defaults(db: AsyncSession):
    db.add(Strategy(strategy_id="bare", name="B", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    await db.commit()
    c = await ConfigResolver().resolve(db, "bare", "MES")
    assert c["dry_run"] is True and c["traderspost_enabled"] is False
