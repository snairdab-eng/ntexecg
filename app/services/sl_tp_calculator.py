"""SLTPCalculator — mandatory SL calculation for entry signals.

CRITICAL: sl_price is NEVER None when passed=True.
Two SL modes (MR-5a, Directiva 4 del Motor de Riesgo):
  - backstop_points configured → fixed-price stop anchored to the signal
    (SL = signal ∓ points). Does NOT depend on ATR — replaces k×ATR for
    that strategy (the study showed tight ×ATR stops kill winners; the
    wide backstop IS the stop). Missing signal price → passed=False.
  - otherwise → k×ATR as always; ATR unavailable → passed=False.
TP (MR-5b): la salida real es de LuxAlgo — el TP NOMINAL por lado
(tp_nominal_long/short, ×ATR sobre el p99 del cierre) casi nunca dispara y
solo satisface el bracket de TradersPost; sin ATR cae al ancho del backstop.
Legacy tp_atr_multiplier se mantiene (inactivo por default).
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


def tp_nominal_config(config: dict, is_long: bool) -> float | None:
    """Multiplicador ×ATR del TP NOMINAL del lado (MR-5b) o None.

    Por lado porque el estudio es asimétrico (largo alto ~11.5×, corto
    reducido e inestable → afinable en config, no hardcodeado). Misma
    validación que el backstop (número > 0, bool excluido)."""
    v = config.get("tp_nominal_long" if is_long else "tp_nominal_short")
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
          If returned with passed=True, sl_price is NEVER None — and the
          whole bracket is VALID: prices > 0 and on the correct side of the
          signal (long: sl < entry < tp; short: tp < entry < sl). Any
          computed bracket that violates this → passed=False,
          reason="bracket_price_invalid" (P0 guard, Auditoría 2026-07-06).
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
              "tp_mode": "nominal_atr" | "nominal_backstop_width" |
                         "legacy_atr" | None,
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
                "tp_mode": None,
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
                "tp_mode": None,
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

        # TP calculation. Prioridad: TP NOMINAL del lado (MR-5b) > legacy
        # tp_atr_multiplier > None. El nominal se queda en ×ATR (el estudio
        # midió el cierre de las ganadoras en ×ATR; un TP de puntos fijos se
        # estrecha relativo a la volatilidad justo en régimen volátil, donde
        # las ganadoras corren más — dispararía antes que LuxAlgo cuando no
        # debe). Fail-closed sin ATR: cae al ANCHO DEL BACKSTOP espejado al
        # lado favorable (más ancho que el nominal → sigue siendo nominal;
        # sin backstop este punto es inalcanzable — bloqueado arriba), para
        # que la entrada nunca quede sin el bracket que TradersPost exige.
        tp_price = None
        tp_mode = None
        nominal = tp_nominal_config(config, is_long)
        if nominal is not None:
            if atr and atr > 0:
                tp_price = (entry_price + (atr * nominal) if is_long
                            else entry_price - (atr * nominal))
                tp_mode = "nominal_atr"
            elif backstop is not None:
                tp_price = (entry_price + backstop if is_long
                            else entry_price - backstop)
                tp_mode = "nominal_backstop_width"
        elif tp_multiplier and atr and atr > 0:
            tp_price = (entry_price + (atr * tp_multiplier) if is_long
                        else entry_price - (atr * tp_multiplier))
            tp_mode = "legacy_atr"

        # P0 — guarda FINAL fail-closed del bracket (Auditoría 2026-07-06,
        # P0-1): un backstop mal escalado (p. ej. los 90 pts de ES pegados
        # en 6E a 1.083) producía sl_price NEGATIVO con passed=True — una
        # orden desnuda disfrazada (clase NX-05). Sobre los precios YA
        # computados, en AMBOS modos (backstop y ATR): positivos y del lado
        # correcto de la señal, o BLOCK. Jamás enviar un bracket inválido.
        bracket_ok = (
            sl_price > 0
            and (sl_price < entry_price if is_long
                 else sl_price > entry_price)
            and (tp_price is None
                 or (tp_price > 0
                     and (tp_price > entry_price if is_long
                          else tp_price < entry_price)))
        )
        if not bracket_ok:
            return {
                "passed": False,
                "reason": "bracket_price_invalid",
                "sl_price": None,          # nunca filtrar un precio inválido
                "tp_price": None,
                "atr_value": atr,
                "sl_mode": None,
                "tp_mode": None,
            }

        return {
            "passed": True,
            "reason": None,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "atr_value": atr,
            "sl_mode": sl_mode,
            "tp_mode": tp_mode,
        }
