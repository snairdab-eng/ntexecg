"""SLTPCalculator — mandatory SL calculation for entry signals.

CRITICAL: sl_price is NEVER None when passed=True.
Two SL modes (MR-5a, Directiva 4 del Motor de Riesgo):
  - backstop_points configured → fixed-price stop anchored to the signal
    (SL = signal ∓ points). Does NOT depend on ATR — replaces k×ATR for
    that strategy (the study showed tight ×ATR stops kill winners; the
    wide backstop IS the stop). Missing signal price → passed=False.
  - otherwise → k×ATR as always; ATR unavailable → passed=False.
TP is managed by LuxAlgo Builtin-Exits (tp_atr_multiplier prepared but inactive).
"""
from __future__ import annotations

from app.models.normalized_signal import NormalizedSignal


def backstop_config(config: dict) -> float | None:
    """backstop_points válido del config merged (número > 0) o None.
    bool se excluye explícitamente (True es int en Python)."""
    v = config.get("backstop_points")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return None
    return float(v)


class SLTPCalculator:
    """Calculate SL/TP for entry signals. SL is mandatory."""

    async def calculate(
        self,
        signal: NormalizedSignal,
        atr: float | None,
        entry_price: float | None,
        config: dict,
    ) -> dict:
        """Calculate SL and TP prices.

        CRITICAL INVARIANT:
          If returned with passed=True, sl_price is NEVER None.
          With backstop_points configured the SL is computable without ATR;
          the ONLY block is entry_price missing (never send without stop).
          Without backstop: ATR unavailable → passed=False,
          reason="atr_calculation_failed".
          entry_price missing/<=0 → passed=False, reason="entry_price_missing"
          (NX-05: an SL computed against 0 is a naked order in disguise).

        Args:
            signal: Normalized signal (has action, sentiment)
            atr: ATR value or None if unavailable
            entry_price: Entry price from signal.price
            config: Merged config dict (sl_atr_multiplier, tp_atr_multiplier,
                    backstop_points)

        Returns:
            {
              "passed": bool,
              "reason": str | None,
              "sl_price": float | None,
              "tp_price": float | None,
              "atr_value": float | None,
              "sl_mode": "backstop_fixed" | "atr" | None,
            }
        """
        backstop = backstop_config(config)

        if backstop is None and (atr is None or atr <= 0):
            return {
                "passed": False,
                "reason": "atr_calculation_failed",
                "sl_price": None,
                "tp_price": None,
                "atr_value": None,
                "sl_mode": None,
            }

        # NX-05 — sin precio de entrada válido no hay SL calculable: BLOCK.
        # Con backstop este es el ÚNICO bloqueo posible (fail-closed: el
        # stop de precio fijo siempre se puede calcular… desde un precio).
        if entry_price is None or entry_price <= 0:
            return {
                "passed": False,
                "reason": "entry_price_missing",
                "sl_price": None,
                "tp_price": None,
                "atr_value": atr,
                "sl_mode": None,
            }

        tp_multiplier = config.get("tp_atr_multiplier")

        # Determine direction from signal
        is_long = signal.action == "buy"

        # SL calculation (mandatory)
        if backstop is not None:
            # MR-5a — backstop: stop de PRECIO FIJO anclado a la señal.
            # Reemplaza el k×ATR para esta estrategia y no depende del ATR.
            sl_price = (entry_price - backstop if is_long
                        else entry_price + backstop)
            sl_mode = "backstop_fixed"
        else:
            sl_multiplier = config.get("sl_atr_multiplier", 1.5)
            sl_price = (entry_price - (atr * sl_multiplier) if is_long
                        else entry_price + (atr * sl_multiplier))
            sl_mode = "atr"

        # TP calculation (prepared but inactive by default; needs ATR)
        tp_price = None
        if tp_multiplier and atr and atr > 0:
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
            "sl_mode": sl_mode,
        }
