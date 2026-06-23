"""HMMService (Fase 6) — baseline regime classifier + service wrapper.

The baseline is a deterministic Kaufman-Efficiency-Ratio classifier (no ML
dependency), so it is fully unit-testable here. It stands behind the same
get_regime() interface the trained HMM will use later.
"""
from __future__ import annotations

import pytest

from app.services.hmm_service import HMMService, classify_regime


# ---------------------------------------------------------------------------
# Pure classifier
# ---------------------------------------------------------------------------

def test_classify_unknown_when_too_few_bars() -> None:
    assert classify_regime([100.0] * 10) == "unknown"
    assert classify_regime([]) == "unknown"
    assert classify_regime(None) == "unknown"


def test_classify_trending_bull() -> None:
    closes = [100.0 + i for i in range(40)]  # steady climb → ER ≈ 1
    assert classify_regime(closes) == "trending_bull"


def test_classify_trending_bear() -> None:
    closes = [100.0 - i for i in range(40)]
    assert classify_regime(closes) == "trending_bear"


def test_classify_ranging_oscillation() -> None:
    closes = [100.0 + (1.0 if i % 2 else -1.0) for i in range(40)]
    assert classify_regime(closes) == "ranging"


def test_classify_flat_is_ranging() -> None:
    assert classify_regime([100.0] * 40) == "ranging"


def test_classify_choppy_drift_is_ranging() -> None:
    # mild upward drift but lots of back-and-forth → low efficiency → ranging
    closes = [100.0 + 0.5 * i + (3.0 if i % 2 else -3.0) for i in range(40)]
    assert classify_regime(closes) == "ranging"


def test_classify_threshold_is_configurable() -> None:
    # A weak but real uptrend: ranging at strict threshold, trend at a lax one.
    closes = [100.0 + 0.3 * i + (1.0 if i % 2 else -1.0) for i in range(40)]
    assert classify_regime(closes, trend_threshold=0.9) == "ranging"
    assert classify_regime(closes, trend_threshold=0.05) == "trending_bull"


# ---------------------------------------------------------------------------
# Service wrapper
# ---------------------------------------------------------------------------

class _BarsProvider:
    def __init__(self, closes: list[float]) -> None:
        self._closes = closes

    async def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> list[dict]:
        return [{"close": c} for c in self._closes]


@pytest.mark.asyncio
async def test_service_reads_bars_and_classifies() -> None:
    svc = HMMService(_BarsProvider([100.0 + i for i in range(40)]))
    assert await svc.get_regime("ES", "1h") == "trending_bull"


@pytest.mark.asyncio
async def test_service_unknown_without_market_data() -> None:
    svc = HMMService(None)
    assert await svc.get_regime("ES", "1h") == "unknown"


@pytest.mark.asyncio
async def test_service_never_raises_on_provider_error() -> None:
    class _Boom:
        async def get_bars(self, *a, **k):
            raise RuntimeError("bridge down")

    svc = HMMService(_Boom())
    assert await svc.get_regime("ES", "1h") == "unknown"


@pytest.mark.asyncio
async def test_service_unknown_on_short_history() -> None:
    svc = HMMService(_BarsProvider([100.0] * 5))
    assert await svc.get_regime("ES", "4h") == "unknown"
