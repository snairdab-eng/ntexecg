"""SignalNormalizer tests.

All tests use SQLite in-memory via conftest.py fixtures.
Key invariants verified:
  - ticker_received is stored EXACTLY as payload["ticker"] — including spaces
  - strategy_id always from the function arg, never from payload
  - sentiment → action + signal_role mapping
  - quantity/price cast from strings
  - timeframe normalization
  - dedupe_key is deterministic (same inputs → same hash)
"""
import hashlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.raw_signal import RawSignal
from app.services.signal_normalizer import SignalNormalizer, make_dedupe_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_payload(**overrides) -> dict:
    base = {
        "ticker": "MES",
        "action": "buy",
        "sentiment": "long",
        "quantity": "1",
        "price": "5500.00",
        "interval": "5",
    }
    base.update(overrides)
    return base


async def _make_raw(db: AsyncSession, strategy_id: str = "strat_1") -> RawSignal:
    raw = RawSignal(
        strategy_id=strategy_id,
        payload_json={"ticker": "MES"},
        token_valid=True,
    )
    db.add(raw)
    await db.flush()
    return raw


# ---------------------------------------------------------------------------
# ticker_received: no transformation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ticker_received_exact_copy(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    payload = _base_payload(ticker="MES")
    norm = await SignalNormalizer().normalize(db, raw.id, "strat_1", payload)
    assert norm.ticker_received == "MES"


@pytest.mark.asyncio
async def test_ticker_received_preserves_spaces(db: AsyncSession) -> None:
    """ticker_received must be copied verbatim — no strip()."""
    raw = await _make_raw(db)
    payload = _base_payload(ticker="  MES  ")
    norm = await SignalNormalizer().normalize(db, raw.id, "strat_1", payload)
    assert norm.ticker_received == "  MES  "


@pytest.mark.asyncio
async def test_ticker_received_lowercase_preserved(db: AsyncSession) -> None:
    """No .upper() transformation — 'mes' stays 'mes'."""
    raw = await _make_raw(db)
    payload = _base_payload(ticker="mes")
    norm = await SignalNormalizer().normalize(db, raw.id, "strat_1", payload)
    assert norm.ticker_received == "mes"


@pytest.mark.asyncio
async def test_ticker_received_mes_bang_preserved(db: AsyncSession) -> None:
    """'MES1!' is stored as-is (will map to None — not our concern here)."""
    raw = await _make_raw(db)
    payload = _base_payload(ticker="MES1!")
    norm = await SignalNormalizer().normalize(db, raw.id, "strat_1", payload)
    assert norm.ticker_received == "MES1!"


# ---------------------------------------------------------------------------
# strategy_id: always from argument, never from payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strategy_id_from_arg_not_payload(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    payload = _base_payload()
    payload["strategy_id"] = "injected_from_payload"  # must be ignored
    norm = await SignalNormalizer().normalize(db, raw.id, "correct_strategy", payload)
    assert norm.strategy_id == "correct_strategy"


# ---------------------------------------------------------------------------
# sentiment → action + signal_role
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sentiment_long(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(sentiment="long", action="buy")
    )
    assert norm.action == "buy"
    assert norm.signal_role == "entry_long"
    assert norm.sentiment == "long"


@pytest.mark.asyncio
async def test_sentiment_short(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(sentiment="short", action="sell")
    )
    assert norm.action == "sell"
    assert norm.signal_role == "entry_short"
    assert norm.sentiment == "short"


@pytest.mark.asyncio
async def test_sentiment_flat(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(sentiment="flat", action="sell")
    )
    assert norm.action == "exit"
    assert norm.sentiment == "flat"


@pytest.mark.asyncio
async def test_sentiment_unknown(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(sentiment="weird", action="buy")
    )
    # Falls through to raw_action
    assert norm.action == "buy"
    assert norm.signal_role == "unknown"


# ---------------------------------------------------------------------------
# quantity cast
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quantity_string_to_int(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(quantity="3")
    )
    assert norm.quantity == 3


@pytest.mark.asyncio
async def test_quantity_defaults_to_1_on_missing(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    payload = _base_payload()
    del payload["quantity"]
    norm = await SignalNormalizer().normalize(db, raw.id, "s1", payload)
    assert norm.quantity == 1


@pytest.mark.asyncio
async def test_quantity_defaults_to_1_on_invalid(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(quantity="abc")
    )
    assert norm.quantity == 1


# ---------------------------------------------------------------------------
# price cast
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_price_string_to_float(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(price="5500.25")
    )
    assert norm.price == pytest.approx(5500.25)


@pytest.mark.asyncio
async def test_price_defaults_to_zero_on_invalid(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(price="N/A")
    )
    assert norm.price == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# timeframe normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("interval,expected", [
    ("5", "5m"),
    ("15", "15m"),
    ("60", "1h"),
    ("240", "4h"),
    ("D", "1d"),
    ("W", "1w"),
    ("1", "1m"),
    ("30", "30m"),
])
@pytest.mark.asyncio
async def test_timeframe_normalization(
    db: AsyncSession, interval: str, expected: str
) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(interval=interval)
    )
    assert norm.timeframe == expected


# ---------------------------------------------------------------------------
# dedupe_key: deterministic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedupe_key_deterministic(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    payload = _base_payload()

    norm1 = await SignalNormalizer().normalize(db, raw.id, "s1", payload)
    norm2 = await SignalNormalizer().normalize(db, raw.id, "s1", payload)
    assert norm1.dedupe_key == norm2.dedupe_key


@pytest.mark.asyncio
async def test_dedupe_key_differs_on_different_ticker(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    n1 = await SignalNormalizer().normalize(db, raw.id, "s1", _base_payload(ticker="MES"))
    n2 = await SignalNormalizer().normalize(db, raw.id, "s1", _base_payload(ticker="MNQ"))
    assert n1.dedupe_key != n2.dedupe_key


@pytest.mark.asyncio
async def test_dedupe_key_differs_on_different_strategy(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    payload = _base_payload()
    n1 = await SignalNormalizer().normalize(db, raw.id, "strategy_A", payload)
    n2 = await SignalNormalizer().normalize(db, raw.id, "strategy_B", payload)
    assert n1.dedupe_key != n2.dedupe_key


def test_make_dedupe_key_standalone() -> None:
    """make_dedupe_key is a pure function — no DB needed."""
    key = make_dedupe_key("s1", "MES", "buy", "long", "5500.00", "5")
    assert len(key) == 64  # SHA256 hex = 64 chars
    # Same inputs → same output
    assert key == make_dedupe_key("s1", "MES", "buy", "long", "5500.00", "5")
    # Different input → different output
    assert key != make_dedupe_key("s1", "MNQ", "buy", "long", "5500.00", "5")


# ---------------------------------------------------------------------------
# symbol mapper integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mapped_symbol_none_when_not_in_db(db: AsyncSession) -> None:
    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(ticker="MES")
    )
    # No SymbolMap rows in test DB → None
    assert norm.mapped_symbol is None


@pytest.mark.asyncio
async def test_mapped_symbol_populated_when_in_db(db: AsyncSession) -> None:
    from app.models.symbol_map import SymbolMap

    db.add(SymbolMap(
        tv_symbol="MES",
        mapped_symbol="MESU2025",
        exchange="CME",
        contract_type="futures_micro",
        pine_script_config='"ticker": "MES"',
        active=True,
    ))
    await db.flush()

    raw = await _make_raw(db)
    norm = await SignalNormalizer().normalize(
        db, raw.id, "s1", _base_payload(ticker="MES")
    )
    assert norm.mapped_symbol == "MESU2025"
