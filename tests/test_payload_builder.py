"""PayloadBuilder tests.

CRITICAL: entries ALWAYS include stopLoss; exits NEVER do.
ticker = mapped_symbol, not ticker_received.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.normalized_signal import NormalizedSignal
from app.services.filter_pipeline import PipelineResult
from app.services.payload_builder import PayloadBuilder


def _signal(action="buy", sentiment="long", role="entry_long",
            mapped="MESU2025", price=5500.0, qty=1) -> NormalizedSignal:
    s = NormalizedSignal(
        raw_signal_id=uuid.uuid4(),
        strategy_id="mes_strat",
        ticker_received="MES",
        mapped_symbol=mapped,
        action=action,
        sentiment=sentiment,
        signal_role=role,
        price=price,
        quantity=qty,
        signal_ts=datetime.now(timezone.utc),
        dedupe_key=uuid.uuid4().hex,
    )
    s.id = uuid.uuid4()
    return s


def _result(sl=5484.0, tp=None, score=100, atr=8.0) -> PipelineResult:
    return PipelineResult(
        outcome="APPROVE", score=score, sl_price=sl, tp_price=tp,
        atr_value=atr, market_data_provider="MockMarketDataProvider",
    )


def test_entry_uses_mapped_symbol_not_ticker_received() -> None:
    payload = PayloadBuilder().build(
        _signal(mapped="MESU2025"), None, {}, _result()
    )
    assert payload["ticker"] == "MESU2025"


def test_entry_includes_stop_loss() -> None:
    payload = PayloadBuilder().build(_signal(), None, {}, _result(sl=5484.0))
    assert payload["stopLoss"] == {"type": "stop", "stopPrice": 5484.0}


def test_entry_short_includes_stop_loss() -> None:
    payload = PayloadBuilder().build(
        _signal(action="sell", sentiment="short", role="entry_short"),
        None, {}, _result(sl=5512.0),
    )
    assert payload["action"] == "sell"
    assert payload["sentiment"] == "short"
    assert payload["stopLoss"]["stopPrice"] == 5512.0


def test_entry_without_sl_price_raises() -> None:
    with pytest.raises(ValueError, match="without sl_price is forbidden"):
        PayloadBuilder().build(_signal(), None, {}, _result(sl=None))


def test_reversal_to_long_is_entry_with_sl() -> None:
    payload = PayloadBuilder().build(
        _signal(action="buy", role="reversal_to_long"), None, {}, _result()
    )
    assert "stopLoss" in payload


def test_exit_has_no_stop_loss() -> None:
    payload = PayloadBuilder().build(
        _signal(action="exit", sentiment="flat", role="exit_long"),
        None, {}, _result(sl=None),
    )
    assert "stopLoss" not in payload
    assert "takeProfit" not in payload
    assert payload["action"] == "exit"
    assert "sentiment" not in payload  # TradersPost: sentiment invalid on exits


def test_exit_does_not_raise_without_sl() -> None:
    # Exits never need SL — must not raise even when sl_price is None
    payload = PayloadBuilder().build(
        _signal(action="exit", role="exit_short"), None, {}, _result(sl=None)
    )
    assert payload["ticker"] == "MESU2025"


def test_take_profit_included_when_tp_price_set() -> None:
    payload = PayloadBuilder().build(_signal(), None, {}, _result(sl=5484.0, tp=5520.0))
    assert payload["takeProfit"] == {"type": "limit", "limitPrice": 5520.0}


def test_take_profit_omitted_when_none() -> None:
    payload = PayloadBuilder().build(_signal(), None, {}, _result(sl=5484.0, tp=None))
    assert "takeProfit" not in payload


def test_extras_always_present() -> None:
    config = {"sl_atr_multiplier": 1.5}
    payload = PayloadBuilder().build(_signal(), None, config, _result(score=87, atr=8.0))
    extras = payload["extras"]
    assert extras["strategy_id"] == "mes_strat"
    assert extras["ntexecg_score"] == 87
    assert extras["atr_value"] == 8.0
    assert extras["sl_multiplier"] == 1.5
    assert extras["provider"] == "MockMarketDataProvider"
    assert "signal_id" in extras


def test_signal_price_and_quantity_passed_through() -> None:
    payload = PayloadBuilder().build(
        _signal(price=5500.25, qty=3), None, {}, _result()
    )
    assert payload["signalPrice"] == 5500.25
    assert payload["quantity"] == 3
