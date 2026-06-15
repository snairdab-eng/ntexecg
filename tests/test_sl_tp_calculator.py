"""SLTPCalculator tests.

CRITICAL invariant: sl_price is NEVER None when passed=True.
TP is managed by LuxAlgo (tp_atr_multiplier prepared but inactive by default).
"""
import pytest

from app.models.normalized_signal import NormalizedSignal
from app.services.sl_tp_calculator import SLTPCalculator


def _base_signal(action: str = "buy", price: float = 5500.0) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=__import__("uuid").uuid4(),
        strategy_id="test",
        ticker_received="MES",
        action=action,
        sentiment="long" if action == "buy" else "short",
        price=price,
        signal_ts=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
        dedupe_key="test_key",
    )


@pytest.mark.asyncio
async def test_long_entry_calculates_sl() -> None:
    signal = _base_signal(action="buy", price=5500.0)
    config = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": None}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=8.0, entry_price=5500.0, config=config)

    assert result["passed"] is True
    assert result["sl_price"] == 5500.0 - (8.0 * 1.5)  # 5488.0
    assert result["tp_price"] is None
    assert result["atr_value"] == 8.0


@pytest.mark.asyncio
async def test_short_entry_calculates_sl() -> None:
    signal = _base_signal(action="sell", price=5500.0)
    config = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": None}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=8.0, entry_price=5500.0, config=config)

    assert result["passed"] is True
    assert result["sl_price"] == 5500.0 + (8.0 * 1.5)  # 5512.0
    assert result["tp_price"] is None
    assert result["atr_value"] == 8.0


@pytest.mark.asyncio
async def test_atr_none_returns_failed() -> None:
    signal = _base_signal(action="buy", price=5500.0)
    config = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": None}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=None, entry_price=5500.0, config=config)

    assert result["passed"] is False
    assert result["reason"] == "atr_calculation_failed"
    assert result["sl_price"] is None
    assert result["tp_price"] is None


@pytest.mark.asyncio
async def test_atr_zero_returns_failed() -> None:
    signal = _base_signal(action="buy", price=5500.0)
    config = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": None}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=0.0, entry_price=5500.0, config=config)

    assert result["passed"] is False
    assert result["reason"] == "atr_calculation_failed"


@pytest.mark.asyncio
async def test_invariant_passed_true_has_sl_price() -> None:
    """Invariant: if passed=True, sl_price is NEVER None."""
    signal = _base_signal(action="buy", price=5500.0)
    config = {"sl_atr_multiplier": 2.0, "tp_atr_multiplier": None}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=10.0, entry_price=5500.0, config=config)

    assert result["passed"] is True
    assert result["sl_price"] is not None
    assert isinstance(result["sl_price"], float)


@pytest.mark.asyncio
async def test_custom_multiplier() -> None:
    signal = _base_signal(action="buy", price=5500.0)
    config = {"sl_atr_multiplier": 3.0, "tp_atr_multiplier": None}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=5.0, entry_price=5500.0, config=config)

    assert result["passed"] is True
    assert result["sl_price"] == 5500.0 - (5.0 * 3.0)  # 5485.0


@pytest.mark.asyncio
async def test_tp_calculation_when_enabled() -> None:
    signal = _base_signal(action="buy", price=5500.0)
    config = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": 2.0}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=8.0, entry_price=5500.0, config=config)

    assert result["passed"] is True
    assert result["sl_price"] == 5488.0
    assert result["tp_price"] == 5500.0 + (8.0 * 2.0)  # 5516.0


@pytest.mark.asyncio
async def test_short_tp_calculation() -> None:
    signal = _base_signal(action="sell", price=5500.0)
    config = {"sl_atr_multiplier": 1.5, "tp_atr_multiplier": 2.0}
    calc = SLTPCalculator()

    result = await calc.calculate(signal, atr=8.0, entry_price=5500.0, config=config)

    assert result["passed"] is True
    assert result["sl_price"] == 5512.0
    assert result["tp_price"] == 5500.0 - (8.0 * 2.0)  # 5484.0
