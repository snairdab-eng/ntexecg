"""Lote 4 — salidas/estados (NX-06, NX-07, NX-27, NX-08).

NX-06: el botón Flatten de la UI despacha un cierre REAL vía el gate Fase-2
       (antes solo marcaba EXITING y decía "Flatten enviado").
NX-07: forced exits (EOD/max_holding/overnight/reversal/flatten) llegan a
       TODOS los perfiles de riesgo habilitados, cada uno por su gate.
NX-27: una estrategia quarantined/retired/candidate NO despacha ni el cierre
       del reversal — la señal cae al pipeline y se registra el BLOCK normal.
NX-08: delivery FAILED (reintentos agotados) → estado honesto: salida → UNKNOWN
       (bloquea entradas en L3), entrada → FLAT. DRY_RUN no cambia (PENDING/
       EXITING como siempre). + reporte de posiciones estancadas.

Invariantes verificados en cada test de dispatch: el payload es SOLO cierre
(action="exit", sin stopLoss/takeProfit/sentiment) y respeta el kill-switch
(env de test cerrado → DRY_RUN; por-destino con la semántica de capas).
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services.forced_exit import dispatch_forced_exit, find_stale_positions
from app.services.market_data_service import MarketDataService
from app.services.traderspost_client import TradersPostClient, WebhookDeliveryResult

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lote4")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


class _MockMD:
    async def get_bars(self, *a, **kw):
        return []

    async def get_atr(self, *a, **kw):
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return True


_MD = MarketDataService(_MockMD())


def _position(state="LONG", strategy_id="fx", symbol="MESU2026",
              quantity=1, updated_at=None) -> PositionState:
    direction = ("long" if "LONG" in state else
                 "short" if "SHORT" in state else None)
    kw = {}
    if updated_at is not None:
        kw["updated_at"] = updated_at
    return PositionState(
        strategy_id=strategy_id, account_id="paper_default", symbol=symbol,
        state=state, state_source="estimated", direction=direction,
        quantity=quantity, **kw,
    )


async def _seed(db: AsyncSession, *, status="paper", strategy_id="fx",
                webhook="https://tp/base", profiles=None,
                pos_state="LONG") -> PositionState:
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True))
    db.add(Strategy(strategy_id=strategy_id, name=strategy_id,
                    asset_symbol="MES", timeframe="5m", status=status,
                    enabled=True))
    cfg = {"profiles": profiles} if profiles else None
    db.add(StrategyProfile(strategy_id=strategy_id, mode="paper",
                           traderspost_webhook_url=webhook,
                           pipeline_config_json=cfg))
    pos = _position(state=pos_state, strategy_id=strategy_id)
    db.add(pos)
    await db.commit()
    return pos


async def _deliveries(db: AsyncSession, strategy_id="fx") -> list:
    db.expire_all()
    rows = await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.strategy_id == strategy_id))
    return list(rows.scalars().all())


async def _pos_state(db: AsyncSession, symbol="MESU2026") -> str:
    db.expire_all()
    p = (await db.execute(select(PositionState).where(
        PositionState.account_id == "paper_default",
        PositionState.symbol == symbol))).scalar_one()
    return p.state


def _assert_close_only(payload: dict) -> None:
    """Invariante del lote: el payload SOLO cierra — nunca abre."""
    assert payload["action"] == "exit"
    assert "stopLoss" not in payload
    assert "takeProfit" not in payload
    assert "sentiment" not in payload


# ---------------------------------------------------------------------------
# NX-06 — Flatten UI despacha de verdad (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ui_flatten_dispatches_exit(client: AsyncClient, db: AsyncSession):
    pos = await _seed(db)
    r = await client.post(f"/ui/positions/{pos.id}/flatten")
    assert r.status_code in (200, 303)

    delivs = await _deliveries(db)
    assert len(delivs) == 1, (
        "flatten no despachó nada (el bug NX-06: solo marcaba EXITING)")
    # Kill-switch respetado: env de test cerrado → DRY_RUN, sin HTTP.
    assert delivs[0].status == "DRY_RUN"
    _assert_close_only(delivs[0].payload_json)
    assert await _pos_state(db) == "EXITING"


@pytest.mark.asyncio
async def test_ui_flatten_flat_position_is_noop(client: AsyncClient, db: AsyncSession):
    """FLAT → nada que cerrar: 0 deliveries y el estado NO cambia (antes el
    botón lo pasaba a EXITING sin enviar nada)."""
    pos = await _seed(db, pos_state="FLAT")
    r = await client.post(f"/ui/positions/{pos.id}/flatten")
    assert r.status_code in (200, 303)
    assert await _deliveries(db) == []
    assert await _pos_state(db) == "FLAT"


# ---------------------------------------------------------------------------
# NX-07 — forced exit a TODOS los perfiles (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forced_exit_reaches_all_profiles(db: AsyncSession):
    pos = await _seed(db, profiles=[
        {"name": "perfil1", "enabled": True, "webhook_url": "https://tp/p1"},
    ])
    strat = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "fx"))).scalar_one()
    config = {
        "traderspost_webhook_url": "https://tp/base", "dry_run": True,
        "traderspost_enabled": False, "sl_atr_multiplier": 1.5,
        "timezone": "America/New_York",
        "profiles": [{"name": "perfil1", "enabled": True,
                      "webhook_url": "https://tp/p1"}],
    }
    await dispatch_forced_exit(db, pos, strat, config, "max_holding", settings)

    delivs = await _deliveries(db)
    assert len(delivs) == 2, (
        f"forced exit llegó a {len(delivs)} destino(s) — el perfil quedó "
        "abierto (bug NX-07)")
    assert {d.destination for d in delivs} == {"traderspost", "traderspost:perfil1"}
    for d in delivs:
        _assert_close_only(d.payload_json)
        assert d.status == "DRY_RUN"   # kill-switch de test cerrado


@pytest.mark.asyncio
async def test_forced_exit_per_profile_gate(db: AsyncSession, monkeypatch):
    """El gate se evalúa POR destino: base armada envía real, perfil en
    dry_run queda DRY_RUN (un perfil solo restringe — NX-02)."""
    calls: list[dict] = []

    async def _fake_send(self, webhook_url, payload, signal_role,
                         dry_run, signal_ts=None, **kw):
        calls.append({"url": webhook_url, "dry_run": dry_run})
        return WebhookDeliveryResult(
            status="DRY_RUN" if dry_run else "SENT",
            payload_json=payload, url_masked=webhook_url, attempts=1)

    monkeypatch.setattr(TradersPostClient, "send", _fake_send)

    pos = await _seed(db)
    strat = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "fx"))).scalar_one()
    config = {
        "traderspost_webhook_url": "https://tp/base", "dry_run": False,
        "traderspost_enabled": True, "sl_atr_multiplier": 1.5,
        "timezone": "America/New_York",
        "profiles": [{"name": "seguro", "enabled": True,
                      "webhook_url": "https://tp/p1", "dry_run": True}],
    }
    armed = SimpleNamespace(TRADERSPOST_ENABLED=True, DRY_RUN=False)
    await dispatch_forced_exit(db, pos, strat, config, "max_holding", armed)

    assert len(calls) == 2
    by_url = {c["url"]: c["dry_run"] for c in calls}
    assert by_url["https://tp/base"] is False    # base armada → real
    assert by_url["https://tp/p1"] is True       # perfil restringe → dry


# ---------------------------------------------------------------------------
# NX-27 — reversal respeta L1.2 (ADVERSARIAL)
# ---------------------------------------------------------------------------

_SELL = {"ticker": "MES", "action": "sell", "sentiment": "short",
         "quantity": "1", "price": "5500.00", "interval": "5"}


async def _fire(db: AsyncSession, sid: str, payload: dict):
    raw = RawSignal(source="luxalgo", strategy_id=sid, payload_json=payload,
                    token_valid=True)
    db.add(raw)
    await db.flush()
    decision = await process_signal(db, sid, raw.id, dict(payload), _MD)
    await db.flush()
    return decision


@pytest.mark.asyncio
async def test_reversal_quarantined_no_dispatch(db: AsyncSession):
    """Señal opuesta a una posición abierta de estrategia QUARANTINED: no se
    despacha ni el cierre — BLOCK normal en L1.2 con 0 deliveries."""
    await _seed(db, status="quarantined")
    decision = await _fire(db, "fx", _SELL)

    assert decision.outcome == "BLOCK"
    assert decision.block_reason == "strategy_quarantined"
    delivs = await _deliveries(db)
    assert delivs == [], (
        f"estrategia quarantined despachó {len(delivs)} delivery(s) "
        "(bug NX-27: el reversal cerraba antes de validar L1)")
    assert await _pos_state(db) == "LONG"   # el estimado no se toca


@pytest.mark.asyncio
async def test_reversal_paused_still_closes_only(db: AsyncSession):
    """paused NO corta el cierre (las salidas tienen prioridad): cierra la
    posición y bloquea la entrada opuesta — comportamiento preservado."""
    await _seed(db, status="paused")
    decision = await _fire(db, "fx", _SELL)

    assert decision.outcome == "BLOCK"
    assert decision.block_reason == "reversal_not_allowed"
    delivs = await _deliveries(db)
    assert len(delivs) == 1
    _assert_close_only(delivs[0].payload_json)


# ---------------------------------------------------------------------------
# NX-08 — FAILED → estado honesto (ADVERSARIAL)
# ---------------------------------------------------------------------------

async def _patch_send_failed(monkeypatch):
    async def _fail(self, webhook_url, payload, signal_role, dry_run,
                    signal_ts=None, **kw):
        return WebhookDeliveryResult(
            status="FAILED", payload_json=payload, url_masked=webhook_url,
            attempts=10, error_message="http_500")
    monkeypatch.setattr(TradersPostClient, "send", _fail)


@pytest.mark.asyncio
async def test_exit_failed_marks_unknown(db: AsyncSession, monkeypatch):
    await _patch_send_failed(monkeypatch)
    pos = await _seed(db)
    strat = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "fx"))).scalar_one()
    config = {"traderspost_webhook_url": "https://tp/base", "dry_run": False,
              "traderspost_enabled": True, "sl_atr_multiplier": 1.5,
              "timezone": "America/New_York"}
    await dispatch_forced_exit(db, pos, strat, config, "max_holding", settings)

    state = await _pos_state(db)
    assert state == "UNKNOWN", (
        f"salida FAILED dejó la posición en {state} (bug NX-08: EXITING "
        "eterno sin alarma); UNKNOWN bloquea entradas en L3")


@pytest.mark.asyncio
async def test_entry_failed_reverts_to_flat(db: AsyncSession, monkeypatch):
    await _patch_send_failed(monkeypatch)
    # estrategia paper SIN posición previa; entrada APPROVE con envío FAILED
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True))
    db.add(Strategy(strategy_id="ef", name="EF", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="ef", mode="paper",
                           traderspost_webhook_url="https://tp/base"))
    await db.commit()

    buy = {"ticker": "MES", "action": "buy", "sentiment": "long",
           "quantity": "1", "price": "5500.00", "interval": "5"}
    decision = await _fire(db, "ef", buy)

    assert decision.outcome == "APPROVE"
    state = await _pos_state(db)
    assert state == "FLAT", (
        f"entrada FAILED dejó la posición en {state} (bug NX-08: PENDING "
        "eterno de algo que nunca llegó al broker)")


@pytest.mark.asyncio
async def test_dry_run_entry_still_pending(db: AsyncSession):
    """Comportamiento preservado: en DRY_RUN la entrada queda PENDING_LONG
    (aprobada pero no enviada) — NX-08 solo actúa sobre FAILED."""
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True))
    db.add(Strategy(strategy_id="dr", name="DR", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="dr", mode="paper",
                           traderspost_webhook_url="https://tp/base"))
    await db.commit()
    buy = {"ticker": "MES", "action": "buy", "sentiment": "long",
           "quantity": "1", "price": "5500.00", "interval": "5"}
    decision = await _fire(db, "dr", buy)
    assert decision.outcome == "APPROVE"
    assert await _pos_state(db) == "PENDING_LONG"


@pytest.mark.asyncio
async def test_find_stale_positions(db: AsyncSession):
    old = datetime.now(UTC) - timedelta(minutes=30)
    db.add(_position(state="PENDING_LONG", strategy_id="s1",
                     symbol="AAA", updated_at=old))
    db.add(_position(state="EXITING", strategy_id="s2",
                     symbol="BBB", updated_at=old))
    db.add(_position(state="PENDING_SHORT", strategy_id="s3",
                     symbol="CCC"))          # fresca → no
    db.add(_position(state="LONG", strategy_id="s4",
                     symbol="DDD", updated_at=old))  # abierta confirmada → no
    await db.commit()

    stale = await find_stale_positions(db, older_than_minutes=15)
    assert {p.symbol for p in stale} == {"AAA", "BBB"}
