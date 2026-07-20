"""Tests del motor de ejecución escalonada (PayloadBuilder.build_scaled)."""
import uuid
from types import SimpleNamespace

from app.services.payload_builder import PayloadBuilder


def _sig(action="buy", price=5000.0, role="entry_long"):
    return SimpleNamespace(
        mapped_symbol="MGCQ2026", action=action, price=price, quantity=1,
        sentiment="bullish" if action == "buy" else "bearish",
        signal_role=role, strategy_id="MicroGC5mContrarianNormal",
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
    )


def _pr(sl=4985.0, tp=5036.0, atr=6.0):
    return SimpleNamespace(sl_price=sl, tp_price=tp, score=78, atr_value=atr,
                           market_data_provider="NinjaTraderBridge")


def _cfg(mode, levels, quantities, maxm=5, sl_mult=2.5):
    return {"sl_atr_multiplier": sl_mult,
            "scale_entry": {"mode": mode, "levels": levels,
                            "quantities": quantities, "max_micro_contracts": maxm}}


def test_design_only_returns_single_entry():
    out = PayloadBuilder().build_scaled(_sig(), None,
                                        _cfg("design_only", [0.75, 1.25], [0, 1, 4]), _pr())
    assert len(out) == 1
    assert "orderType" not in out[0]            # entrada única a mercado
    assert out[0]["stopLoss"]["stopPrice"] == 4985.0


def test_execute_long_multi_leg_limit_prices_and_common_stop():
    out = PayloadBuilder().build_scaled(_sig(), None,
                                        _cfg("execute", [0.75, 1.25], [0, 1, 4]), _pr())
    assert len(out) == 2                          # C1 qty0 se omite; C2,C3
    c2, c3 = out
    assert c2["quantity"] == 1 and c3["quantity"] == 4
    # adds long = precio - nivel*ATR (5000 - 0.75*6, 5000 - 1.25*6)
    assert c2["orderType"] == "limit" and c2["limitPrice"] == 4995.5
    assert c3["orderType"] == "limit" and c3["limitPrice"] == 4992.5
    # stop común en todas
    assert c2["stopLoss"]["stopPrice"] == 4985.0 == c3["stopLoss"]["stopPrice"]
    assert c2["takeProfit"]["limitPrice"] == 5036.0


def test_execute_includes_market_base_leg():
    out = PayloadBuilder().build_scaled(_sig(), None,
                                        _cfg("execute", [0.5, 1.0], [1, 0, 2]), _pr())
    assert len(out) == 2                          # C1 (market) + C3
    c1, c3 = out
    assert "orderType" not in c1                  # C1 a mercado
    assert c1["quantity"] == 1 and c1["signalPrice"] == 5000.0
    assert c3["orderType"] == "limit" and c3["limitPrice"] == 4994.0  # 5000-1.0*6


def test_execute_short_adds_above():
    out = PayloadBuilder().build_scaled(_sig(action="sell", role="entry_short"), None,
                                        _cfg("execute", [0.75, 1.25], [0, 1, 4],
                                             sl_mult=2.5), _pr(sl=5015.0, tp=4964.0))
    c2, c3 = out
    assert c2["limitPrice"] == 5004.5 and c3["limitPrice"] == 5007.5  # precio + nivel*ATR
    # P0-2 ESCALERA: las piernas que SUMAN van como "add" sin sentiment
    # (un sell con posición abierta se ignoraría en silencio).
    assert c2["action"] == "add" and "sentiment" not in c2


def test_total_exceeds_max_falls_back_to_single():
    out = PayloadBuilder().build_scaled(_sig(), None,
                                        _cfg("execute", [0.75, 1.25], [0, 2, 5], maxm=4), _pr())
    assert len(out) == 1                          # 7 > max 4 → entrada única segura


def test_exit_never_scales():
    out = PayloadBuilder().build_scaled(_sig(action="exit", role="exit_long"), None,
                                        _cfg("execute", [0.75, 1.25], [0, 1, 4]),
                                        _pr(sl=None, tp=None, atr=None))
    assert len(out) == 1
    assert "stopLoss" not in out[0] and "orderType" not in out[0]


def test_leg_extras_index_and_level():
    out = PayloadBuilder().build_scaled(_sig(), None,
                                        _cfg("execute", [0.75, 1.25], [0, 1, 4]), _pr())
    assert out[0]["extras"]["leg_index"] == 1 and out[1]["extras"]["leg_index"] == 2
    assert out[1]["extras"]["level_atr"] == 1.25
