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


def classify_regime_detail(
    closes: list[float],
    lookback: int = _DEFAULT_LOOKBACK,
    trend_threshold: float = _DEFAULT_TREND_THRESHOLD,
) -> dict:
    """Classify + EXPOSE the evidence (Kaufman ER, bars used) for the UI.

    Returns {regime, er, n_bars, n_available, sufficient, min_bars}:
      - regime: same label as classify_regime (this is its single source).
      - er: efficiency ratio (rounded 2dp for display), None if insufficient.
      - n_bars: bars in the ER window; n_available: bars fetched.
      - sufficient: False when n_available < min_bars → regime 'unknown'.

    Pure and deterministic. The regime DECISION uses the unrounded ER so it is
    byte-for-byte identical to the previous classify_regime (no gate change).
    """
    n_available = len(closes) if closes else 0
    base = {"n_available": n_available, "min_bars": _MIN_BARS}
    if closes is None or n_available < _MIN_BARS:
        return {"regime": "unknown", "er": None, "n_bars": n_available,
                "sufficient": False, **base}
    n = min(lookback, n_available - 1)
    window = closes[-(n + 1):]
    net = window[-1] - window[0]
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path <= 0:
        return {"regime": "ranging", "er": 0.0, "n_bars": n,
                "sufficient": True, **base}
    er = abs(net) / path                          # unrounded → the decision
    if er < trend_threshold:
        regime = "ranging"
    else:
        regime = "trending_bull" if net > 0 else "trending_bear"
    return {"regime": regime, "er": round(er, 2), "n_bars": n,
            "sufficient": True, **base}


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

    Pure and deterministic — no ML dependency, fully unit-testable. Thin wrapper
    over classify_regime_detail (single source; the label is identical)."""
    return classify_regime_detail(closes, lookback, trend_threshold)["regime"]


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

    async def get_regime_detail(
        self,
        symbol: str,
        timeframe: str = "1h",
        lookback: int = _DEFAULT_LOOKBACK,
        trend_threshold: float = _DEFAULT_TREND_THRESHOLD,
    ) -> dict:
        """Regime + evidence (ER, bars) for the UI — the deterministic 1h
        baseline (Kaufman ER), the same engine the gate uses in paper/demo (no
        trained model). Read-only, advisory: never raises; no market data or no
        bars → 'unknown' insufficient. Does NOT touch the pipeline."""
        base = {"regime": "unknown", "er": None, "n_bars": 0,
                "n_available": 0, "sufficient": False, "min_bars": _MIN_BARS,
                "timeframe": timeframe}
        if self._market_data is None:
            return base
        try:
            bars = await self._market_data.get_bars(symbol, timeframe, limit=250)
        except Exception:
            return base
        closes: list[float] = []
        for b in bars or []:
            try:
                closes.append(float(b.get("close", 0) or 0))
            except (ValueError, TypeError):
                continue
        detail = classify_regime_detail(closes, lookback, trend_threshold)
        detail["timeframe"] = timeframe
        return detail
