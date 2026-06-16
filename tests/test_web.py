"""Web UI smoke + flow tests.

Uses the `client` fixture (httpx AsyncClient with test DB override).
Redirects are NOT auto-followed → POST handlers asserted via 303 + Location.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.asset_profile import AssetProfile
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.models.symbol_map import SymbolMap


@pytest.fixture(autouse=True)
def _authenticated(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Attach a valid session cookie so the protected UI routes return 200.

    These tests exercise UI behavior, not auth; auth itself is covered in
    test_auth.py. Token is signed and verified with the same SESSION_SECRET.
    """
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_session_secret_web")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


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


# ---------------------------------------------------------------------------
# Batch action over multiple strategies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_action_pauses_multiple(client: AsyncClient, db: AsyncSession) -> None:
    for sid in ("ba1", "ba2"):
        db.add(Strategy(strategy_id=sid, name=sid, status="paper", enabled=True))
    await db.commit()

    resp = await client.post("/ui/strategies/batch-action", data={
        "action": "pause", "selected": ["ba1", "ba2"],
    })
    assert resp.status_code == 303

    result = await db.execute(select(Strategy).where(Strategy.strategy_id.in_(["ba1", "ba2"])))
    statuses = {s.strategy_id: s.status for s in result.scalars().all()}
    assert statuses == {"ba1": "paused", "ba2": "paused"}


# ---------------------------------------------------------------------------
# Symbol map create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_symbol_map_create(client: AsyncClient, db: AsyncSession) -> None:
    resp = await client.post("/ui/symbol-map/new", data={
        "tv_symbol": "MNQ", "mapped_symbol": "MNQU2025",
        "exchange": "CME", "contract_type": "futures_micro",
        "expiry_date": "2025-09-19",
    })
    assert resp.status_code == 303
    result = await db.execute(select(SymbolMap).where(SymbolMap.tv_symbol == "MNQ"))
    sm = result.scalar_one_or_none()
    assert sm is not None
    assert sm.pine_script_config == '"ticker": "MNQ"'


# ---------------------------------------------------------------------------
# Asset update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_asset_update(client: AsyncClient, db: AsyncSession) -> None:
    db.add(AssetProfile(
        symbol="MES", name="Micro S&P", contract_type="futures_micro",
        pine_script_config='"ticker": "MES"', sl_atr_multiplier=2.0, score_minimum=65,
    ))
    await db.commit()

    resp = await client.post("/ui/assets/MES", data={
        "sl_atr_multiplier": "2.5", "score_minimum": "75", "atr_period": "21",
    })
    assert resp.status_code == 303
    result = await db.execute(select(AssetProfile).where(AssetProfile.symbol == "MES"))
    a = result.scalar_one()
    assert float(a.sl_atr_multiplier) == 2.5
    assert a.score_minimum == 75


# ---------------------------------------------------------------------------
# Settings update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_update_changes_mode(client: AsyncClient, db: AsyncSession) -> None:
    from app.models.global_profile import GlobalProfile

    resp = await client.post("/ui/settings", data={
        "mode": "defensive", "max_open_positions": "3", "score_minimum": "80",
    })
    assert resp.status_code == 303
    result = await db.execute(select(GlobalProfile).where(GlobalProfile.active.is_(True)))
    gp = result.scalar_one()
    assert gp.mode == "defensive"
    assert gp.max_open_positions == 3


# ---------------------------------------------------------------------------
# Template create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_template_create(client: AsyncClient, db: AsyncSession) -> None:
    from app.models.strategy_template import StrategyTemplate

    resp = await client.post("/ui/strategy-templates/new", data={
        "name": "My Template", "strategy_type": "trend_following",
        "sl_atr_multiplier": "1.5", "score_minimum": "70",
    })
    assert resp.status_code == 303
    result = await db.execute(
        select(StrategyTemplate).where(StrategyTemplate.name == "My Template")
    )
    assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Position flatten / lock / unlock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_position_flatten_lock_unlock(client: AsyncClient, db: AsyncSession) -> None:
    from app.models.position_state import PositionState

    pos = PositionState(
        strategy_id="p_strat", account_id="paper_1", symbol="MESU2025",
        state="LONG", state_source="estimated", quantity=1,
    )
    db.add(pos)
    await db.commit()
    await db.refresh(pos)

    # Flatten → EXITING
    resp = await client.post(f"/ui/positions/{pos.id}/flatten")
    assert resp.status_code == 303
    result = await db.execute(select(PositionState).where(PositionState.id == pos.id))
    assert result.scalar_one().state == "EXITING"

    # Lock → LOCKED
    resp = await client.post(f"/ui/positions/{pos.id}/lock")
    assert resp.status_code == 303
    result = await db.execute(select(PositionState).where(PositionState.id == pos.id))
    assert result.scalar_one().state == "LOCKED"

    # Unlock → restores EXITING
    resp = await client.post(f"/ui/positions/{pos.id}/unlock")
    assert resp.status_code == 303
    result = await db.execute(select(PositionState).where(PositionState.id == pos.id))
    assert result.scalar_one().state == "EXITING"


# ---------------------------------------------------------------------------
# Signal detail page renders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal_detail_renders(client: AsyncClient, db: AsyncSession) -> None:
    import uuid as _uuid
    from datetime import datetime, timezone
    from app.models.raw_signal import RawSignal
    from app.models.normalized_signal import NormalizedSignal
    from app.models.decision import StrategyDecision

    raw = RawSignal(strategy_id="sd", payload_json={"ticker": "MES"}, token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, strategy_id="sd", ticker_received="MES",
        mapped_symbol="MESU2025", action="buy", sentiment="long",
        signal_ts=datetime.now(timezone.utc), dedupe_key=_uuid.uuid4().hex,
    )
    db.add(norm)
    await db.flush()
    decision = StrategyDecision(
        normalized_signal_id=norm.id, strategy_id="sd", outcome="APPROVE",
        score=100, sl_price=5488.0, atr_value=8.0,
        pipeline_execution_json={"level_1": {"outcome": "CONTINUE"}},
    )
    db.add(decision)
    await db.commit()

    resp = await client.get(f"/ui/signals/{decision.id}")
    assert resp.status_code == 200
    assert "MESU2025" in resp.text
    assert "APPROVE" in resp.text
