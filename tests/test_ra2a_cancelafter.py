"""RA-2a — cancelAfter en el payload de TODA pierna LÍMITE (C1 móvil + C2/C3).

TradersPost caduca la orden de trabajo al vencer el cancelAfter (1..3600s); ese
TTL sale del entry_reserve_timeout_seconds del config (MISMA fuente que la
reserva NX-28) y es el reloj del ciclo sin-solape del re-armado (RA-2b). El
mercado (C1 a mercado) llena al instante → jamás lleva cancelAfter.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.normalized_signal import NormalizedSignal
from app.services.filter_pipeline import PipelineResult
from app.services.payload_builder import (_CANCEL_AFTER_MAX_S,
                                          _cancel_after_seconds, PayloadBuilder)


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


def _cfg(c1_depth=0.0, ttl=3600, levels=(1.0, 2.0), qty=(5, 3, 2)):
    cfg = {"scale_entry": {"mode": "execute", "quantities": list(qty),
                           "levels": list(levels), "c1_depth_atr": c1_depth,
                           "max_micro_contracts": sum(qty)},
           "tick_size": 0.25}
    if ttl is not None:
        cfg["entry_reserve_timeout_seconds"] = ttl
    return cfg


# ---------------------------------------------------------------------------
# 1) Helper de TTL — acotado a [1, 3600], del entry_reserve_timeout_seconds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ttl,esperado", [
    (3600, 3600), (1800, 1800), (30, 30),
    (7200, 3600),                       # cap duro TradersPost
    (0, 1), (-5, 1),                    # piso 1s
    (None, 3600),                       # ausente → techo (despacho vigente)
])
def test_cancel_after_seconds_acotado(ttl, esperado):
    cfg = {} if ttl is None else {"entry_reserve_timeout_seconds": ttl}
    assert _cancel_after_seconds(cfg) == esperado


def test_cancel_after_invalido_cae_al_techo():
    assert _cancel_after_seconds({"entry_reserve_timeout_seconds": "x"}) == 3600
    assert _CANCEL_AFTER_MAX_S == 3600


# ---------------------------------------------------------------------------
# 2) C2/C3 (adds límite) SIEMPRE llevan cancelAfter; C1 a mercado NO
# ---------------------------------------------------------------------------

def test_c2_c3_llevan_cancel_after_y_c1_mercado_no():
    legs = PayloadBuilder().build_scaled(_sig(), None, _cfg(c1_depth=0.0), _pr())
    c1, c2, c3 = legs
    assert "orderType" not in c1 and "cancelAfter" not in c1   # C1 a mercado
    assert c2["orderType"] == "limit" and c2["cancelAfter"] == 3600
    assert c3["orderType"] == "limit" and c3["cancelAfter"] == 3600


def test_c1_movil_tambien_lleva_cancel_after():
    legs = PayloadBuilder().build_scaled(_sig(), None, _cfg(c1_depth=1.0), _pr())
    assert legs[0]["orderType"] == "limit"
    assert legs[0]["cancelAfter"] == 3600                      # C1 móvil = límite
    assert all(l["cancelAfter"] == 3600 for l in legs)         # las 3 son límite


def test_cancel_after_toma_el_ttl_del_config():
    legs = PayloadBuilder().build_scaled(_sig(), None,
                                         _cfg(c1_depth=1.0, ttl=1800), _pr())
    assert all(l["cancelAfter"] == 1800 for l in legs)


def test_cancel_after_capea_a_3600():
    legs = PayloadBuilder().build_scaled(_sig(), None,
                                         _cfg(c1_depth=1.0, ttl=99999), _pr())
    assert all(l["cancelAfter"] == 3600 for l in legs)


def test_ttl_ausente_usa_techo():
    legs = PayloadBuilder().build_scaled(_sig(), None,
                                         _cfg(c1_depth=1.0, ttl=None), _pr())
    assert all(l["cancelAfter"] == 3600 for l in legs)


# ---------------------------------------------------------------------------
# 3) Adversarial — el mercado y las salidas JAMÁS llevan cancelAfter
# ---------------------------------------------------------------------------

def test_entrada_unica_a_mercado_sin_cancel_after():
    # sin scale_entry ejecutable → build() (entrada única a mercado)
    p = PayloadBuilder().build(_sig(), None, {"entry_reserve_timeout_seconds": 3600},
                               _pr())
    assert "cancelAfter" not in p
    assert "orderType" not in p


def test_exit_sin_cancel_after():
    # una salida no escala y no es límite de trabajo → sin cancelAfter
    p = PayloadBuilder().build(
        _sig(action="exit", sentiment="flat", role="exit_long"),
        None, {"entry_reserve_timeout_seconds": 3600}, _pr())
    assert "cancelAfter" not in p
    assert p["cancel"] is True                                 # FIX-D3 intacto


def test_short_escalonado_lleva_cancel_after_en_limites():
    legs = PayloadBuilder().build_scaled(
        _sig(action="sell", sentiment="short", role="entry_short"),
        None, _cfg(c1_depth=1.0), _pr())
    assert all(l.get("orderType") == "limit" for l in legs)
    assert all(l["cancelAfter"] == 3600 for l in legs)
