"""HMMService — market-regime detection (Fase 6).

Current implementation is a **deterministic baseline** (Kaufman Efficiency
Ratio + direction) that classifies the regime from higher-timeframe bars. It
replaces the Fase 1 "unknown" stub and exposes the SAME interface
(`get_regime`) that the trained HMM (hmmlearn) will use in the next increment,
so swapping the engine later requires no changes in callers.

Regimes (contract 05 §Fase 6):
  - "trending_bull"  : efficient, directional up
  - "trending_bear"  : efficient, directional down
  - "ranging"        : choppy / low efficiency
  - "unknown"        : not enough bars to decide (fail-open: never blocks)

Timeframe: regime is a higher-level state — read on a slower timeframe than the
entry (default 1h; 4h also supported), NEVER 5m (too noisy).
"""
from __future__ import annotations

from app.services.market_data_service import MarketDataService

# Bars needed before we attempt a classification. Below this → "unknown".
_MIN_BARS = 20
# Lookback window (in bars) used for the efficiency ratio.
_DEFAULT_LOOKBACK = 30
# Efficiency ratio at/above which the move is considered a trend.
_DEFAULT_TREND_THRESHOLD = 0.30


def classify_regime(
    closes: list[float],
    lookback: int = _DEFAULT_LOOKBACK,
    trend_threshold: float = _DEFAULT_TREND_THRESHOLD,
) -> str:
    """Classify a regime from a list of close prices (oldest → newest).

    Uses the Kaufman Efficiency Ratio (ER):
        ER = |close[-1] - close[-N]| / Σ|close[i] - close[i-1]|   (last N bars)
    ER → 1 means a clean directional move (trend); ER → 0 means choppy/ranging.
    Direction is the sign of the net move over the window.

    Pure and deterministic — no ML dependency, fully unit-testable.
    """
    if closes is None or len(closes) < _MIN_BARS:
        return "unknown"
    n = min(lookback, len(closes) - 1)
    window = closes[-(n + 1):]
    net = window[-1] - window[0]
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path <= 0:
        return "ranging"
    er = abs(net) / path
    if er < trend_threshold:
        return "ranging"
    return "trending_bull" if net > 0 else "trending_bear"


class HMMService:
    """Market-regime detection. Baseline engine now; HMM (hmmlearn) later."""

    def __init__(self, market_data: MarketDataService | None = None) -> None:
        self._market_data = market_data

    async def get_regime(
        self,
        symbol: str,
        timeframe: str = "1h",
        lookback: int = _DEFAULT_LOOKBACK,
        trend_threshold: float = _DEFAULT_TREND_THRESHOLD,
    ) -> str:
        """Return the current regime for ``symbol`` on ``timeframe``.

        Order: (1) trained HMM model from MODELS_DIR if present, else
        (2) the deterministic baseline classifier. Reads bars from the injected
        MarketDataService (same provider the pipeline uses). With no market data
        wired, returns "unknown" (fail-open). Never raises — regime is advisory.
        """
        if self._market_data is None:
            return "unknown"
        try:
            bars = await self._market_data.get_bars(symbol, timeframe, limit=250)
        except Exception:
            return "unknown"
        closes: list[float] = []
        volumes: list[float] = []
        for b in bars or []:
            try:
                c = float(b.get("close", 0) or 0)
                v = float(b.get("volume", 0) or 0)
            except (ValueError, TypeError):
                continue
            closes.append(c)
            volumes.append(v)

        # 1) trained HMM model (if one exists for this symbol/timeframe)
        try:
            from app.services import hmm_trainer

            model_obj = hmm_trainer.load_model(symbol, timeframe)
            if model_obj is not None:
                label = hmm_trainer.predict_regime(model_obj, closes, volumes)
                if label and label != "unknown":
                    return label
        except Exception:
            pass  # fall through to the baseline

        # 2) deterministic baseline (always available, no ML dependency)
        return classify_regime(closes, lookback, trend_threshold)
