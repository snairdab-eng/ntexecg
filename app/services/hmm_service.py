"""HMMService — Phase 6 stub returns "unknown" always.

Phase 6 will implement Hidden Markov Model market regime detection.
Detects bull, bear, sideways, or volatile market regimes.
"""
from __future__ import annotations


class HMMService:
    """Phase 6 stub: market regime detection."""

    async def get_regime(self, symbol: str) -> str:
        """Detect current market regime.

        Phase 6 will return: "bull", "bear", "sideways", "volatile"
        Phase 1-5: always returns "unknown"

        Args:
            symbol: Asset symbol (e.g., "MESU2025")

        Returns:
            Regime string. Phase 1: always "unknown".
        """
        return "unknown"
