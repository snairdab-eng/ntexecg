"""FIX-D2 — tick rounding + fixed-decimal payload serialization.

Covers: round_to_tick to the catalog tick (nearest, tie → up, missing tick →
unchanged); dumps() renders fixed-decimal never scientific; ES/GC byte-for-byte
parity with json.dumps; exact serialized string for 6J (tick 5e-7) and 6E (5e-5);
SLTPCalculator and PayloadBuilder.build_scaled snap prices to the tick; and the
TradersPostClient sends the fixed-decimal body (D-5: extras.atr_value never sci).
"""
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.normalized_signal import NormalizedSignal
from app.services.filter_pipeline import PipelineResult
from app.services.payload_builder import PayloadBuilder
from app.services.sl_tp_calculator import SLTPCalculator
from app.services.tp_format import dumps, round_to_tick, to_fixed_str

# Real catalog ticks (instrument_catalog.json)
TICK = {"ES": 0.25, "GC": 0.10, "6E": 0.00005, "6J": 0.0000005}


# ---------------------------------------------------------------------------
# 1) round_to_tick — nearest multiple, tie → up, missing tick → unchanged
# ---------------------------------------------------------------------------

def _is_multiple(price, tick):
    return (Decimal(str(price)) / Decimal(str(tick))) % 1 == 0


def test_round_to_tick_nearest():
    assert round_to_tick(5484.13, TICK["ES"]) == 5484.25    # .52 → up
    assert round_to_tick(5484.12, TICK["ES"]) == 5484.0     # .48 → down
    assert round_to_tick(1913.44, TICK["GC"]) == 1913.4
    assert round_to_tick(1.083273, TICK["6E"]) == 1.08325
    # 6J: odd 5e-7 tick, price snaps to an exact 7-decimal multiple
    r = round_to_tick(0.00679763, TICK["6J"])
    assert _is_multiple(r, TICK["6J"]) and to_fixed_str(r) == "0.0067975"


def test_round_to_tick_half_goes_up():
    # exact half-tick tie rounds toward +inf (documented boundary)
    assert round_to_tick(5484.125, TICK["ES"]) == 5484.25
    assert round_to_tick(1913.45, TICK["GC"]) == 1913.5


def test_round_to_tick_missing_or_invalid_tick_unchanged():
    assert round_to_tick(1.23456789, None) == 1.23456789
    assert round_to_tick(1.23456789, 0) == 1.23456789
    assert round_to_tick(1.23456789, -0.25) == 1.23456789
    assert round_to_tick(None, TICK["ES"]) is None


# ---------------------------------------------------------------------------
# 2) to_fixed_str / dumps — never scientific; ES/GC byte-for-byte with json.dumps
# ---------------------------------------------------------------------------

def test_to_fixed_str_never_scientific():
    assert to_fixed_str(0.0000005) == "0.0000005"      # 6J tick, json → "5e-07"
    assert to_fixed_str(0.00005) == "0.00005"          # 6E tick, json → "5e-05"
    assert to_fixed_str(0.0000015) == "0.0000015"      # atr (D-5), json → "1.5e-06"
    # matches Python's repr where repr has no exponent → no regression
    for v in (5484.0, 8.0, 5500.25, 1913.4, 0.001):
        assert to_fixed_str(v) == repr(v)


def _es_payload():
    return {"ticker": "ESU2025", "action": "buy", "signalPrice": 5500.25,
            "quantity": 3, "sentiment": "long",
            "stopLoss": {"type": "stop", "stopPrice": 5484.0},
            "takeProfit": {"type": "limit", "limitPrice": 5520.5},
            "extras": {"strategy_id": "es", "atr_value": 8.0, "score": 100,
                       "q": None, "on": True, "legs": [1, 2.5]}}


def test_dumps_byte_for_byte_with_json_when_no_scientific():
    # ES (and GC-like) payloads have no scientific-notation floats → identical bytes
    p = _es_payload()
    assert dumps(p) == json.dumps(p)
    gc = {"ticker": "GCQ2025", "stopLoss": {"stopPrice": 1913.4},
          "takeProfit": {"limitPrice": 1920.7}, "signalPrice": 1913.0}
    assert dumps(gc) == json.dumps(gc)


def test_dumps_fixed_decimal_no_scientific_for_fx():
    p = {"stopPrice": round_to_tick(0.00679763, TICK["6J"]),
         "atr": 0.0000015, "e6": 0.00005}
    out = dumps(p)
    assert "e-" not in out.lower()
    assert '"stopPrice": 0.0067975' in out
    assert '"atr": 0.0000015' in out            # D-5: atr never scientific
    assert '"e6": 0.00005' in out
    # json.dumps WOULD have used scientific here — proves the fix is doing work
    assert "e-" in json.dumps(p).lower()


# ---------------------------------------------------------------------------
# 3) Exact serialized string of a full 6J / 6E payload (odd tick represented)
# ---------------------------------------------------------------------------

def _signal(mapped, price, action="buy", sentiment="long", role="entry_long", qty=2):
    s = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="fx", ticker_received=mapped[:2],
        mapped_symbol=mapped, action=action, sentiment=sentiment, signal_role=role,
        price=price, quantity=qty,
        signal_ts=datetime(2026, 7, 16, tzinfo=timezone.utc), dedupe_key="k")
    s.id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    return s


def test_exact_payload_string_6j():
    # tick-aligned bracket as the calculator would emit it (5e-7 → 7 decimals)
    pr = PipelineResult(outcome="APPROVE", score=100, sl_price=0.0067975,
                        tp_price=0.0068025, atr_value=0.0000015,
                        market_data_provider="Mock")
    payload = PayloadBuilder().build(
        _signal("6JU2025", 0.0068), None, {"tick_size": TICK["6J"]}, pr)
    expected = (
        '{"ticker": "6JU2025", "action": "buy", "signalPrice": 0.0068, '
        '"quantity": 2, "sentiment": "long", '
        '"stopLoss": {"type": "stop", "stopPrice": 0.0067975}, '
        '"takeProfit": {"type": "limit", "limitPrice": 0.0068025}, '
        '"extras": {"strategy_id": "fx", '
        '"signal_id": "00000000-0000-0000-0000-000000000001", '
        '"ntexecg_score": 100, "ntexecg_quality": null, "filters_active": false, '
        '"atr_value": 0.0000015, "sl_multiplier": null, "provider": "Mock"}}'
    )
    assert dumps(payload) == expected
    assert "e-" not in dumps(payload).lower()


def test_exact_payload_string_6e():
    pr = PipelineResult(outcome="APPROVE", score=90, sl_price=1.08320,
                        tp_price=1.08330, atr_value=0.00005,
                        market_data_provider="Mock")
    payload = PayloadBuilder().build(
        _signal("6EU2025", 1.08325), None, {"tick_size": TICK["6E"]}, pr)
    out = dumps(payload)
    assert '"stopPrice": 1.0832' in out
    assert '"limitPrice": 1.0833' in out
    assert '"atr_value": 0.00005' in out
    assert "e-" not in out.lower()


# ---------------------------------------------------------------------------
# 4) SLTPCalculator snaps sl/tp to the tick; build_scaled snaps limitPrice
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_calculator_rounds_sl_tp_to_tick_6j():
    signal = _signal("6JU2025", 0.0068)
    # backstop in price units (not tick-aligned) → sl/tp must snap to 5e-7
    config = {"tick_size": TICK["6J"], "backstop_points": 0.0000123,
              "tp_nominal_long": 2.0}
    calc = await SLTPCalculator().calculate(
        signal, atr=0.0000015, entry_price=0.0068, config=config)
    assert calc["passed"] is True
    assert _is_multiple(calc["sl_price"], TICK["6J"])
    assert _is_multiple(calc["tp_price"], TICK["6J"])
    assert calc["sl_price"] == round_to_tick(0.0068 - 0.0000123, TICK["6J"])


@pytest.mark.asyncio
async def test_calculator_no_tick_leaves_price_raw():
    signal = _signal("ESU2025", 5500.0, sentiment="long")
    config = {"sl_atr_multiplier": 1.5}       # no tick_size → unchanged
    calc = await SLTPCalculator().calculate(
        signal, atr=8.3, entry_price=5500.0, config=config)
    assert calc["sl_price"] == 5500.0 - 8.3 * 1.5      # 5487.55, untouched


def test_build_scaled_rounds_limit_to_tick_6e():
    se = {"mode": "execute", "quantities": [2, 2], "levels": [3.0],
          "max_micro_contracts": 10}
    config = {"scale_entry": se, "tick_size": TICK["6E"]}
    pr = PipelineResult(outcome="APPROVE", score=100, sl_price=1.0830,
                        tp_price=None, atr_value=0.000123,
                        market_data_provider="Mock")
    legs = PayloadBuilder().build_scaled(
        _signal("6EU2025", 1.08325), None, config, pr)
    add = next(l for l in legs if l.get("orderType") == "limit")
    # limit = 1.08325 − 3.0×0.000123 = 1.082881 → snap to 5e-5
    assert add["limitPrice"] == round_to_tick(1.08325 - 3.0 * 0.000123, TICK["6E"])
    assert _is_multiple(add["limitPrice"], TICK["6E"])
    assert "e-" not in dumps(legs[0]).lower() and "e-" not in dumps(add).lower()


# ---------------------------------------------------------------------------
# 5) TradersPostClient sends the fixed-decimal body (content=, not json=)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_sends_fixed_decimal_body(monkeypatch):
    import app.services.traderspost_client as tc

    captured = {}

    class _Resp:
        status_code = 200
        text = "ok"

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, content=None, headers=None):
            captured["content"] = content
            return _Resp()

    monkeypatch.setattr(tc.httpx, "AsyncClient", _Client)

    async def _no_sleep(*a, **k): return None
    monkeypatch.setattr(tc.asyncio, "sleep", _no_sleep)

    payload = {"ticker": "6JU2025", "action": "buy",
               "stopLoss": {"type": "stop", "stopPrice": 0.0067975},
               "extras": {"atr_value": 0.0000015}}
    client = tc.TradersPostClient(SimpleNamespace(entry_signal_timeout_secs=30))
    await client.send("https://app.traderspost.io/webhook/x?token=S",
                      payload, "entry_long", dry_run=False)

    body = captured["content"]
    assert body is not None and "e-" not in body.lower()
    assert '"stopPrice": 0.0067975' in body
    assert '"atr_value": 0.0000015' in body        # D-5 fixed on the wire
    assert dumps(payload) == body                  # exactly the fixed serializer
