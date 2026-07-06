"""P0 — guarda fail-closed del bracket en L5 (Auditoría 2026-07-06, P0-1).

Bug demostrado en la auditoría: backstop mal escalado (los 90 pts de ES
pegados por error en 6E a 1.083) producía `passed=True` con
`sl_price = −88.917` — una orden desnuda disfrazada (la misma clase que
NX-05). La guarda valida el bracket YA COMPUTADO, en AMBOS modos (backstop
y ATR): precios positivos y del lado correcto de la señal, o BLOCK
`bracket_price_invalid`.

Invariante endurecido: passed=True ⇒ sl_price válido, positivo y del lado
correcto (y tp_price, si existe, también).

Adversarial: los tests de BLOCK fallan sin el fix (salían passed=True).
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.normalized_signal import NormalizedSignal
from app.services.sl_tp_calculator import SLTPCalculator


def _sig(action: str = "buy", sentiment: str = "long") -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="test_strat",
        ticker_received="M6E",
        mapped_symbol="M6EU2025",
        action=action,
        sentiment=sentiment,
        price=1.083,
        signal_ts=datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex,
    )


# ---------------------------------------------------------------------------
# El caso demostrado: 6E + backstop de ES → SL negativo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backstop_mal_escalado_long_bloquea():
    """6E long a 1.083 con backstop_points=90 (valor de ES pegado por
    error): antes passed=True con sl_price=−88.917; ahora BLOCK."""
    calc = SLTPCalculator()
    r = await calc.calculate(_sig("buy", "long"), atr=0.004,
                             entry_price=1.083,
                             config={"backstop_points": 90})
    assert r["passed"] is False
    assert r["reason"] == "bracket_price_invalid"
    assert r["sl_price"] is None            # jamás filtrar un precio inválido
    assert r["tp_price"] is None


@pytest.mark.asyncio
async def test_backstop_mal_escalado_short_tp_fallback_bloquea():
    """Short: el SL (señal+90) queda positivo y del lado correcto, pero el
    TP fallback sin ATR (señal−90 = −88.917) revienta → BLOCK igual."""
    calc = SLTPCalculator()
    r = await calc.calculate(_sig("sell", "short"), atr=None,
                             entry_price=1.083,
                             config={"backstop_points": 90,
                                     "tp_nominal_short": 8.0})
    assert r["passed"] is False
    assert r["reason"] == "bracket_price_invalid"


@pytest.mark.asyncio
async def test_atr_gigante_vs_precio_chico_bloquea():
    """El camino ATR también puede cruzar cero (ATR ≥ precio/multiplicador):
    entry 1.0, ATR 0.9, k=1.5 → SL −0.35 → BLOCK (antes pasaba)."""
    calc = SLTPCalculator()
    r = await calc.calculate(_sig("buy", "long"), atr=0.9, entry_price=1.0,
                             config={"sl_atr_multiplier": 1.5})
    assert r["passed"] is False
    assert r["reason"] == "bracket_price_invalid"


@pytest.mark.asyncio
async def test_tp_legacy_negativo_en_corto_bloquea():
    """TP legacy ×ATR en corto puede cruzar cero: entry 1.0, ATR 0.9,
    tp_mult 1.5 → TP −0.35 → BLOCK (el SL corto queda válido)."""
    calc = SLTPCalculator()
    r = await calc.calculate(_sig("sell", "short"), atr=0.9, entry_price=1.0,
                             config={"sl_atr_multiplier": 0.5,
                                     "tp_atr_multiplier": 1.5})
    assert r["passed"] is False
    assert r["reason"] == "bracket_price_invalid"


@pytest.mark.asyncio
async def test_multiplicador_cero_sl_en_la_entrada_bloquea():
    """Lado correcto estricto: sl_atr_multiplier=0 → SL == entrada (ni por
    debajo en long ni por encima en short) = bracket degenerado → BLOCK."""
    calc = SLTPCalculator()
    r = await calc.calculate(_sig("buy", "long"), atr=8.0, entry_price=5500.0,
                             config={"sl_atr_multiplier": 0})
    assert r["passed"] is False
    assert r["reason"] == "bracket_price_invalid"


# ---------------------------------------------------------------------------
# El invariante en positivo: los casos sanos siguen pasando (sin regresión)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bracket_sano_backstop_y_atr_pasan():
    calc = SLTPCalculator()
    # 6E con el backstop CORRECTO de FX (decimal, unidad de precio): 0.036
    fx = await calc.calculate(_sig("buy", "long"), atr=0.004,
                              entry_price=1.083,
                              config={"backstop_points": 0.036,
                                      "tp_nominal_long": 11.5})
    assert fx["passed"] is True
    assert fx["sl_price"] == pytest.approx(1.083 - 0.036)
    assert 0 < fx["sl_price"] < 1.083 < fx["tp_price"]
    # ES por ATR, ambos lados, con TP
    for action, senti in (("buy", "long"), ("sell", "short")):
        r = await calc.calculate(_sig(action, senti), atr=8.0,
                                 entry_price=5500.0,
                                 config={"sl_atr_multiplier": 1.5,
                                         "tp_atr_multiplier": 2.0})
        assert r["passed"] is True
        if action == "buy":
            assert 0 < r["sl_price"] < 5500.0 < r["tp_price"]
        else:
            assert r["sl_price"] > 5500.0 > r["tp_price"] > 0


@pytest.mark.asyncio
async def test_invariante_passed_implica_bracket_valido():
    """Barrido chico de configs raras: si passed=True, el bracket SIEMPRE es
    positivo y del lado correcto — el invariante endurecido."""
    calc = SLTPCalculator()
    configs = [
        {"backstop_points": 90},
        {"backstop_points": 0.036},
        {"backstop_points": 90, "tp_nominal_short": 8.0},
        {"sl_atr_multiplier": 1.5},
        {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": 2.0},
        {"sl_atr_multiplier": 200.0},
    ]
    for entry in (1.083, 100.0, 5500.0):
        for atr in (None, 0.004, 8.0, 90.0):
            for cfg in configs:
                for action, senti in (("buy", "long"), ("sell", "short")):
                    r = await calc.calculate(_sig(action, senti), atr,
                                             entry, dict(cfg))
                    if not r["passed"]:
                        continue
                    is_long = action == "buy"
                    assert r["sl_price"] is not None and r["sl_price"] > 0
                    assert (r["sl_price"] < entry if is_long
                            else r["sl_price"] > entry)
                    if r["tp_price"] is not None:
                        assert r["tp_price"] > 0
                        assert (r["tp_price"] > entry if is_long
                                else r["tp_price"] < entry)
