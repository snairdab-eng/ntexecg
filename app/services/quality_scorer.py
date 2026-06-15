"""QualityScorer — Phase 1 stub returns 100 always.

Phase 5 will implement:
  - Volume relative (weight 30pts)
  - ATR normalized (weight 25pts)
  - VWAP position (weight 25pts)
  - Time of day quality (weight 20pts)

Phase 6:
  - HMM market regime detection
"""
from __future__ import annotations

from app.models.normalized_signal import NormalizedSignal


class QualityScorer:
    """Phase 1: always returns 100. Structure ready for Phase 5."""

    async def score(
        self,
        signal: NormalizedSignal,
        bars: list[dict],
        config: dict,
    ) -> int:
        """Return a quality score 0-100.

        Phase 1 (MVP): always returns 100.
        Callers still implement score_minimum logic for future extensibility.

        Args:
            signal: The normalized signal
            bars: OHLCV bars from market data
            config: Merged config dict

        Returns:
            Score 0-100. Phase 1: always 100.
        """
        # Phase 1 stub: always pass
        return 100
