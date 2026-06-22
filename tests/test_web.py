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
from app.models.strategy_profile import StrategyProfile
from app.models.global_profile import GlobalProfile
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


@pytest.mark.asyncio
async def test_create_strategy_with_guardrails(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Anexo 08 #2 — guardrail toggles + staleness persist in pipeline_config_json."""
    resp = await client.post("/ui/strategies/new", data={
        "strategy_id": "guard_strat",
        "name": "Guard Strategy",
        "asset_symbol": "MES",
        "timeframe": "5m",
        "initial_mode": "paper",
        "enforce_symbol_match": "1",
        "enforce_timeframe_match": "1",
        "signal_max_age_entry_seconds": "120",
        "signal_max_age_exit_seconds": "300",
    })
    assert resp.status_code == 303

    row = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "guard_strat"))).scalar_one()
    g = (row.pipeline_config_json or {}).get("guardrails", {})
    assert g.get("enforce_symbol_match") is True
    assert g.get("enforce_timeframe_match") is True
    assert g.get("signal_max_age_entry_seconds") == 120
    assert g.get("signal_max_age_exit_seconds") == 300


@pytest.mark.asyncio
async def test_create_strategy_without_guardrails_leaves_none(
    client: AsyncClient, db: AsyncSession
) -> None:
    resp = await client.post("/ui/strategies/new", data={
        "strategy_id": "plain_strat",
        "name": "Plain",
        "asset_symbol": "MES",
        "timeframe": "5m",
        "initial_mode": "paper",
    })
    assert resp.status_code == 303
    row = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "plain_strat"))).scalar_one()
    # No guardrail fields → pipeline_config_json stays None (no enforcement).
    assert row.pipeline_config_json is None


@pytest.mark.asyncio
async def test_ticker_hint_shows_tick_value(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Anexo 08 #4 — ticker hint shows the catalog tick value as reference."""
    db.add(SymbolMap(
        tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
        contract_type="futures_micro", pine_script_config='"ticker": "MES"',
        tick_value=1.25, tick_size=0.25,
    ))
    await db.commit()
    resp = await client.get("/ui/strategies/ticker-hint?asset_symbol=MES")
    assert resp.status_code == 200
    assert "1.25" in resp.text
    assert "futures_micro" in resp.text


@pytest.mark.asyncio
async def test_ticker_hint_without_catalog_no_tick(
    client: AsyncClient, db: AsyncSession
) -> None:
    resp = await client.get("/ui/strategies/ticker-hint?asset_symbol=ZZZ")
    assert resp.status_code == 200
    assert "valor de tick" not in resp.text


@pytest.mark.asyncio
async def test_update_guardrails_on_detail(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Anexo 08 #2 — edit guardrails from the strategy detail (Config tab)."""
    db.add(Strategy(strategy_id="ug_strat", name="UG", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="ug_strat", mode="paper"))
    await db.commit()

    # Set guardrails
    resp = await client.post("/ui/strategies/ug_strat/guardrails", data={
        "enforce_symbol_match": "1",
        "signal_max_age_entry_seconds": "90",
    })
    assert resp.status_code == 303
    row = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ug_strat"))).scalar_one()
    await db.refresh(row)
    g = (row.pipeline_config_json or {}).get("guardrails", {})
    assert g.get("enforce_symbol_match") is True
    assert g.get("enforce_timeframe_match") is None
    assert g.get("signal_max_age_entry_seconds") == 90

    # Detail page reflects the saved value (checkbox checked + number value)
    page = await client.get("/ui/strategies/ug_strat")
    assert page.status_code == 200
    assert 'name="enforce_symbol_match" value="1"' in page.text
    assert 'checked' in page.text

    # Clearing all removes the guardrails key
    resp = await client.post("/ui/strategies/ug_strat/guardrails", data={})
    assert resp.status_code == 303
    row2 = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ug_strat"))).scalar_one()
    await db.refresh(row2)
    assert (row2.pipeline_config_json or {}).get("guardrails") is None


@pytest.mark.asyncio
async def test_update_windows_persists_and_cleans(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Anexo 08 #5 — windows editor saves cleaned windows; invalid ones dropped."""
    import json as _json
    db.add(Strategy(strategy_id="win_strat", name="W", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="win_strat", mode="paper"))
    await db.commit()

    payload = _json.dumps([
        {"days": [0, 1, 2, 3], "start": "09:00", "end": "15:45"},
        {"days": [4], "start": "09:00", "end": "12:00", "next_day_end": False},
        {"days": [], "start": "09:00", "end": "10:00"},      # no days → dropped
        {"days": [1], "start": "", "end": "10:00"},          # no start → dropped
    ])
    resp = await client.post("/ui/strategies/win_strat/windows",
                             data={"windows_json": payload})
    assert resp.status_code == 303

    row = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "win_strat"))).scalar_one()
    await db.refresh(row)
    wins = (row.pipeline_config_json or {}).get("windows")
    assert wins is not None and len(wins) == 2
    assert wins[0] == {"days": [0, 1, 2, 3], "start": "09:00", "end": "15:45"}
    assert wins[1]["days"] == [4]

    # Detail "Ventanas" tab renders the saved windows JSON
    page = await client.get("/ui/strategies/win_strat")
    assert page.status_code == 200
    assert "Añadir ventana" in page.text

    # Empty submission clears the windows key
    resp = await client.post("/ui/strategies/win_strat/windows",
                             data={"windows_json": "[]"})
    assert resp.status_code == 303
    row2 = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "win_strat"))).scalar_one()
    await db.refresh(row2)
    assert (row2.pipeline_config_json or {}).get("windows") is None


@pytest.mark.asyncio
async def test_config_resolver_injects_strategy_windows(
    db: AsyncSession
) -> None:
    """ConfigResolver puts per-strategy windows into session_config_json."""
    from app.services.config_resolver import ConfigResolver
    db.add(Strategy(strategy_id="cr_win", name="CR", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(
        strategy_id="cr_win", mode="paper",
        pipeline_config_json={"windows": [
            {"days": [0, 1, 2, 3, 4], "start": "09:30", "end": "15:45"}]}))
    await db.commit()

    config = await ConfigResolver().resolve(db, "cr_win", "MES")
    sc = config.get("session_config_json") or {}
    assert "windows" in sc
    assert sc["windows"][0]["start"] == "09:30"


@pytest.mark.asyncio
async def test_create_generates_webhook_url_and_regenerate(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Anexo 08 — creation generates a token; detail shows the NTEXECG URL."""
    resp = await client.post("/ui/strategies/new", data={
        "strategy_id": "wh_strat", "name": "WH",
        "asset_symbol": "MES", "timeframe": "5m", "initial_mode": "paper",
    })
    assert resp.status_code == 303
    row = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "wh_strat"))).scalar_one()
    assert row.webhook_token

    page = await client.get("/ui/strategies/wh_strat")
    assert page.status_code == 200
    assert "/webhooks/luxalgo/wh_strat?token=" in page.text
    assert row.webhook_token in page.text

    # Regenerate produces a different token
    old = row.webhook_token
    resp = await client.post("/ui/strategies/wh_strat/regenerate-token")
    assert resp.status_code == 303
    row2 = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "wh_strat"))).scalar_one()
    await db.refresh(row2)
    assert row2.webhook_token and row2.webhook_token != old


@pytest.mark.asyncio
async def test_create_full_ficha(client: AsyncClient, db: AsyncSession) -> None:
    """Machote completo: definición+BT, riesgo-ref, ruteo, EOD se persisten."""
    resp = await client.post("/ui/strategies/new", data={
        "strategy_id": "ficha_strat", "name": "Ficha",
        "asset_symbol": "MES", "timeframe": "15m", "initial_mode": "paper",
        "descripcion": "Estrategia de prueba MES", "responsable": "Sergio",
        "toolkit": "Signals & Overlays", "trigger": "Confirmation Any",
        "filter_1": "Contrarian", "filter_2": "Overflow",
        "exit_condition": "Builtin-Exits",
        "bt_start": "2025-08-20", "bt_end": "2026-06-09",
        "num_trades": "56", "winrate": "96.43", "profit_factor": "10.6",
        "net_profit": "5928", "max_drawdown": "11.78",
        "frequency": "~1/5d", "order_size": "unitario",
        "dedup_seconds": "5", "stop_required": "1", "stop_ticks": "40",
        "risk_usd_max_operation": "50", "max_contracts": "2",
        "target_account": "PAPER_FUTURES", "routing_notes": "nota",
        "allow_exits_outside_window": "1", "force_flat_time": "16:00",
    })
    assert resp.status_code == 303

    strat = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "ficha_strat"))).scalar_one()
    await db.refresh(strat)
    assert strat.notes == "Estrategia de prueba MES"
    m = strat.luxalgo_metrics_json
    assert m["responsable"] == "Sergio"
    assert m["toolkit"] == "Signals & Overlays"
    assert m["backtest"]["num_trades"] == 56
    assert m["backtest"]["winrate"] == 96.43

    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ficha_strat"))).scalar_one()
    await db.refresh(prof)
    pc = prof.pipeline_config_json
    assert pc["risk_reference"]["stop_required"] is True
    assert pc["risk_reference"]["stop_ticks"] == 40
    assert pc["risk_reference"]["max_contracts"] == 2
    assert pc["dedup_seconds"] == 5
    assert pc["routing"]["target_account"] == "PAPER_FUTURES"
    assert prof.allow_exits_outside_window is True
    assert prof.force_flat_time is not None
    assert prof.force_flat_time.strftime("%H:%M") == "16:00"

    page = await client.get("/ui/strategies/ficha_strat")
    assert page.status_code == 200
    assert "PAPER_FUTURES" in page.text
    assert "Sergio" in page.text


@pytest.mark.asyncio
async def test_strategy_dispatch_arm_requires_confirmar(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Fase 2 — arming real dispatch needs CONFIRMAR; disarm is free."""
    db.add(Strategy(strategy_id="arm1", name="A", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="arm1", mode="paper",
                           dry_run=True, traderspost_enabled=False))
    await db.commit()

    async def _prof():
        row = (await db.execute(select(StrategyProfile).where(
            StrategyProfile.strategy_id == "arm1"))).scalar_one()
        await db.refresh(row)
        return row

    # arm WITHOUT CONFIRMAR → unchanged (still safe)
    r = await client.post("/ui/strategies/arm1/dispatch",
                          data={"action": "arm", "confirm": "x"})
    assert r.status_code == 303
    p1 = await _prof()
    assert p1.traderspost_enabled is False and p1.dry_run is True

    # arm WITH CONFIRMAR → armed
    r = await client.post("/ui/strategies/arm1/dispatch",
                          data={"action": "arm", "confirm": "CONFIRMAR"})
    assert r.status_code == 303
    p2 = await _prof()
    assert p2.traderspost_enabled is True and p2.dry_run is False

    # disarm → back to dry_run (no confirm needed)
    r = await client.post("/ui/strategies/arm1/dispatch", data={"action": "disarm"})
    assert r.status_code == 303
    p3 = await _prof()
    assert p3.dry_run is True


@pytest.mark.asyncio
async def test_global_dispatch_arm_and_confirm(
    client: AsyncClient, db: AsyncSession
) -> None:
    async def _gp():
        row = (await db.execute(select(GlobalProfile))).scalars().first()
        if row is not None:
            await db.refresh(row)
        return row

    # arm with CONFIRMAR
    r = await client.post("/ui/settings/dispatch",
                          data={"action": "arm", "confirm": "CONFIRMAR"})
    assert r.status_code == 303
    gp = await _gp()
    assert gp.traderspost_enabled is True and gp.dry_run is False

    # disarm
    await client.post("/ui/settings/dispatch", data={"action": "disarm"})
    # arm without CONFIRMAR → stays disarmed
    await client.post("/ui/settings/dispatch", data={"action": "arm", "confirm": "no"})
    gp2 = await _gp()
    assert gp2.dry_run is True


@pytest.mark.asyncio
async def test_dry_run_badge_present(client: AsyncClient) -> None:
    """Test env has TRADERSPOST_ENABLED=false → DRY RUN badge visible."""
    page = await client.get("/ui/strategies")
    assert page.status_code == 200
    assert "DRY RUN" in page.text


@pytest.mark.asyncio
async def test_delivery_alerts_partial(client: AsyncClient, db: AsyncSession) -> None:
    """Fase 2 — dashboard banner appears when a delivery is FAILED (24h)."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    from app.models.normalized_signal import NormalizedSignal
    from app.models.decision import StrategyDecision
    from app.models.webhook_delivery import WebhookDelivery

    # No failures → no banner
    r = await client.get("/ui/partials/delivery-alerts")
    assert r.status_code == 200
    assert "FALLIDO" not in r.text

    norm = NormalizedSignal(
        raw_signal_id=_uuid.uuid4(), strategy_id="del1", ticker_received="MES",
        mapped_symbol="MESU2026", action="buy", sentiment="long", price=5500.0,
        signal_ts=_dt.now(_tz.utc), dedupe_key=_uuid.uuid4().hex)
    db.add(norm)
    await db.flush()
    dec = StrategyDecision(normalized_signal_id=norm.id, strategy_id="del1",
                           outcome="APPROVE")
    db.add(dec)
    await db.flush()
    db.add(WebhookDelivery(
        decision_id=dec.id, strategy_id="del1", destination="traderspost",
        payload_json={}, status="FAILED", error_message="http_500", attempts=3))
    await db.commit()

    r2 = await client.get("/ui/partials/delivery-alerts")
    assert r2.status_code == 200
    assert "FALLIDO" in r2.text
    assert "del1" in r2.text
