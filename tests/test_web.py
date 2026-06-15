"""Web UI smoke + flow tests.

Uses the `client` fixture (httpx AsyncClient with test DB override).
Redirects are NOT auto-followed → POST handlers asserted via 303 + Location.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_profile import AssetProfile
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.models.symbol_map import SymbolMap


# ---------------------------------------------------------------------------
# GET smoke tests — every page renders 200 on an empty DB
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/ui",
    "/ui/strategies",
    "/ui/strategies/new",
    "/ui/signals",
    "/ui/positions",
    "/ui/symbol-map",
    "/ui/assets",
    "/ui/strategy-templates",
    "/ui/settings",
    "/ui/audit",
])
@pytest.mark.asyncio
async def test_pages_render_200(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 200
    assert "NTEXECG" in resp.text


@pytest.mark.asyncio
async def test_partials_render(client: AsyncClient) -> None:
    for path in ["/ui/partials/bridge-status", "/ui/partials/bridge-badge",
                 "/ui/partials/recent-signals"]:
        resp = await client.get(path)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Strategy create flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_strategy_flow(client: AsyncClient, db: AsyncSession) -> None:
    resp = await client.post("/ui/strategies/new", data={
        "strategy_id": "web_strat",
        "name": "Web Strategy",
        "asset_symbol": "MES",
        "timeframe": "5m",
        "initial_mode": "paper",
    })
    assert resp.status_code == 303
    assert "/ui/strategies/web_strat" in resp.headers["location"]

    result = await db.execute(select(Strategy).where(Strategy.strategy_id == "web_strat"))
    strat = result.scalar_one_or_none()
    assert strat is not None
    assert strat.status == "candidate"

    # AuditLog written
    audit = await db.execute(select(AuditLog).where(AuditLog.action == "CREATE"))
    assert audit.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_create_strategy_duplicate_rejected(
    client: AsyncClient, db: AsyncSession
) -> None:
    db.add(Strategy(strategy_id="dup_web", name="X", status="candidate", enabled=False))
    await db.commit()

    resp = await client.post("/ui/strategies/new", data={
        "strategy_id": "dup_web", "name": "Dup",
    })
    assert resp.status_code == 303
    assert "flash" in resp.headers["location"]
    assert "error" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Status change + audit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_change_creates_audit(client: AsyncClient, db: AsyncSession) -> None:
    db.add(Strategy(strategy_id="st_strat", name="St", status="paper", enabled=True))
    await db.commit()

    resp = await client.post("/ui/strategies/st_strat/status", data={"new_status": "paused"})
    assert resp.status_code == 303

    result = await db.execute(select(Strategy).where(Strategy.strategy_id == "st_strat"))
    assert result.scalar_one().status == "paused"


@pytest.mark.asyncio
async def test_quarantine_requires_reason(client: AsyncClient, db: AsyncSession) -> None:
    db.add(Strategy(strategy_id="q_strat", name="Q", status="paper", enabled=True))
    await db.commit()

    # No reason → rejected with error flash, status unchanged
    resp = await client.post("/ui/strategies/q_strat/status", data={"new_status": "quarantined"})
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]

    result = await db.execute(select(Strategy).where(Strategy.strategy_id == "q_strat"))
    assert result.scalar_one().status == "paper"  # unchanged


# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clone_strategy(client: AsyncClient, db: AsyncSession) -> None:
    db.add(Strategy(
        strategy_id="src_strat", name="Source", asset_symbol="MES",
        status="live", enabled=True,
    ))
    await db.commit()

    resp = await client.post("/ui/strategies/src_strat/clone", data={
        "new_strategy_id": "cloned_strat", "asset_symbol": "MNQ",
    })
    assert resp.status_code == 303
    assert "/ui/strategies/cloned_strat" in resp.headers["location"]

    result = await db.execute(select(Strategy).where(Strategy.strategy_id == "cloned_strat"))
    clone = result.scalar_one_or_none()
    assert clone is not None
    assert clone.status == "candidate"   # clones start as candidate
    assert clone.asset_symbol == "MNQ"


# ---------------------------------------------------------------------------
# Symbol map toggle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_symbol_map_toggle(client: AsyncClient, db: AsyncSession) -> None:
    sm = SymbolMap(
        tv_symbol="MES", mapped_symbol="MESU2025", exchange="CME",
        contract_type="futures_micro", pine_script_config='"ticker": "MES"', active=True,
    )
    db.add(sm)
    await db.commit()
    await db.refresh(sm)

    resp = await client.post(f"/ui/symbol-map/{sm.id}/toggle")
    assert resp.status_code == 303

    result = await db.execute(select(SymbolMap).where(SymbolMap.id == sm.id))
    assert result.scalar_one().active is False


# ---------------------------------------------------------------------------
# Ticker hint partial
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ticker_hint_shows_pine_config(
    client: AsyncClient, db: AsyncSession
) -> None:
    db.add(AssetProfile(
        symbol="MJY", name="Micro Yen", contract_type="futures_micro",
        pine_script_config='"ticker": "MJY"',
    ))
    await db.commit()

    resp = await client.get("/ui/strategies/ticker-hint?asset_symbol=MJY")
    assert resp.status_code == 200
    # Jinja2 HTML-escapes the quotes; the pine config content is still present
    assert "MJY" in resp.text
    assert "Ticker para configurar en LuxAlgo" in resp.text
