"""NX-05 — fail-closed real para entradas sin precio.

Bug original: el normalizador convertía precio ausente/no parseable a 0.0 y el
Nivel 5 calculaba el SL contra `signal.price or 0.0` → SL absurdo (negativo en
longs) con passed=True, y la señal se APROBABA. Con el fix: precio inválido →
None en el normalizador, y el Nivel 5 BLOQUEA con `entry_price_missing`.

Las salidas no llevan precio ni SL: siguen pasando (exentas de N3-N5).
Adversarial: los tests de BLOCK fallan sin el fix (la señal salía APPROVE).
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.services.filter_pipeline import FilterPipeline
from app.services.signal_normalizer import SignalNormalizer
from app.services.sl_tp_calculator import SLTPCalculator


def _utcnow():
    return datetime.now(timezone.utc)


def _make_signal(action: str = "buy", sentiment: str = "long",
                 price: float | None = None) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="test_strat",
        ticker_received="MES",
        mapped_symbol="MESU2025",
        action=action,
        sentiment=sentiment,
        price=price,
        signal_ts=_utcnow(),
        dedupe_key=uuid.uuid4().hex,
    )


def _make_strategy(status: str = "paper") -> Strategy:
    return Strategy(strategy_id="test_strat", name="Test", asset_symbol="MES",
                    status=status, enabled=True)


# ---------------------------------------------------------------------------
# SLTPCalculator — el invariante endurecido
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [None, 0.0, -1.0])
async def test_calculator_blocks_without_valid_entry_price(bad_price):
    calc = SLTPCalculator()
    result = await calc.calculate(
        _make_signal(price=bad_price), atr=8.0, entry_price=bad_price,
        config={"sl_atr_multiplier": 1.5},
    )
    assert result["passed"] is False
    assert result["reason"] == "entry_price_missing"
    assert result["sl_price"] is None


@pytest.mark.asyncio
async def test_calculator_still_passes_with_valid_price():
    calc = SLTPCalculator()
    result = await calc.calculate(
        _make_signal(price=5500.0), atr=8.0, entry_price=5500.0,
        config={"sl_atr_multiplier": 1.5},
    )
    assert result["passed"] is True
    assert result["sl_price"] == pytest.approx(5500.0 - 12.0)


# ---------------------------------------------------------------------------
# Pipeline end-to-end — ADVERSARIAL (sin el fix la señal salía APPROVE con
# sl_price = 0 − ATR×k, un stop negativo sin sentido)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entry_without_price_blocks_at_level_5(
    db: AsyncSession, market_data_service
) -> None:
    signal = _make_signal(price=None)
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, _make_strategy(), {"global_mode": "normal"})

    assert result.outcome == "BLOCK", (
        f"esperaba BLOCK, salió {result.outcome} con sl_price={result.sl_price} "
        "(el bug NX-05: SL absurdo con passed=True)"
    )
    assert result.block_reason == "entry_price_missing"
    assert result.block_level == 5
    assert result.sl_price is None


@pytest.mark.asyncio
async def test_exit_without_price_still_passes(
    db: AsyncSession, market_data_service
) -> None:
    """Las salidas no llevan SL ni precio — siguen exentas (prioridad de cierre)."""
    signal = _make_signal(action="exit", sentiment="flat", price=None)
    pipeline = FilterPipeline(market_data_service)

    result = await pipeline.evaluate(db, signal, _make_strategy(), {"global_mode": "normal"})

    assert result.outcome == "APPROVE"
    assert result.sl_price is None


# ---------------------------------------------------------------------------
# Normalizador — precio inválido/ausente/<=0 → None (no 0.0)
# ---------------------------------------------------------------------------

async def _normalize(db: AsyncSession, payload: dict) -> NormalizedSignal:
    raw = RawSignal(source="luxalgo", strategy_id="s1", payload_json=payload,
                    token_valid=True)
    db.add(raw)
    await db.flush()
    return await SignalNormalizer().normalize(db, raw.id, "s1", payload)


_BASE = {"ticker": "MES", "action": "buy", "sentiment": "long",
         "quantity": "1", "interval": "5"}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload_price", ["N/A", "", "0", "-5"])
async def test_normalizer_invalid_price_becomes_none(
    db: AsyncSession, payload_price
) -> None:
    norm = await _normalize(db, {**_BASE, "price": payload_price})
    assert norm.price is None


@pytest.mark.asyncio
async def test_normalizer_missing_price_becomes_none(db: AsyncSession) -> None:
    norm = await _normalize(db, dict(_BASE))
    assert norm.price is None


@pytest.mark.asyncio
async def test_normalizer_valid_price_kept(db: AsyncSession) -> None:
    norm = await _normalize(db, {**_BASE, "price": "5500.25"})
    assert float(norm.price) == pytest.approx(5500.25)
