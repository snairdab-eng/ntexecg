"""SLTPCalculator — mandatory SL calculation for entry signals.

CRITICAL: sl_price is NEVER None when passed=True.
If ATR is unavailable → passed=False with reason.
TP is managed by LuxAlgo Builtin-Exits (tp_atr_multiplier prepared but inactive).
"""
from __future__ import annotations

from app.models.normalized_signal import NormalizedSignal


class SLTPCalculator:
    """Calculate SL/TP for entry signals. SL is mandatory."""

    async def calculate(
        self,
        signal: NormalizedSignal,
        atr: float | None,
        entry_price: float,
        config: dict,
    ) -> dict:
        """Calculate SL and TP prices.

        CRITICAL INVARIANT:
          If returned with passed=True, sl_price is NEVER None.
          If ATR is unavailable → passed=False, reason="atr_calculation_failed"

        Args:
            signal: Normalized signal (has action, sentiment)
            atr: ATR value or None if unavailable
            entry_price: Entry price from signal.price
            config: Merged config dict (contains sl_atr_multiplier, tp_atr_multiplier)

        Returns:
            {
              "passed": bool,
              "reason": str | None,
              "sl_price": float | None,
              "tp_price": float | None,
              "atr_value": float | None,
            }
        """
        if atr is None or atr <= 0:
            return {
                "passed": False,
                "reason": "atr_calculation_failed",
                "sl_price": None,
                "tp_price": None,
                "atr_value": None,
            }

        sl_multiplier = config.get("sl_atr_multiplier", 1.5)
        tp_multiplier = config.get("tp_atr_multiplier")

        # Determine direction from signal
        is_long = signal.action == "buy"

        # SL calculation (mandatory)
        if is_long:
            sl_price = entry_price - (atr * sl_multiplier)
        else:
            sl_price = entry_price + (atr * sl_multiplier)

        # TP calculation (prepared but inactive by default)
        tp_price = None
        if tp_multiplier:
            if is_long:
                tp_price = entry_price + (atr * tp_multiplier)
            else:
                tp_price = entry_price - (atr * tp_multiplier)

        return {
            "passed": True,
            "reason": None,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "atr_value": atr,
        }
