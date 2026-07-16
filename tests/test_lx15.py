"""LX-15 (backend/cable) — C1 móvil SIN espejismo.

Cubre el cable de despacho (C1>0 → LÍMITE a P0∓depth, al tick; fail-honest sin
ATR), la caída HONESTA de participación con C1>0 (leg_filled), el tripwire
c1_market que NO dispara falso con C1>0, el gate ÁMBAR forzado con C1>0, y la
SEGURIDAD DEL PERIODO INTERMEDIO: ningún camino escribe c1_depth_atr>0 en config.
"""
import uuid
from datetime import datetime, timezone

import pytest

import scripts.mr_luxy as mrl
from app.models.normalized_signal import NormalizedSignal
from app.services.filter_pipeline import PipelineResult
from app.services.payload_builder import PayloadBuilder
from scripts.mr_sims import SimTrade, leg_filled


def _sig(action="buy", sentiment="long", role="entry_long", price=5000.0, qty=5):
    s = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="es", ticker_received="ES",
        mapped_symbol="ESU2025", action=action, sentiment=sentiment,
        signal_role=role, price=price, quantity=qty,
        signal_ts=datetime.now(timezone.utc), dedupe_key=uuid.uuid4().hex)
    s.id = uuid.uuid4()
    return s


def _pr(sl=4990.0, tp=5010.0, atr=8.0):
    return PipelineResult(outcome="APPROVE", score=100, sl_price=sl, tp_price=tp,
                          atr_value=atr, market_data_provider="M")


def _se(c1_depth, levels=(1.64, 3.28), qty=(5, 3, 2)):
    return {"scale_entry": {"mode": "execute", "quantities": list(qty),
                            "levels": list(levels), "c1_depth_atr": c1_depth,
                            "max_micro_contracts": sum(qty)},
            "tick_size": 0.25}


# ---------------------------------------------------------------------------
# 1) EL CABLE — C1 móvil se despacha como LÍMITE a P0∓depth (punto 7)
# ---------------------------------------------------------------------------

def test_c1_movil_long_es_limite_a_p0_menos_depth():
    legs = PayloadBuilder().build_scaled(_sig(), None, _se(1.0), _pr())
    c1 = legs[0]
    assert c1["orderType"] == "limit"
    assert c1["limitPrice"] == 4992.0            # 5000 − 1.0×8 = 4992, al tick 0.25
    # stop común y TP se anclan igual en TODAS las piernas
    assert all(l["stopLoss"] == {"type": "stop", "stopPrice": 4990.0} for l in legs)
    assert all(l["takeProfit"] == {"type": "limit", "limitPrice": 5010.0} for l in legs)


def test_c1_movil_short_es_limite_a_p0_mas_depth():
    legs = PayloadBuilder().build_scaled(
        _sig(action="sell", sentiment="short", role="entry_short"),
        None, _se(1.0), _pr())
    assert legs[0]["orderType"] == "limit"
    assert legs[0]["limitPrice"] == 5008.0       # 5000 + 1.0×8


def test_c1_depth_cero_sigue_a_mercado():
    legs = PayloadBuilder().build_scaled(_sig(), None, _se(0.0), _pr())
    assert "orderType" not in legs[0]            # C1 a mercado (comportamiento actual)


def test_c1_movil_al_tick_fx_6j():
    # 6J tick 5e-7: el límite de C1 se cuantiza al tick (reusa round_to_tick FIX-D2)
    s = _sig(price=0.0068)
    cfg = _se(1.0); cfg["tick_size"] = 0.0000005
    legs = PayloadBuilder().build_scaled(
        s, None, cfg, _pr(sl=0.0067, tp=0.0069, atr=0.00002))
    # 0.0068 − 1.0×0.00002 = 0.00678 → múltiplo de 5e-7
    assert legs[0]["limitPrice"] == 0.00678


# ---------------------------------------------------------------------------
# 2) FAIL-HONEST TOTAL — C1>0 sin ATR/precio JAMÁS despacha a mercado (punto 7)
# ---------------------------------------------------------------------------

def test_c1_movil_sin_atr_bloquea_fail_honest():
    with pytest.raises(ValueError, match="C1 móvil"):
        PayloadBuilder().build_scaled(_sig(), None, _se(1.0),
                                      _pr(atr=None))


def test_c1_movil_sin_precio_bloquea_fail_honest():
    s = _sig()
    s.price = None
    with pytest.raises(ValueError, match="C1 móvil"):
        PayloadBuilder().build_scaled(s, None, _se(1.0), _pr())


# ---------------------------------------------------------------------------
# 3) Participación cae HONESTAMENTE con C1>0 (leg_filled: C1 deja de ser mercado)
# ---------------------------------------------------------------------------

def _trade(mae_atr, atr=10.0):
    return SimTrade(number=1, side="long", in_sample=True, entry_price=5000.0,
                    atr_pts=atr, mae_pts=mae_atr * atr, mfe_pts=atr,
                    native_pnl_usd=10.0)


def test_participacion_cae_honestamente_con_c1_movil():
    # MAE de 0.2 a 2.0 ×ATR; C1 a mercado (0) → todas participan; C1 a 1.0×ATR →
    # solo las que llegan a 1.0 de MAE. La caída es real, no espejismo.
    sts = [_trade(m / 10.0) for m in range(2, 21)]        # 0.2 .. 2.0 ×ATR
    part_mercado = sum(1 for s in sts if leg_filled(s, 0.0, None)[0])
    part_c1_1atr = sum(1 for s in sts if leg_filled(s, 1.0, None)[0])
    assert part_mercado == len(sts)                       # C1 mercado: 100%
    assert part_c1_1atr < part_mercado                    # C1>0: cae honestamente
    assert part_c1_1atr == sum(1 for s in sts if s.mae_pts / s.atr_pts >= 1.0)


def test_overrides_to_levers_mueve_c1():
    base = {"ladder": {"alloc": [5, 3, 2], "levels": [0.0, 1.5, 3.0]}}
    lev = mrl._overrides_to_levers(base, {"l1_usd": 400.0}, atr_med=8.0, ppt=50.0)
    levels = lev["ladder"]["levels"]
    assert levels[0] == pytest.approx(400.0 / 50.0 / 8.0)   # C1 profundidad>0
    # las legs reflejan C1 a esa profundidad (ya no depth 0)
    depths = [d for d, _w in lev["ladder"]["legs"]]
    assert depths[0] == pytest.approx(levels[0]) and depths[0] > 0


# ---------------------------------------------------------------------------
# 4) Tripwire c1_market NO dispara falso con C1>0 (punto 5)
# ---------------------------------------------------------------------------

def test_tripwire_c1_market_no_falso_con_c1_movil():
    # participación baja (70%) con C1 MÓVIL (todas las piernas depth>0) → NO implausible
    legs_movil = ((1.0, 0.5), (1.64, 0.3), (3.28, 0.2))
    impl, _msg, _av = mrl.tripwire_implausible(legs_movil, "both", 70.0, 1.5, 5)
    assert impl is False
    # contraste: con C1 a MERCADO (depth 0) esa misma participación baja SÍ dispara
    legs_mercado = ((0.0, 0.5), (1.64, 0.3), (3.28, 0.2))
    impl2, _m2, _a2 = mrl.tripwire_implausible(legs_mercado, "both", 70.0, 1.5, 5)
    assert impl2 is True


# ---------------------------------------------------------------------------
# 5) Gate ÁMBAR forzado con C1>0 (punto 6)
# ---------------------------------------------------------------------------

def _study_verde():
    return {"dashboard": {"robustez": {"verdict": "verde"},
                          "table3": {"crudo_plus": {"participacion_pct": 100.0}},
                          "notes": []},
            "contencion": {"confiable": True, "pct": 100.0}}


def test_gate_ambar_forzado_con_c1_movil():
    st = _study_verde()
    assert mrl.gate_aplicar(st)["nivel"] == "verde"                 # sin C1: verde
    g = mrl.gate_aplicar(st, {"c1_depth_atr": 1.0})
    assert g["nivel"] == "amber"                                    # C1>0: mínimo ámbar
    assert any("C1 móvil" in t for t in g["triggers"])
    # C1 depth 0 explícito → no fuerza ámbar
    assert mrl.gate_aplicar(st, {"c1_depth_atr": 0.0})["nivel"] == "verde"


# ---------------------------------------------------------------------------
# 6) SEGURIDAD DEL PERIODO INTERMEDIO — ningún camino gana C1>0 en config viva
# ---------------------------------------------------------------------------

def test_activacion_from_study_nunca_emite_c1_depth():
    study = {"levers_in_sample": {"ladder": {"alloc": [5, 3, 2],
                                             "levels": [0.0, 1.64, 3.28]}},
             "cancel_after_s": 3600}
    out = mrl.activacion_from_study(study)
    assert "scale_entry" in out
    assert "c1_depth_atr" not in out["scale_entry"]     # el estudio nunca deriva C1>0


def test_build_scaled_sin_c1_depth_es_mercado():
    # una scale_entry SIN la llave c1_depth_atr (como la de activacion) → C1 mercado
    cfg = {"scale_entry": {"mode": "execute", "quantities": [5, 3, 2],
                           "levels": [1.64, 3.28], "max_micro_contracts": 10},
           "tick_size": 0.25}
    legs = PayloadBuilder().build_scaled(_sig(), None, cfg, _pr())
    assert "orderType" not in legs[0]                   # cable inerte sin la llave
