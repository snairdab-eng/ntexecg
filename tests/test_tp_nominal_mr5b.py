"""MR-5b — TP NOMINAL por lado en L5 (que cierre LuxAlgo).

Directiva 4 + recomendacion.json: el TP nominal va tan lejos (sobre el p99
del cierre de LuxAlgo, por lado) que casi nunca dispara — solo satisface el
bracket de TradersPost. Config opt-in por estrategia:
  pipeline_config_json: {"tp_nominal_long": 11.5, "tp_nominal_short": 14.5}
(multiplicadores ×ATR por lado; el corto es inestable → afinable en config).

Decisión de diseño (ATR): el TP nominal se queda en ×ATR — el estudio midió
el cierre de las ganadoras en ×ATR, y un TP de puntos fijos se ESTRECHA
(relativo a la volatilidad) justo en régimen volátil, donde las ganadoras
corren más: dispararía antes que LuxAlgo exactamente cuando no debe. La
lógica del backstop fijo es asimétrica y no aplica al lado favorable.
Fail-closed sin ATR: fallback al ANCHO DEL BACKSTOP espejado al lado
favorable (ES: 90 pts > TP nominal ~60 pts → sigue siendo nominal-ancho);
computable siempre que la entrada pase L5 sin ATR (solo pasa con backstop).

Adversariales (rojo antes del fix):
  (a) TP largo/corto por lado (clave correcta por dirección)
  (b) sin config nominal → tp_atr_multiplier/None como hoy (retrocompat)
  (c) backstop + TP nominal juntos: ambos anclados a la señal
  (d) ATR ausente + backstop → TP = ancho del backstop (nunca sin bracket);
      ATR ausente SIN backstop → BLOCK actual intacto
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.filter_pipeline import FilterPipeline
from app.services.sl_tp_calculator import SLTPCalculator


def _make_signal(action: str = "buy", sentiment: str = "long",
                 price: float | None = 5500.0) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="test_strat",
        ticker_received="MES",
        mapped_symbol="MESU2025",
        action=action,
        sentiment=sentiment,
        price=price,
        signal_ts=datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex,
    )


def _make_strategy(status: str = "live") -> Strategy:
    return Strategy(strategy_id="test_strat", name="Test", asset_symbol="MES",
                    status=status, enabled=True)


CFG_NOMINAL = {"tp_nominal_long": 11.5, "tp_nominal_short": 14.5,
               "sl_atr_multiplier": 1.5}


# ---------------------------------------------------------------------------
# (a) TP nominal por lado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tp_nominal_long_usa_su_lado():
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("buy", "long"), atr=8.0,
                             entry_price=5500.0, config=dict(CFG_NOMINAL))
    assert r["passed"] is True
    assert r["tp_price"] == 5500.0 + 11.5 * 8.0        # 5592, lado largo
    assert r["tp_mode"] == "nominal_atr"


@pytest.mark.asyncio
async def test_tp_nominal_short_usa_su_lado():
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("sell", "short"), atr=8.0,
                             entry_price=5500.0, config=dict(CFG_NOMINAL))
    assert r["passed"] is True
    assert r["tp_price"] == 5500.0 - 14.5 * 8.0        # 5384, lado corto
    assert r["tp_mode"] == "nominal_atr"


@pytest.mark.asyncio
async def test_tp_nominal_un_solo_lado_configurado():
    """Solo el largo configurado → el corto sigue el camino actual
    (tp_atr_multiplier o None). Los lados son independientes."""
    calc = SLTPCalculator()
    cfg = {"tp_nominal_long": 11.5, "sl_atr_multiplier": 1.5}
    largo = await calc.calculate(_make_signal("buy", "long"), 8.0, 5500.0,
                                 dict(cfg))
    assert largo["tp_price"] == 5500.0 + 92.0
    corto = await calc.calculate(_make_signal("sell", "short"), 8.0, 5500.0,
                                 dict(cfg))
    assert corto["tp_price"] is None                    # como hoy
    assert corto["tp_mode"] is None


@pytest.mark.asyncio
async def test_tp_nominal_manda_sobre_legacy():
    """Con nominal Y tp_atr_multiplier legacy, manda el nominal del lado."""
    calc = SLTPCalculator()
    cfg = dict(CFG_NOMINAL, tp_atr_multiplier=2.0)
    r = await calc.calculate(_make_signal("buy", "long"), 8.0, 5500.0, cfg)
    assert r["tp_price"] == 5500.0 + 92.0               # 11.5×, no 2.0×


# ---------------------------------------------------------------------------
# (b) Retrocompat: sin nominal, el TP actual intacto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sin_nominal_tp_legacy_como_hoy():
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("buy", "long"), 8.0, 5500.0,
                             {"sl_atr_multiplier": 1.5,
                              "tp_atr_multiplier": 2.0})
    assert r["tp_price"] == 5500.0 + 16.0
    r2 = await calc.calculate(_make_signal("buy", "long"), 8.0, 5500.0,
                              {"sl_atr_multiplier": 1.5})
    assert r2["tp_price"] is None


@pytest.mark.asyncio
async def test_tp_nominal_invalido_cae_al_camino_actual():
    calc = SLTPCalculator()
    for malo in (0, -11.5, "11.5", True):
        r = await calc.calculate(
            _make_signal("buy", "long"), 8.0, 5500.0,
            {"tp_nominal_long": malo, "sl_atr_multiplier": 1.5})
        assert r["tp_price"] is None, f"tp_nominal_long={malo!r}"


# ---------------------------------------------------------------------------
# (c) Interacción con el backstop: bracket completo anclado a la señal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backstop_mas_tp_nominal_bracket_completo():
    calc = SLTPCalculator()
    cfg = dict(CFG_NOMINAL, backstop_points=90)
    largo = await calc.calculate(_make_signal("buy", "long"), 8.0, 5500.0,
                                 cfg)
    assert largo["passed"] is True
    assert largo["sl_price"] == 5500.0 - 90             # stop fijo
    assert largo["tp_price"] == 5500.0 + 92.0           # TP nominal ×ATR
    assert largo["sl_mode"] == "backstop_fixed"
    assert largo["tp_mode"] == "nominal_atr"
    corto = await calc.calculate(_make_signal("sell", "short"), 8.0, 5500.0,
                                 cfg)
    assert corto["sl_price"] == 5500.0 + 90
    assert corto["tp_price"] == 5500.0 - 116.0


# ---------------------------------------------------------------------------
# (d) ATR ausente: nunca sin bracket, sin romper el fail-closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_atr_ausente_con_backstop_tp_fallback_ancho():
    """ATR caído + backstop + TP nominal → el TP cae al ANCHO DEL BACKSTOP
    espejado (más ancho que el nominal en ES) — la entrada nunca queda sin
    el bracket que TradersPost exige."""
    calc = SLTPCalculator()
    cfg = dict(CFG_NOMINAL, backstop_points=90)
    largo = await calc.calculate(_make_signal("buy", "long"), None, 5500.0,
                                 cfg)
    assert largo["passed"] is True
    assert largo["sl_price"] == 5410.0
    assert largo["tp_price"] == 5590.0                  # señal + 90
    assert largo["tp_mode"] == "nominal_backstop_width"
    corto = await calc.calculate(_make_signal("sell", "short"), None, 5500.0,
                                 cfg)
    assert corto["tp_price"] == 5410.0                  # señal − 90


@pytest.mark.asyncio
async def test_atr_ausente_sin_backstop_bloquea_como_hoy():
    """Sin backstop, el fail-closed actual manda: ATR caído → BLOCK
    (el TP nominal no abre un camino nuevo sin stop calculable)."""
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("buy", "long"), None, 5500.0,
                             dict(CFG_NOMINAL))
    assert r["passed"] is False
    assert r["reason"] == "atr_calculation_failed"


# ---------------------------------------------------------------------------
# Pipeline end-to-end + resolver
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_bracket_completo_en_audit(
    db: AsyncSession, market_data_service
) -> None:
    pipeline = FilterPipeline(market_data_service)
    config = {"mode": "normal", "score_minimum": 70, "backstop_points": 90,
              "tp_nominal_long": 11.5, "tp_nominal_short": 14.5}
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(
            db, _make_signal("buy", "long", 5500.0), _make_strategy(), config)
    assert result.outcome == "APPROVE"
    assert result.sl_price == 5410.0
    assert result.tp_price == 5500.0 + 11.5 * 8.0       # mock ATR = 8.0
    l5 = result.pipeline_execution_json["level_5"]
    assert l5["sl_mode"] == "backstop_fixed"
    assert l5["tp_mode"] == "nominal_atr"


async def _seed(db: AsyncSession, pipeline_json: dict | None) -> None:
    db.add(Strategy(strategy_id="tpnom", name="T", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="tpnom", mode="paper",
                           pipeline_config_json=pipeline_json))
    await db.commit()


@pytest.mark.asyncio
async def test_resolver_tp_nominal_desde_perfil(db: AsyncSession):
    await _seed(db, {"tp_nominal_long": 11.5, "tp_nominal_short": 14.5})
    cfg = await ConfigResolver().resolve(db, "tpnom", "MES")
    assert cfg["tp_nominal_long"] == 11.5
    assert cfg["tp_nominal_short"] == 14.5


@pytest.mark.asyncio
async def test_resolver_sin_tp_nominal_retrocompat(db: AsyncSession):
    """Ausente → no llega al config: NINGUNA estrategia cambia al
    desplegar hasta configurarlo."""
    await _seed(db, None)
    cfg = await ConfigResolver().resolve(db, "tpnom", "MES")
    assert cfg.get("tp_nominal_long") is None
    assert cfg.get("tp_nominal_short") is None


@pytest.mark.asyncio
async def test_resolver_tp_nominal_invalido_no_entra(db: AsyncSession):
    await _seed(db, {"tp_nominal_long": 0, "tp_nominal_short": -1})
    cfg = await ConfigResolver().resolve(db, "tpnom", "MES")
    assert cfg.get("tp_nominal_long") is None
    assert cfg.get("tp_nominal_short") is None
