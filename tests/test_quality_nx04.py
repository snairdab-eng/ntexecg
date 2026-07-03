"""NX-04 — calidad de señal UNKNOWN/LOW/MEDIUM/HIGH (Anexo 25 §1-bis).

Regla: un score=100 solo es confiable si viene de filtros reales activos.
  - filters_active = (hay filters habilitados) OR (regime.enabled)
  - sin medición → quality=UNKNOWN (nunca HIGH); la UI no pinta el ✅ verde
  - con medición: LOW (< score_minimum, bloquea) · MEDIUM · HIGH (≥ umbral_alto)
  - el gate numérico (score ≥ score_minimum) NO cambia
  - el score ya NO parte en 100: solo existe cuando el Nivel 4 corrió
    (salidas y blocks tempranos → score None)
  - ntexecg_quality + filters_active viajan en la traza (level_4) y en
    payload.extras

Adversariales: fallan sin el fix (level_4 sin marca, extras sin etiqueta,
ribbon en verde para UNKNOWN, score=100 en salidas/blocks tempranos).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.services.filter_pipeline import FilterPipeline, PipelineResult
from app.services.payload_builder import PayloadBuilder
from app.services import quality_scorer as qs
from app.web.routes_signals import build_ribbon


def _utcnow():
    return datetime.now(timezone.utc)


def _make_signal(action="buy", sentiment="long", price=5500.0) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="test_strat",
        ticker_received="MES", mapped_symbol="MESU2025",
        action=action, sentiment=sentiment, price=price,
        signal_ts=_utcnow(), dedupe_key=uuid.uuid4().hex,
    )


def _make_strategy(status="paper") -> Strategy:
    return Strategy(strategy_id="test_strat", name="Test", asset_symbol="MES",
                    status=status, enabled=True)


# Tres subscores deterministas con barras vacías (cada uno devuelve 0.5 → 50).
_FILTERS_50 = {
    "volume_relative": {"enabled": True, "weight": 1},
    "atr_normalized": {"enabled": True, "weight": 1},
    "vwap_position": {"enabled": True, "weight": 1},
}

_SESSION_OK = patch(
    "app.services.session_validator.SessionValidator.is_within_session_config",
    return_value=True,
)


# ---------------------------------------------------------------------------
# Unidad — filters_active y quality_label (funciones puras)
# ---------------------------------------------------------------------------

def test_filters_active_variants():
    assert qs.filters_active({}) is False
    assert qs.filters_active({"filters": {}}) is False
    assert qs.filters_active(
        {"filters": {"volume_relative": {"enabled": False, "weight": 25}}}
    ) is False
    assert qs.filters_active(
        {"filters": {"volume_relative": {"enabled": True, "weight": 25}}}
    ) is True
    # regime.enabled también cuenta como medición (Anexo 25 §1-bis)
    assert qs.filters_active({"regime": {"enabled": True}}) is True


@pytest.mark.parametrize("score,active,smin,expected", [
    (100, False, 70, qs.QUALITY_UNKNOWN),   # sin medición → UNKNOWN, nunca HIGH
    (0, False, 70, qs.QUALITY_UNKNOWN),
    (100, True, 70, qs.QUALITY_HIGH),
    (80, True, 70, qs.QUALITY_HIGH),        # umbral_alto inclusive
    (79, True, 70, qs.QUALITY_MEDIUM),
    (70, True, 70, qs.QUALITY_MEDIUM),      # == score_minimum pasa como MEDIUM
    (69, True, 70, qs.QUALITY_LOW),         # < score_minimum → LOW (se bloquea)
])
def test_quality_label(score, active, smin, expected):
    assert qs.quality_label(score, active, smin) == expected


# ---------------------------------------------------------------------------
# Pipeline — traza level_4 con filters_active/quality (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entry_without_filters_is_unknown(
    db: AsyncSession, market_data_service
) -> None:
    signal = _make_signal()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, signal, _make_strategy(), {"global_mode": "normal"})

    assert result.outcome == "APPROVE"          # el gate numérico no cambia
    assert result.score == 100
    l4 = result.pipeline_execution_json["level_4"]
    assert l4["filters_active"] is False, "falta la marca filters_active (NX-04)"
    assert l4["quality"] == qs.QUALITY_UNKNOWN
    assert result.quality == qs.QUALITY_UNKNOWN
    assert result.filters_active is False


@pytest.mark.asyncio
async def test_entry_with_filters_medium(
    db: AsyncSession, market_data_service
) -> None:
    """Con barras vacías los 3 subscores dan 0.5 → score 50; smin 40 → pasa
    con calidad medida MEDIUM (50 < umbral_alto 80)."""
    signal = _make_signal()
    pipeline = FilterPipeline(market_data_service)
    config = {"global_mode": "normal", "score_minimum": 40,
              "filters": dict(_FILTERS_50)}
    with _SESSION_OK:
        result = await pipeline.evaluate(db, signal, _make_strategy(), config)

    assert result.outcome == "APPROVE"
    assert result.score == 50
    l4 = result.pipeline_execution_json["level_4"]
    assert l4["filters_active"] is True
    assert l4["quality"] == qs.QUALITY_MEDIUM


@pytest.mark.asyncio
async def test_entry_blocked_by_score_is_low(
    db: AsyncSession, market_data_service
) -> None:
    signal = _make_signal()
    pipeline = FilterPipeline(market_data_service)
    config = {"global_mode": "normal", "score_minimum": 60,
              "filters": dict(_FILTERS_50)}
    with _SESSION_OK:
        result = await pipeline.evaluate(db, signal, _make_strategy(), config)

    assert result.outcome == "BLOCK"
    assert result.block_reason == "score_below_minimum"
    l4 = result.pipeline_execution_json["level_4"]
    assert l4["quality"] == qs.QUALITY_LOW
    assert result.quality == qs.QUALITY_LOW


@pytest.mark.asyncio
async def test_regime_only_counts_as_measured(
    db: AsyncSession, market_data_service
) -> None:
    """Solo gate de régimen (sin filtros de score): filters_active=True por el
    Anexo 25; con score 100 la calidad es HIGH (medida por el gate)."""
    signal = _make_signal()
    pipeline = FilterPipeline(market_data_service)
    config = {"global_mode": "normal",
              "regime": {"enabled": True, "timeframe": "1h",
                         "allowed_regimes": ["ranging", "trending_bull",
                                             "trending_bear"]}}
    with _SESSION_OK:
        result = await pipeline.evaluate(db, signal, _make_strategy(), config)

    assert result.outcome == "APPROVE"
    l4 = result.pipeline_execution_json["level_4"]
    assert l4["filters_active"] is True
    assert l4["quality"] == qs.QUALITY_HIGH


# ---------------------------------------------------------------------------
# El score ya no parte en 100 (ADVERSARIAL: antes salidas y blocks → 100)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exit_has_no_score(db: AsyncSession, market_data_service) -> None:
    signal = _make_signal(action="exit", sentiment="flat")
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, signal, _make_strategy(), {"global_mode": "normal"})

    assert result.outcome == "APPROVE"
    assert result.score is None, (
        f"salida con score={result.score} (el bug: 'partía en 100' sin medir)")
    assert result.quality is None


@pytest.mark.asyncio
async def test_early_block_has_no_score(
    db: AsyncSession, market_data_service
) -> None:
    signal = _make_signal()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, signal, _make_strategy(status="retired"), {"global_mode": "normal"})

    assert result.outcome == "BLOCK" and result.block_level == 1
    assert result.score is None


# ---------------------------------------------------------------------------
# Payload — ntexecg_quality + filters_active en extras (ADVERSARIAL)
# ---------------------------------------------------------------------------

def _pr(**over) -> PipelineResult:
    base = dict(outcome="APPROVE", score=100, sl_price=5488.0, atr_value=8.0,
                quality=qs.QUALITY_UNKNOWN, filters_active=False)
    base.update(over)
    return PipelineResult(**base)


def test_payload_extras_carry_quality():
    payload = PayloadBuilder().build(
        _make_signal(), None, {"sl_atr_multiplier": 1.5}, _pr())
    assert payload["extras"]["ntexecg_quality"] == qs.QUALITY_UNKNOWN
    assert payload["extras"]["filters_active"] is False


def test_scaled_payload_extras_carry_quality():
    config = {"sl_atr_multiplier": 1.5,
              "scale_entry": {"mode": "execute", "levels": [0.75],
                              "quantities": [1, 1], "max_micro_contracts": 5}}
    legs = PayloadBuilder().build_scaled(
        _make_signal(), None, config,
        _pr(quality=qs.QUALITY_HIGH, filters_active=True))
    assert len(legs) == 2
    for leg in legs:
        assert leg["extras"]["ntexecg_quality"] == qs.QUALITY_HIGH
        assert leg["extras"]["filters_active"] is True


def test_payload_tolerates_results_without_quality():
    """forced_exit y el dispatch por perfil construyen SimpleNamespace — sin
    los campos nuevos el builder no debe reventar (quality → None)."""
    pr = SimpleNamespace(sl_price=None, tp_price=None, atr_value=None,
                         score=None, market_data_provider=None)
    payload = PayloadBuilder().build(
        _make_signal(action="exit", sentiment="flat"), None, {}, pr)
    assert payload["extras"]["ntexecg_quality"] is None


# ---------------------------------------------------------------------------
# UI — la cinta no pinta verde una calidad no medida (ADVERSARIAL)
# ---------------------------------------------------------------------------

def _decision(outcome="APPROVE"):
    return SimpleNamespace(outcome=outcome)


def test_ribbon_unknown_is_not_green():
    l4 = {"score": 100, "passed": True, "filters_active": False,
          "quality": qs.QUALITY_UNKNOWN}
    ribbon = build_ribbon(_decision(), {"level_4": l4}, {}, [])
    node = next(n for n in ribbon if n["key"] == "filtro")
    assert node["state"] != "pass", (
        "calidad UNKNOWN pintada en verde (el bug del ✅ del Anexo 25)")
    assert "UNKNOWN" in node["summary"]


def test_ribbon_measured_quality_is_green_with_label():
    l4 = {"score": 85, "passed": True, "filters_active": True,
          "quality": qs.QUALITY_HIGH}
    ribbon = build_ribbon(_decision(), {"level_4": l4},
                          {"score_minimum": 70}, [])
    node = next(n for n in ribbon if n["key"] == "filtro")
    assert node["state"] == "pass"
    assert "HIGH" in node["summary"]


def test_ribbon_legacy_decision_without_mark_falls_back_to_config():
    """Decisiones históricas sin filters_active en level_4: se infiere de la
    config efectiva (sin filtros → UNKNOWN, sin verde)."""
    l4 = {"score": 100, "passed": True}
    ribbon = build_ribbon(_decision(), {"level_4": l4}, {"filters": {}}, [])
    node = next(n for n in ribbon if n["key"] == "filtro")
    assert node["state"] != "pass"
    assert "UNKNOWN" in node["summary"]
