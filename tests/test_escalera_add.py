"""P0-2 ESCALERA-ADD — las piernas que SUMAN van como action:"add" (2026-07-20).

Semántica VERIFICADA EMPÍRICAMENTE por el operador (CONTRATO/TRADERSPOST_
Semantica_Verificada_2026-07-20.md, sondas directas con posición abierta):
  · un buy/sell con posición abierta se IGNORA en silencio (success:true sin
    orden) — causa raíz de que C2/C3 JAMÁS llegaran al broker;
  · action:"add" crea la orden de trabajo, llena, promedia la posición y el
    bracket se ajusta al total;
  · "add" RECHAZA sentiment (invalid-sentiment-action) pero acepta
    orderType/limitPrice/cancelAfter/stopLoss/takeProfit.

Pineado aquí: C1 conserva buy/sell + sentiment (abre); toda pierna i>0 y toda
pierna re-armada va action:"add" SIN sentiment, con todo lo demás intacto
(limitPrice al tick FIX-D2, cancelAfter RA-2a, bracket, extras). CORTOS: misma
regla (add no lleva lado — el broker suma a la posición abierta); verificación
EN VIVO de short pendiente del operador (las sondas fueron sobre un long).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.normalized_signal import NormalizedSignal
from app.services.payload_builder import PayloadBuilder
from app.services.tp_format import dumps as tp_dumps

_SIG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _sig(action="buy", sentiment="long", role="entry_long",
         price=7510.0, qty=5) -> NormalizedSignal:
    s = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="esc", ticker_received="MES",
        mapped_symbol="MESU2026", action=action, sentiment=sentiment,
        signal_role=role, price=price, quantity=qty,
        signal_ts=datetime.now(timezone.utc), dedupe_key=uuid.uuid4().hex)
    s.id = _SIG_ID
    return s


def _pr(sl, tp, atr):
    return SimpleNamespace(sl_price=sl, tp_price=tp, atr_value=atr,
                           score=None, quality=None, filters_active=None,
                           market_data_provider=None)


def _cfg(quantities, levels, tick=0.25):
    return {"tick_size": tick, "entry_reserve_timeout_seconds": 3600,
            "scale_entry": {"mode": "execute", "quantities": quantities,
                            "levels": levels, "max_micro_contracts": 10}}


# ═══════════════════════════════════════════════════════════════════════════
# 1) LONG — regresión con los números del INCIDENTE ([5,3,2], las piernas
#    que en producción jamás llegaron al broker)
# ═══════════════════════════════════════════════════════════════════════════

def test_long_incidente_532_c1_abre_y_los_adds_suman():
    legs = PayloadBuilder().build_scaled(
        _sig(price=5500.0, qty=10), None, _cfg([5, 3, 2], [1.0, 2.0]),
        _pr(sl=5470.0, tp=5520.0, atr=8.0))
    assert len(legs) == 3
    c1, c2, c3 = legs

    # C1 ABRE: buy a mercado con sentiment — intacta.
    assert c1["action"] == "buy" and c1["sentiment"] == "long"
    assert "orderType" not in c1 and c1["quantity"] == 5

    # C2/C3 SUMAN: action:"add", SIN sentiment; todo lo demás intacto.
    for leg, lp, q in ((c2, 5492.0, 3), (c3, 5484.0, 2)):
        assert leg["action"] == "add"
        assert "sentiment" not in leg              # add lo RECHAZA (sonda B1)
        assert leg["orderType"] == "limit"
        assert leg["limitPrice"] == lp             # al tick (FIX-D2)
        assert leg["cancelAfter"] == 3600          # RA-2a
        assert leg["quantity"] == q
        assert leg["stopLoss"] == {"type": "stop", "stopPrice": 5470.0}
        assert leg["takeProfit"] == {"type": "limit", "limitPrice": 5520.0}
        assert leg["extras"]["leg_quantity"] == q  # extras intactos
    assert c2["extras"]["leg_index"] == 2 and c3["extras"]["leg_index"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# 2) SHORT — misma regla (verificación EN VIVO pendiente, declarada)
# ═══════════════════════════════════════════════════════════════════════════

def test_short_misma_regla_adds_por_arriba_sin_sentiment():
    legs = PayloadBuilder().build_scaled(
        _sig(action="sell", sentiment="short", role="entry_short",
             price=5000.0, qty=3), None, _cfg([2, 1], [1.0]),
        _pr(sl=5015.0, tp=4964.0, atr=6.0))
    c1, c2 = legs
    assert c1["action"] == "sell" and c1["sentiment"] == "short"  # C1 abre
    assert "orderType" not in c1
    assert c2["action"] == "add" and "sentiment" not in c2
    assert c2["orderType"] == "limit"
    assert c2["limitPrice"] == 5006.0              # pullback por ARRIBA
    assert c2["cancelAfter"] == 3600
    assert c2["stopLoss"]["stopPrice"] == 5015.0


# ═══════════════════════════════════════════════════════════════════════════
# 3) RE-ARMADO — una pierna re-armada es un add
# ═══════════════════════════════════════════════════════════════════════════

def test_rearm_leg_es_add_sin_sentiment_ambos_lados():
    for side in ("long", "short"):
        leg = PayloadBuilder().build_rearm_leg(
            symbol="MESU2026", side=side,
            leg_state={"leg_index": 2, "side": side, "level_atr": 1.0,
                       "limit_price": 5492.1, "qty": 3, "cycle_n": 1,
                       "last_client_id": None, "last_sent_at": "x",
                       "state": "working", "death_reason": None},
            config=_cfg([4, 3, 3], [1.0, 2.0]),
            sl_price=5488.0, tp_price=5620.0, strategy_id="esc",
            signal_id="sig", client_id="sig-r2", cycle_n=2)
        assert leg["action"] == "add", side
        assert "sentiment" not in leg, side
        assert leg["orderType"] == "limit"
        assert leg["limitPrice"] == 5492.0         # re-snap al tick
        assert leg["cancelAfter"] == 3600
        assert leg["extras"]["rearm_cycle"] == 2
        assert leg["extras"]["client_id"] == "sig-r2"


# ═══════════════════════════════════════════════════════════════════════════
# 4) STRING EXACTO del wire — los números de las SONDAS verificadas (B2/C:
#    add límite 7500/7495 sobre LONG de 3, bracket 7419.50/7569.50)
# ═══════════════════════════════════════════════════════════════════════════

def test_string_exacto_del_add_como_las_sondas():
    legs = PayloadBuilder().build_scaled(
        _sig(price=7510.0, qty=5), None, _cfg([3, 1, 1], [2.0, 3.0]),
        _pr(sl=7419.5, tp=7569.5, atr=5.0))
    c1, c2, c3 = legs
    assert c1["action"] == "buy" and c1["quantity"] == 3
    assert c2["limitPrice"] == 7500.0 and c3["limitPrice"] == 7495.0  # sondas

    body = tp_dumps(c2)                            # los MISMOS bytes del wire
    assert body == (
        '{"ticker": "MESU2026", "action": "add", "quantity": 1, '
        '"signalPrice": 7510.0, "orderType": "limit", "limitPrice": 7500.0, '
        '"cancelAfter": 3600, '
        '"stopLoss": {"type": "stop", "stopPrice": 7419.5}, '
        '"takeProfit": {"type": "limit", "limitPrice": 7569.5}, '
        '"extras": {"strategy_id": "esc", '
        '"signal_id": "00000000-0000-0000-0000-000000000001", '
        '"leg_index": 2, "leg_quantity": 1, "level_atr": 2.0, '
        '"ntexecg_score": null, "ntexecg_quality": null, '
        '"filters_active": null, "atr_value": 5.0, "sl_multiplier": null, '
        '"provider": null}}'
    )
    assert '"sentiment"' not in body               # jamás en un add (sonda B1)
