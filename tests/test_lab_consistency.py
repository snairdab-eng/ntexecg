"""B4.0 — consistencia intrabar ↔ MFE/MAE sobre DATOS REALES (ES).

El test de aceptación que pidió el operador: %touch_intrabar(thr) debe cuadrar
con %(mfe_atr ≥ thr) y %(mae_atr ≥ thr) dentro de una tolerancia chica — la
caminata 5m y el MFE/MAE de LuxAlgo miden la MISMA excursión desde la MISMA
referencia (precio de entrada, misma ATR). Además guarda el invariante B3:
en resim_rows el ALCANCE lo deciden mfe/mae (los toques solo el orden), así
que %TP del panel ≡ %(mfe_atr ≥ tp) EXACTO en modo TP-only.

Integración con datos reales del repo (ListaDeOperaciones + NINJATRADER/HOLC);
se salta limpio donde no estén (CI/checkouts sin data).
"""
import glob
import math
from pathlib import Path

import pytest

_ES_CSV = sorted(glob.glob("ListaDeOperaciones/*_ES1!_*.csv"))
_ES_HOLC = Path("NINJATRADER/HOLC/ES_5m.csv")

pytestmark = pytest.mark.skipif(
    not (_ES_CSV and _ES_HOLC.exists()),
    reason="datos reales de ES no disponibles en este checkout",
)

# Tolerancia HONESTA por nivel, contada en TRADES (no en pp). Los feeds son
# dos: LuxAlgo mide su MFE/MAE con su propio intrabar; la caminata usa el HOLC
# 5m de NinjaTrader. En la FRONTERA de un umbral (mfe_atr apenas ≥ tp) un tick
# favorable ~0.1% que LuxAlgo registró puede no aparecer en el high de la barra
# 5m — granularidad ENTRE FUENTES, no hueco de datos.
#
# Diagnóstico 2026-07-11 (TP 3.0×, ES real, n=120): los 3 divergentes
# (#119 mfe 3.059×, #21 3.064×, #80 3.153×) caen todos en 3.0–3.15×, con CERO
# barras faltantes en su ventana y 0 inversos (sin sesgo). La caminata vio
# favor_max 2.84–2.98× — corta por debajo del umbral por <0.17×ATR. Es
# frontera/feed-skew legítimo, no R5.
#
# Cota: Δ ≤ max(2, ceil(2.5%·n)) trades por nivel — el 2.5% de la muestra o 2,
# lo que sea mayor. Una pp fija escala mal con n; contar trades es la cantidad
# real. Antes del fix B4.0 el sesgo era sistemático (3.5pp); hoy el residuo es
# marginal y sin dirección.
def _allowed_delta(n: int) -> int:
    return max(2, math.ceil(0.025 * n))


@pytest.fixture(scope="module")
def es_trades():
    from scripts.lab_analyze import (
        compute_touch_times,
        detect_tz_offset,
        enrich_with_bars,
        load_holc,
        parse_luxalgo_csv,
        split_in_out,
    )

    trades = parse_luxalgo_csv(Path(_ES_CSV[-1]))
    bars = load_holc("ES", "5m")
    off, _sanity, _detail = detect_tz_offset(trades, bars)
    enrich_with_bars(trades, bars, off)
    split_in_out(trades, 0.3)
    keys5 = sorted(bars)
    idx5 = {k: i for i, k in enumerate(keys5)}
    compute_touch_times(trades, keys5, idx5, bars)
    return [t for t in trades if t.atr_pct]


def test_touch_vs_mfe_consistency_es_real(es_trades):
    """Lado favorable: %con-toque(tp) ≈ %(mfe_atr ≥ tp) en toda la grilla."""
    from app.services.lab_metrics import TP_GRID

    n = len(es_trades)
    assert n >= 50                      # sanity: dataset real presente
    allowed = _allowed_delta(n)
    for tp in TP_GRID:
        touch = sum(
            1 for t in es_trades if t.t_tp_touch.get(str(tp)) is not None)
        mfe = sum(1 for t in es_trades if t.mfe_atr >= tp)
        assert abs(touch - mfe) <= allowed, (
            f"TP {tp}×: touch {touch} vs mfe_atr {mfe} de {n} "
            f"(Δ {abs(touch - mfe)} > {allowed} trades)")


def test_touch_vs_mae_consistency_es_real(es_trades):
    """Lado adverso: %con-toque(k) ≈ %(mae_atr ≥ k) en toda la grilla."""
    from app.services.lab_metrics import SL_GRID

    n = len(es_trades)
    allowed = _allowed_delta(n)
    for k in SL_GRID:
        touch = sum(
            1 for t in es_trades if t.t_sl_touch.get(str(k)) is not None)
        mae = sum(1 for t in es_trades if t.mae_atr >= k)
        assert abs(touch - mae) <= allowed, (
            f"SL {k}×: touch {touch} vs mae_atr {mae} de {n} "
            f"(Δ {abs(touch - mae)} > {allowed} trades)")


def test_resim_tp_pct_equals_mfe_reach_exact(es_trades):
    """Invariante B3 (el que hace IMPOSIBLE el 46.7% del hallazgo): en modo
    TP-only, %TP del panel ≡ %(mfe_atr ≥ tp) EXACTO — el alcance lo decide
    el MFE, nunca la caminata."""
    from app.services.lab_metrics import TP_GRID, resim_rows
    from scripts.lab_analyze import feature_rows

    rows = feature_rows(es_trades)
    for tp in TP_GRID:
        r = resim_rows(rows, tp=tp)
        for blk, sel in (("in", [x for x in es_trades if x.in_sample]),
                         ("out", [x for x in es_trades if not x.in_sample])):
            mfe = round(100 * sum(1 for t in sel if t.mfe_atr >= tp)
                        / len(sel), 1)
            assert r[blk]["tp_pct"] == mfe
