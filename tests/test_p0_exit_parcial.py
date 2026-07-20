"""P0-EXIT-PARCIAL — los exits cierran la posición COMPLETA (2026-07-20).

Incidente real: entrada escalonada ES [5,3,2] llenó C1=5; el exit viajó con
"quantity": 1 (la de la ALERTA de LuxAlgo) y TradersPost cerró SOLO 1 micro —
para TradersPost un exit CON quantity es un cierre PARCIAL explícito; SIN
quantity aplana la posición COMPLETA real del broker (docs core-concepts/
webhooks + referencia de partial exit).

Fix pineado aquí: NINGÚN payload de exit lleva "quantity" (ni la de la
alerta ni el estimado — que es lo DESPACHADO, no lo llenado); la cantidad
que habría viajado queda en extras.omitted_quantity como traza forense.
Caminos: exit LuxAlgo (incidente) · forced_exit (EOD/max_holding/Flatten
convergen en dispatch_forced_exit) · reversal. El Flatten de UI queda
pineado además por _assert_close_only en test_exits_lote4.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services.market_data_service import MarketDataService
from app.services.traderspost_client import TradersPostClient, WebhookDeliveryResult

UTC = timezone.utc
_SE = {"mode": "execute", "quantities": [5, 3, 2], "levels": [1.0, 2.0],
       "max_micro_contracts": 10}


def _capture_send(monkeypatch):
    """Fake de TradersPostClient.send que captura payloads y devuelve SENT."""
    enviados: list[dict] = []

    async def _send(self, url, payload, **kw):
        enviados.append(payload)
        return WebhookDeliveryResult(status="SENT", payload_json=payload,
                                     url_masked="x", response_status_code=200,
                                     attempts=1)

    monkeypatch.setattr(TradersPostClient, "send", _send)
    return enviados


async def _persistir_cadena(db, *, sid, action, role, sentiment, qty,
                            price=5500.0):
    from app.services.filter_pipeline import PipelineResult
    raw = RawSignal(source="luxalgo", strategy_id=sid, ticker_received="MES",
                    action=action, sentiment=sentiment, quantity_raw=str(qty),
                    payload_json={}, token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, source="luxalgo", strategy_id=sid,
        ticker_received="MES", mapped_symbol="MESU2025", action=action,
        sentiment=sentiment, quantity=qty, price=price,
        signal_ts=datetime.now(UTC), signal_role=role,
        dedupe_key=uuid.uuid4().hex, status="processed")
    db.add(norm)
    await db.flush()
    decision = StrategyDecision(normalized_signal_id=norm.id, strategy_id=sid,
                                outcome="APPROVE")
    db.add(decision)
    await db.flush()
    is_entry = action in ("buy", "sell")
    result = PipelineResult(
        outcome="APPROVE", score=100,
        sl_price=5470.0 if is_entry else None,
        tp_price=5520.0 if is_entry else None,
        atr_value=8.0, market_data_provider="Mock")
    return norm, result, decision


async def _pos(db, symbol="MESU2025"):
    db.expire_all()
    res = await db.execute(
        select(PositionState).where(PositionState.symbol == symbol))
    return res.scalar_one_or_none()


# ═══════════════════════════════════════════════════════════════════════════
# 1) EL INCIDENTE — escalonada [5,3,2] y exit de LuxAlgo con la qty de la
#    alerta (1, y también 7: da igual — el exit JAMÁS lleva quantity)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize("alerta_qty", [1, 7])
async def test_incidente_escalonada_exit_aplana_completo(db: AsyncSession,
                                                         monkeypatch,
                                                         alerta_qty):
    from app.api.webhooks_luxalgo import _dispatch_approved
    enviados = _capture_send(monkeypatch)
    config = {"traderspost_webhook_url": "https://x/hook?token=t",
              "tick_size": 0.25, "scale_entry": dict(_SE)}

    # Entrada escalonada: 3 piernas, C1=5 a mercado, total despachado 10.
    norm, result, decision = await _persistir_cadena(
        db, sid="p0exit", action="buy", role="entry_long", sentiment="long",
        qty=10)
    await _dispatch_approved(db, norm, None, config, result, decision)
    assert len(enviados) == 3
    assert "orderType" not in enviados[0] and enviados[0]["quantity"] == 5
    assert sum(p["quantity"] for p in enviados) == 10
    pos = await _pos(db)
    assert pos.state == "LONG" and pos.quantity == 10     # estimado=despachado

    # Exit de LuxAlgo con la quantity de la ALERTA (≠ posición): el payload
    # NO lleva quantity (TradersPost aplana la posición COMPLETA del broker,
    # los fills reales que NTEXECG no observa) y conserva cancel:true.
    enviados.clear()
    norm_x, result_x, decision_x = await _persistir_cadena(
        db, sid="p0exit", action="exit", role="exit_long", sentiment="flat",
        qty=alerta_qty)
    await _dispatch_approved(db, norm_x, None, config, result_x, decision_x)
    (exit_p,) = enviados                                  # exits no escalan
    assert exit_p["action"] == "exit"
    assert "quantity" not in exit_p
    assert exit_p["cancel"] is True                       # FIX-D3 intacto
    assert "sentiment" not in exit_p and "stopLoss" not in exit_p
    assert exit_p["extras"]["omitted_quantity"] == alerta_qty   # traza
    pos = await _pos(db)
    assert pos.state == "FLAT"                            # estimador coherente
    # La delivery registrada refleja el payload realmente enviado (sin qty).
    db.expire_all()
    delivs = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.strategy_id == "p0exit"))).scalars().all()
    exits = [d for d in delivs if (d.payload_json or {}).get("action") == "exit"]
    assert len(exits) == 1 and "quantity" not in exits[0].payload_json


# ═══════════════════════════════════════════════════════════════════════════
# 2) forced_exit (EOD / max_holding / Flatten de UI convergen aquí):
#    el estimado (lo DESPACHADO, 10) tampoco viaja — solo a extras
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_forced_exit_sin_quantity_con_traza(db: AsyncSession,
                                                  monkeypatch):
    from app.services.forced_exit import dispatch_forced_exit
    enviados = _capture_send(monkeypatch)
    db.add(Strategy(strategy_id="p0f", name="p0f", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    pos = PositionState(
        strategy_id="p0f", account_id="paper_default", symbol="MESU2025",
        state="LONG", state_source="estimated", direction="long",
        quantity=10, risk_plan_json={"opened_at": datetime.now(UTC).isoformat()})
    db.add(pos)
    await db.flush()
    strat = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "p0f"))).scalar_one()
    config = {"traderspost_webhook_url": "https://tp/base", "dry_run": True,
              "traderspost_enabled": False, "timezone": "America/New_York"}

    await dispatch_forced_exit(db, pos, strat, config, "max_holding", settings)

    (p,) = enviados
    assert p["action"] == "exit" and "quantity" not in p
    assert p["cancel"] is True
    assert p["extras"]["omitted_quantity"] == 10          # el estimado, a extras


# ═══════════════════════════════════════════════════════════════════════════
# 3) reversal — el cierre previo al reverso tampoco lleva quantity
# ═══════════════════════════════════════════════════════════════════════════

class _MockMD:
    async def get_bars(self, *a, **kw): return []
    async def get_atr(self, *a, **kw): return 8.0
    async def is_active(self, symbol): return True


@pytest.mark.asyncio
async def test_reversal_cierre_previo_sin_quantity(db: AsyncSession,
                                                   monkeypatch):
    """LONG abierta + señal sell (reversal, allow_reversal ausente ⇒ cierra y
    bloquea la entrada opuesta): la ÚNICA delivery es el cierre y va SIN
    quantity — el reverso jamás monta sobre residuo parcial."""
    from app.api.webhooks_luxalgo import process_signal
    enviados = _capture_send(monkeypatch)
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2025", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True))
    db.add(Strategy(strategy_id="p0r", name="p0r", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="p0r", mode="paper",
                           traderspost_webhook_url="https://tp/base"))
    db.add(PositionState(strategy_id="p0r", account_id="paper_default",
                         symbol="MESU2025", state="LONG",
                         state_source="estimated", direction="long",
                         quantity=10))
    await db.commit()

    sell = {"ticker": "MES", "action": "sell", "sentiment": "short",
            "quantity": "1", "price": "5500.00", "interval": "5"}
    raw = RawSignal(source="luxalgo", strategy_id="p0r", payload_json=sell,
                    token_valid=True)
    db.add(raw)
    await db.flush()
    decision = await process_signal(db, "p0r", raw.id, dict(sell),
                                    MarketDataService(_MockMD()))

    assert decision.block_reason == "reversal_not_allowed"
    (p,) = enviados                                       # SOLO el cierre
    assert p["action"] == "exit" and "quantity" not in p
    assert p["cancel"] is True
    assert p["extras"]["omitted_quantity"] == 10          # el estimado de pos
