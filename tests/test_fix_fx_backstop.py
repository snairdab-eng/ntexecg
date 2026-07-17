"""FIX-FX-BACKSTOP — la conversión USD→puntos/×ATR ya NO colapsa el backstop FX
a 0 por un round(_,2) pensado para índices. Rejilla del tick del catálogo
(FIX-D2), fail-honest bajo 1 tick, y display FX en ticks.

Evidencia raíz (6J apply 2026-07-17): slider SL −$570 → backstop_points=0.0
porque 570 / ppt 12.5M ≈ 4.56e-5 pts se aplastaba con round(_,2). Con el tick
6J (5e-7) esos 4.56e-5 son ~91 ticks: representables, jamás 0.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.normalized_signal import NormalizedSignal
from app.services.sl_tp_calculator import SLTPCalculator
from scripts.fx_levers import (fmt_pts, snap_puntos, usd_a_mult_atr,
                               usd_a_puntos)
from scripts.mr_luxy import config_from_overrides


def _signal(entry: float) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="fx_test",
        ticker_received="X", mapped_symbol="Xu2025", action="buy",
        sentiment="long", price=entry, signal_ts=datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex)

# Catálogo real (instrument_catalog.json / seed_dev_data.py / mr_report.TICK_SIZE)
PPT = {"ES": 50.0, "GC": 100.0, "6E": 125000.0, "6J": 12500000.0}
TICK = {"ES": 0.25, "GC": 0.10, "6E": 0.00005, "6J": 0.0000005}
# ATR mediano (puntos de precio) representativo por instrumento — solo afecta ×ATR
ATR = {"ES": 8.0, "GC": 4.0, "6E": 0.0008, "6J": 0.00003}


def _on_grid(pts, tick):
    from decimal import Decimal
    return (Decimal(str(pts)) / Decimal(str(tick))) % 1 == 0


# ---------------------------------------------------------------------------
# 1) Núcleo fx_levers — snap al tick + representabilidad
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("activo", ["ES", "GC", "6E", "6J"])
def test_usd_a_puntos_no_colapsa_y_queda_en_rejilla(activo):
    # un backstop "de operador" razonable: $600/mini
    pts, repr_ok, raw = usd_a_puntos(600.0, PPT[activo], TICK[activo])
    assert repr_ok is True
    assert pts is not None and pts > 0            # jamás 0 colapsado
    assert _on_grid(pts, TICK[activo])            # en la rejilla del tick
    assert abs(raw - 600.0 / PPT[activo]) < 1e-15  # crudo = usd/ppt


def test_regresion_hallazgo_570_por_12_5M():
    """El número EXACTO del hallazgo: 570 USD / ppt 12.5M (6J)."""
    pts, repr_ok, raw = usd_a_puntos(570.0, 12_500_000.0, TICK["6J"])
    assert raw == pytest.approx(4.56e-5, rel=1e-3)   # el crudo real
    assert repr_ok is True                            # 4.56e-5 ≈ 91 ticks ≥ 1 tick
    assert pts > 0 and pts != 0.0                     # ANTES daba 0.0 — el espejismo
    assert round(pts / TICK["6J"]) == 91              # ~91 ticks, en rejilla


def test_fail_honest_backstop_bajo_un_tick():
    # $3 en 6J: 3/12.5M = 2.4e-7 < tick 5e-7 → NO representable
    pts, repr_ok, raw = usd_a_puntos(3.0, 12_500_000.0, TICK["6J"])
    assert repr_ok is False
    assert raw < TICK["6J"]                            # bajo la resolución


def test_snap_puntos_sin_tick_fail_open():
    # instrumento fuera del catálogo (tick None) → no snap, no juicio (crudo)
    pts, repr_ok, raw = snap_puntos(0.0000456, None)
    assert repr_ok is True and pts == raw == 0.0000456


def test_usd_a_mult_atr_adimensional_no_colapsa():
    # ×ATR = usd/ppt/atr — misma prueba de representabilidad (mult·ATR = usd/ppt)
    for activo in ("ES", "GC", "6E", "6J"):
        mult, repr_ok = usd_a_mult_atr(2000.0, PPT[activo], ATR[activo],
                                       TICK[activo])
        assert repr_ok is True and mult > 0


# ---------------------------------------------------------------------------
# 2) config_from_overrides — camino del operador, por instrumento
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("activo", ["ES", "GC", "6E", "6J"])
def test_config_backstop_representable_por_instrumento(activo):
    o = {"sl_usd": 600.0}
    cfg = config_from_overrides(o, ATR[activo], PPT[activo], [10, 0, 0],
                                3600, tick=TICK[activo], activo=activo)
    bp = cfg["backstop_points"]
    assert bp > 0 and _on_grid(bp, TICK[activo])      # nunca 0, en rejilla
    assert "_no_representable" not in cfg


def test_config_6J_hallazgo_no_escribe_cero():
    """Reproduce el apply real: 6J, sl_usd 570 → backstop_points NO es 0."""
    cfg = config_from_overrides({"sl_usd": 570.0}, ATR["6J"], PPT["6J"],
                                [10, 0, 0], 3600, tick=TICK["6J"], activo="6J")
    assert cfg["backstop_points"] > 0
    assert cfg["backstop_points"] == pytest.approx(570.0 / PPT["6J"],
                                                   rel=1e-2)


def test_config_colapso_bajo_tick_rechaza_con_aviso():
    """sl_usd diminuto en 6J → OMITE la llave (jamás 0) + aviso no-representable."""
    cfg = config_from_overrides({"sl_usd": 3.0}, ATR["6J"], PPT["6J"],
                                [10, 0, 0], 3600, tick=TICK["6J"], activo="6J")
    assert "backstop_points" not in cfg              # NO se escribe 0 en silencio
    nr = cfg["_no_representable"]
    assert any(x["campo"] == "backstop_points" for x in nr)


def test_config_es_backstop_indice_intacto():
    """Retrocompat: ES 4500/50 = 90 pts sigue exacto (la ruta LX-15 clásica)."""
    cfg = config_from_overrides({"sl_usd": 4500.0}, ATR["ES"], PPT["ES"],
                                [10, 0, 0], 3600, tick=TICK["ES"], activo="ES")
    assert cfg["backstop_points"] == 90.0


# ---------------------------------------------------------------------------
# 3) USD→pts→config→payload: el precio SL final cae en la rejilla y el bracket
#    P0 lo valida (lo que ANTES rompía: backstop 0 → SL = entrada → inválido)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("activo,entry", [("ES", 5000.0), ("GC", 1913.4),
                                          ("6E", 1.08325), ("6J", 0.0068)])
@pytest.mark.asyncio
async def test_payload_sl_en_rejilla_bracket_valido(activo, entry):
    cfg = config_from_overrides({"sl_usd": 600.0}, ATR[activo], PPT[activo],
                                [10, 0, 0], 3600, tick=TICK[activo], activo=activo)
    config = {"backstop_points": cfg["backstop_points"], "tick_size": TICK[activo]}
    res = await SLTPCalculator().calculate(_signal(entry), atr=None,
                                           entry_price=entry, config=config)
    assert res["passed"] is True                      # bracket VÁLIDO
    assert res["sl_mode"] == "backstop_fixed"
    assert 0 < res["sl_price"] < entry                # long: SL bajo la entrada
    assert _on_grid(res["sl_price"], TICK[activo])    # en la rejilla del tick


# ---------------------------------------------------------------------------
# 4) Display FX — ticks, nunca '0 pts' ni notación científica cruda
# ---------------------------------------------------------------------------

def test_fmt_pts_fx_en_ticks():
    assert "ticks" in fmt_pts("6J", 0.0000455)         # FX → ticks
    assert "ticks" in fmt_pts("6E", 0.0006)
    assert fmt_pts("ES", 90.0) == "90 pts"             # índice → pts
    assert fmt_pts("6J", None) == "—"
