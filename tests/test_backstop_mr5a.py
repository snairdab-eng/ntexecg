"""MR-5a — Backstop como stop obligatorio de $/puntos fijos en L5.

Directiva 4 (CONTRATO/PROMPT_Motor_Riesgo_BUILD.md) + recomendacion.json del
Motor de Riesgo: con `backstop_points` configurado en el pipeline_config_json
de la estrategia, el SL es un stop de PRECIO FIJO anclado a la señal
(SL = señal ∓ pts) — reemplaza el k×ATR y NO depende del ATR. Fail-closed
reforzado: sin precio de señal → BLOCK (jamás enviar sin stop). Sin
backstop configurado → la lógica ATR actual intacta (retrocompat).

Adversariales (rojo antes del fix):
  (a) backstop long/short → SL = señal ∓ pts (y no señal ∓ k·ATR)
  (b) sin backstop → ATR como hoy (retrocompat, sin regresión)
  (c) backstop sin precio de señal → BLOCK entry_price_missing
  (d) backstop con ATR caído → APPROVE igual (el punto del precio fijo);
      kill-switch (global paused) intacto con backstop configurado
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


# ---------------------------------------------------------------------------
# (a) SLTPCalculator — stop fijo anclado a la señal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backstop_long_sl_fijo_desde_senal():
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("buy", "long"), atr=8.0,
                             entry_price=5500.0,
                             config={"backstop_points": 90,
                                     "sl_atr_multiplier": 1.5})
    assert r["passed"] is True
    assert r["sl_price"] == 5500.0 - 90          # NO 5500 − 8·1.5 = 5488
    assert r["sl_price"] != 5500.0 - 8.0 * 1.5


@pytest.mark.asyncio
async def test_backstop_short_sl_fijo_desde_senal():
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("sell", "short"), atr=8.0,
                             entry_price=5500.0,
                             config={"backstop_points": 90,
                                     "sl_atr_multiplier": 1.5})
    assert r["passed"] is True
    assert r["sl_price"] == 5500.0 + 90


@pytest.mark.asyncio
async def test_backstop_no_depende_del_atr():
    """(d) El punto del precio fijo: ATR caído → el stop se calcula igual."""
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("buy", "long"), atr=None,
                             entry_price=5500.0,
                             config={"backstop_points": 90})
    assert r["passed"] is True
    assert r["sl_price"] == 5410.0
    # invariante de siempre: passed=True ⇒ sl_price nunca None
    assert r["sl_price"] is not None


@pytest.mark.asyncio
async def test_backstop_tp_atr_sigue_opcional():
    """El TP (inactivo por default) no cambia: con multiplicador y ATR se
    calcula como hoy; sin ATR queda None (el TP es MR-5c)."""
    calc = SLTPCalculator()
    cfg = {"backstop_points": 90, "tp_atr_multiplier": 2.0}
    con_atr = await calc.calculate(_make_signal(), 8.0, 5500.0, cfg)
    assert con_atr["tp_price"] == 5500.0 + 16.0
    sin_atr = await calc.calculate(_make_signal(), None, 5500.0, cfg)
    assert sin_atr["passed"] is True and sin_atr["tp_price"] is None


# ---------------------------------------------------------------------------
# (c) Fail-closed reforzado: sin precio de señal → BLOCK, jamás sin stop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backstop_sin_precio_bloquea():
    calc = SLTPCalculator()
    for precio in (None, 0.0, -1.0):
        r = await calc.calculate(_make_signal(price=precio), atr=8.0,
                                 entry_price=precio,
                                 config={"backstop_points": 90})
        assert r["passed"] is False, f"precio={precio}"
        assert r["reason"] == "entry_price_missing"
        assert r["sl_price"] is None


# ---------------------------------------------------------------------------
# (b) Retrocompat: sin backstop_points, la lógica ATR intacta
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sin_backstop_sl_por_atr_como_hoy():
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal("buy", "long"), atr=8.0,
                             entry_price=5500.0,
                             config={"sl_atr_multiplier": 1.5})
    assert r["passed"] is True
    assert r["sl_price"] == 5500.0 - 12.0


@pytest.mark.asyncio
async def test_sin_backstop_atr_none_bloquea_como_hoy():
    calc = SLTPCalculator()
    r = await calc.calculate(_make_signal(), atr=None, entry_price=5500.0,
                             config={"sl_atr_multiplier": 1.5})
    assert r["passed"] is False
    assert r["reason"] == "atr_calculation_failed"


@pytest.mark.asyncio
async def test_backstop_invalido_cae_a_atr():
    """Valores no válidos (0, negativo, string) NO activan el backstop —
    equivalen a ausente (retrocompat, fail-safe hacia la lógica actual)."""
    calc = SLTPCalculator()
    for malo in (0, -90, "90", None, True):
        r = await calc.calculate(_make_signal(), 8.0, 5500.0,
                                 {"backstop_points": malo,
                                  "sl_atr_multiplier": 1.5})
        assert r["sl_price"] == 5500.0 - 12.0, f"backstop={malo!r}"


# ---------------------------------------------------------------------------
# Pipeline end-to-end (L5) + kill-switch intacto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipeline_backstop_aprueba_sin_atr(
    db: AsyncSession, market_data_service
) -> None:
    """L5 con backstop: bridge sin ATR → APPROVE igual, SL = señal − pts,
    y el modo queda en el audit trail."""
    async def _sin_atr(*a, **kw):
        return None

    market_data_service.get_atr = _sin_atr
    pipeline = FilterPipeline(market_data_service)
    config = {"mode": "normal", "score_minimum": 70,
              "sl_atr_multiplier": 1.5, "backstop_points": 90}
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(
            db, _make_signal("buy", "long", 5500.0), _make_strategy(), config)
    assert result.outcome == "APPROVE"
    assert result.sl_price == 5410.0
    assert result.pipeline_execution_json["level_5"]["sl_mode"] == \
        "backstop_fixed"


@pytest.mark.asyncio
async def test_pipeline_backstop_sin_precio_bloquea_l5(
    db: AsyncSession, market_data_service
) -> None:
    pipeline = FilterPipeline(market_data_service)
    config = {"mode": "normal", "score_minimum": 70, "backstop_points": 90}
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(
            db, _make_signal(price=None), _make_strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_level == 5
    assert result.block_reason == "entry_price_missing"


@pytest.mark.asyncio
async def test_kill_switch_intacto_con_backstop(
    db: AsyncSession, market_data_service
) -> None:
    """(d) El freno global (L1) manda igual con backstop configurado — el
    backstop cambia CÓMO se calcula el SL, nada más."""
    pipeline = FilterPipeline(market_data_service)
    config = {"global_mode": "paused", "backstop_points": 90,
              "score_minimum": 70}
    result = await pipeline.evaluate(
        db, _make_signal(), _make_strategy(), config)
    assert result.outcome == "BLOCK"
    assert result.block_level == 1
    assert result.block_reason == "global_paused"


# ---------------------------------------------------------------------------
# ConfigResolver — backstop_points desde pipeline_config_json (opt-in)
# ---------------------------------------------------------------------------

async def _seed(db: AsyncSession, pipeline_json: dict | None) -> None:
    db.add(Strategy(strategy_id="bkstp", name="B", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="bkstp", mode="paper",
                           pipeline_config_json=pipeline_json))
    await db.commit()


@pytest.mark.asyncio
async def test_resolver_backstop_points_desde_perfil(db: AsyncSession):
    await _seed(db, {"backstop_points": 90})
    cfg = await ConfigResolver().resolve(db, "bkstp", "MES")
    assert cfg["backstop_points"] == 90


@pytest.mark.asyncio
async def test_resolver_sin_backstop_retrocompat(db: AsyncSession):
    """Ausente → la clave no llega al config (comportamiento actual):
    NINGUNA estrategia cambia al desplegar sin configurar backstop."""
    await _seed(db, None)
    cfg = await ConfigResolver().resolve(db, "bkstp", "MES")
    assert cfg.get("backstop_points") is None


@pytest.mark.asyncio
async def test_resolver_backstop_invalido_no_entra(db: AsyncSession):
    await _seed(db, {"backstop_points": -90})
    cfg = await ConfigResolver().resolve(db, "bkstp", "MES")
    assert cfg.get("backstop_points") is None
