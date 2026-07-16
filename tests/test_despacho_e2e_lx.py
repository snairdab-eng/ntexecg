"""E2E del flujo de despacho (webhook→decisión→payload→destinos→cierre).

Guardas de regresión del recorrido completo, DRY_RUN (sin HTTP). Fijan el
comportamiento vigente auditado en CONTRATO/AUDITORIA_Despacho_E2E_2026-07-15.md:
precios ABSOLUTOS en el payload, destinos por perfil, exits limpios y a todos
los destinos, catálogo de BLOCK por nivel que NO llega a payload, token inválido
que no procesa, y el kill-switch por capas. El test de 6J documenta el string
decimal exacto del payload (hallazgo D-2: sin redondeo al tick — lote aparte).
"""
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal, resolve_effective_dry_run
from app.models.position_state import PositionState
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services.market_data_service import MarketDataService


class _MD:
    def __init__(self, atr=8.0, active=True):
        self._atr, self._active = atr, active
    async def get_bars(self, *a, **kw): return []
    async def get_atr(self, *a, **kw): return self._atr
    async def is_active(self, symbol): return self._active


_MD_ES = MarketDataService(_MD(atr=8.0))
_MD_6J = MarketDataService(_MD(atr=0.0000013))   # ATR a escala de 6J


async def _seed(db, *, sid, asset, tv, mapped, tick, status="live",
                pipeline_config=None, base_webhook=None):
    db.add(SymbolMap(tv_symbol=tv, mapped_symbol=mapped, exchange="CME",
                     contract_type="futures_micro", tick_size=tick,
                     pine_script_config=f'"ticker": "{tv}"', active=True))
    db.add(Strategy(strategy_id=sid, name=f"Strat {sid}", asset_symbol=asset,
                    status=status, enabled=True))
    if pipeline_config is not None or base_webhook is not None:
        db.add(StrategyProfile(
            strategy_id=sid, traderspost_webhook_url=base_webhook,
            pipeline_config_json=pipeline_config or {}))
    await db.flush()


async def _raw(db, sid, body):
    from app.models.raw_signal import RawSignal
    r = RawSignal(strategy_id=sid, payload_json=body, token_valid=True)
    db.add(r)
    await db.flush()
    return r


def _body(ticker, action="buy", sentiment="long", price="5500.00",
          qty="1", interval="5"):
    return {"ticker": ticker, "action": action, "sentiment": sentiment,
            "quantity": qty, "price": price, "interval": interval}


async def _deliveries(db, sid):
    rows = await db.execute(
        select(WebhookDelivery).where(WebhookDelivery.strategy_id == sid))
    return list(rows.scalars().all())


# ---------------------------------------------------------------------------
# 1) ES entrada E2E — payload ABSOLUTO + destinos por perfil
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_es_entrada_e2e_payload_absoluto_y_destinos(db: AsyncSession):
    base_wh = "https://webhooks.traderspost.io/trading/webhook/base/tok"
    prof_wh = "https://webhooks.traderspost.io/trading/webhook/fondeadora/tok"
    await _seed(db, sid="es_e2e", asset="MES", tv="MES", mapped="MESU2025",
                tick="0.25", base_webhook=base_wh,
                pipeline_config={"profiles": [
                    {"name": "fondeadora", "enabled": True,
                     "webhook_url": prof_wh, "quantities": [1, 0, 0]}]})
    raw = await _raw(db, "es_e2e", _body("MES"))
    dec = await process_signal(db, "es_e2e", raw.id, _body("MES"), _MD_ES)
    assert dec.outcome == "APPROVE"

    ds = await _deliveries(db, "es_e2e")
    assert len(ds) == 2                                  # base + fondeadora
    assert {d.destination for d in ds} == {"traderspost", "traderspost:fondeadora"}
    for d in ds:
        p = d.payload_json
        assert p["ticker"] == "MESU2025"                # mapped, no ticker_received
        assert p["action"] == "buy"
        sp = p["stopLoss"]["stopPrice"]
        assert 5000 < sp < 5500                         # ABSOLUTO (no un offset ~12)
        assert d.status == "DRY_RUN"


# ---------------------------------------------------------------------------
# 2) 6J — formato decimal del payload (D-2: sin redondeo al tick, documentado)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_6j_payload_formato_decimal(db: AsyncSession):
    await _seed(db, sid="j6_e2e", asset="6J", tv="6J", mapped="6JU2026",
                tick="0.0000005", base_webhook="https://webhooks.traderspost.io/x/j/tok",
                pipeline_config={"backstop_points": 0.0000225,
                                 "tp_nominal_long": 15.0})
    body = _body("6J", price="0.0063545")
    raw = await _raw(db, "j6_e2e", body)
    dec = await process_signal(db, "j6_e2e", raw.id, body, _MD_6J)
    assert dec.outcome == "APPROVE"

    (d,) = await _deliveries(db, "j6_e2e")
    p = d.payload_json
    # PRECIOS ABSOLUTOS: SL = P0 − backstop (nativo), no un offset
    assert p["stopLoss"]["stopPrice"] == pytest.approx(0.0063545 - 0.0000225)
    assert p["signalPrice"] == pytest.approx(0.0063545)
    assert p["takeProfit"]["limitPrice"] > p["signalPrice"]     # long: TP arriba
    # Formato: los campos de ORDEN NO salen en notación científica en el JSON
    s = json.dumps(p)
    import re
    for campo in ("stopPrice", "limitPrice", "signalPrice"):
        m = re.search(rf'"{campo}": ([^,}}]+)', s)
        assert m and "e-" not in m.group(1).lower(), (campo, m.group(1))
    # (D-2) el valor es absoluto y ~6e-3 — el redondeo AL TICK es lote aparte.


# ---------------------------------------------------------------------------
# 3) EXIT — sin bracket/sentiment, a TODOS los destinos, posición cerrada
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exit_limpia_bracket_y_todos_los_destinos(db: AsyncSession):
    base_wh = "https://webhooks.traderspost.io/x/base/tok"
    prof_wh = "https://webhooks.traderspost.io/x/fa/tok"
    await _seed(db, sid="ex_e2e", asset="MES", tv="MES", mapped="MESU2025",
                tick="0.25", base_webhook=base_wh,
                pipeline_config={"profiles": [
                    {"name": "fa", "enabled": True, "webhook_url": prof_wh,
                     "quantities": [1, 0, 0]}]})
    body = _body("MES", action="exit", sentiment="flat", price="")
    raw = await _raw(db, "ex_e2e", body)
    dec = await process_signal(db, "ex_e2e", raw.id, body, _MD_ES)
    assert dec.outcome == "APPROVE"                     # exit exento L3/L4/L5

    ds = await _deliveries(db, "ex_e2e")
    assert len(ds) == 2                                  # cierra en TODOS los destinos
    for d in ds:
        p = d.payload_json
        assert p["action"] == "exit"
        assert "stopLoss" not in p and "takeProfit" not in p
        assert "sentiment" not in p                     # TradersPost lo rechaza en exit
        assert p["cancel"] is True                       # FIX-D3: cancela piernas C2/C3

    # FIX-D3 — el cierre queda auditado como cancelación de piernas pendientes.
    from app.models.audit_log import AuditLog
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "EXIT_CANCEL_LEGS"))).scalars().first()
    assert audit is not None
    assert audit.new_value_json["cancel_requested"] is True


# ---------------------------------------------------------------------------
# 4) BLOQUEO por nivel → NO llega a payload (0 WebhookDelivery)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bloqueo_l1_estrategia_no_despacha(db: AsyncSession):
    await _seed(db, sid="blk_l1", asset="MES", tv="MES", mapped="MESU2025",
                tick="0.25", status="quarantined")
    raw = await _raw(db, "blk_l1", _body("MES"))
    dec = await process_signal(db, "blk_l1", raw.id, _body("MES"), _MD_ES)
    assert dec.outcome == "BLOCK" and dec.block_level == 1
    assert await _deliveries(db, "blk_l1") == []


@pytest.mark.asyncio
async def test_bloqueo_l3_symbol_busy_no_despacha(db: AsyncSession):
    await _seed(db, sid="blk_l3", asset="MES", tv="MES", mapped="MESU2025",
                tick="0.25")
    db.add(PositionState(strategy_id="blk_l3", account_id="paper_default",
                         symbol="MESU2025", state="LONG", direction="long",
                         quantity=1))
    await db.flush()
    raw = await _raw(db, "blk_l3", _body("MES"))
    dec = await process_signal(db, "blk_l3", raw.id, _body("MES"), _MD_ES)
    assert dec.outcome == "BLOCK" and dec.block_level == 3
    assert dec.block_reason == "symbol_busy"
    assert await _deliveries(db, "blk_l3") == []


@pytest.mark.asyncio
async def test_bloqueo_l5_sin_precio_no_despacha(db: AsyncSession):
    await _seed(db, sid="blk_l5", asset="MES", tv="MES", mapped="MESU2025",
                tick="0.25")
    body = _body("MES", price="N/A")                    # precio inválido → None
    raw = await _raw(db, "blk_l5", body)
    dec = await process_signal(db, "blk_l5", raw.id, body, _MD_ES)
    assert dec.outcome == "BLOCK" and dec.block_level == 5
    assert dec.block_reason == "entry_price_missing"
    assert await _deliveries(db, "blk_l5") == []


@pytest.mark.asyncio
async def test_bloqueo_l1_mercado_inactivo_no_despacha(db: AsyncSession):
    """L1.6 — bridge inactivo bloquea ENTRADAS (exits exentos)."""
    await _seed(db, sid="blk_l16", asset="MES", tv="MES", mapped="MESU2025",
                tick="0.25")
    raw = await _raw(db, "blk_l16", _body("MES"))
    dec = await process_signal(db, "blk_l16", raw.id, _body("MES"),
                               MarketDataService(_MD(active=False)))
    assert dec.outcome == "BLOCK" and dec.block_reason == "market_data_not_active"
    assert await _deliveries(db, "blk_l16") == []


# ---------------------------------------------------------------------------
# 5) RECEPCIÓN — token inválido → 401 y NO procesa (D-1 pin)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recepcion_token_invalido_401_y_no_procesa(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    llamado = {"n": 0}
    async def _spy(*a, **kw):
        llamado["n"] += 1
    monkeypatch.setattr(
        "app.api.webhooks_luxalgo._background_process_signal", _spy)
    r = await client.post("/webhooks/luxalgo/tok_bad?token=nope",
                          json=_body("MES"))
    assert r.status_code == 401
    assert llamado["n"] == 0                             # NO se encoló procesamiento


# ---------------------------------------------------------------------------
# 6) KILL-SWITCH por capas — real solo con las 4 abiertas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env_tp,env_dry,cfg_tp,cfg_dry,espera_dry", [
    (True, False, True, False, False),      # las 4 abiertas → envío REAL
    (False, False, True, False, True),      # capa 1 (env kill-switch) cierra
    (True, True, True, False, True),        # capa 2 (env DRY_RUN) cierra
    (True, False, False, False, True),      # capa 3 (traderspost_enabled) cierra
    (True, False, True, True, True),        # capa 4 (dry_run) cierra
])
def test_killswitch_por_capa(env_tp, env_dry, cfg_tp, cfg_dry, espera_dry):
    from types import SimpleNamespace
    s = SimpleNamespace(TRADERSPOST_ENABLED=env_tp, DRY_RUN=env_dry)
    cfg = {"traderspost_enabled": cfg_tp, "dry_run": cfg_dry}
    assert resolve_effective_dry_run(s, cfg) is espera_dry
