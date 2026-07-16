"""Fixed-tick pricing + TradersPost JSON serialization (FIX-D2).

Two guarantees for every price that leaves for TradersPost:

  1. round_to_tick — order prices snap to the instrument tick (catalog tick_size),
     to the NEAREST multiple. Exact half-tick ties round UP, toward +inf — the
     documented boundary. A missing/invalid tick returns the price unchanged
     (fail-open: we never fabricate a tick we do not know from the catalog).

  2. dumps — floats render as FIXED-decimal, NEVER scientific notation. Python's
     json renders e.g. the 6J tick 5e-7 as "5e-07", which TradersPost misparses;
     6E (5e-5) and any deep-decimal FX price hit the same. For a payload WITHOUT
     scientific-notation floats the output is byte-identical to json.dumps, so ES /
     GC (and every well-scaled instrument) never regress — see tests.

Both are pure and dependency-free; the TradersPostClient serializes with dumps()
and the pricing paths (SLTPCalculator, PayloadBuilder.build_scaled) round with
round_to_tick().
"""
from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def round_to_tick(price: float | None, tick: float | None) -> float | None:
    """Snap `price` to the nearest multiple of `tick`.

    Exact half-tick ties round UP (toward +inf): `quantize(ROUND_HALF_UP)` on the
    tick count. `tick` None / non-positive / unparseable → `price` unchanged
    (fail-open — an unknown tick must not silently move the price)."""
    if price is None:
        return None
    if tick is None:
        return price
    try:
        t = Decimal(str(tick))
        d = Decimal(str(price))
    except (InvalidOperation, ValueError):
        return price
    if t <= 0:
        return price
    n = (d / t).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return float(n * t)


def to_fixed_str(value: float) -> str:
    """A float as a fixed-point decimal string, never scientific. Matches Python's
    float repr wherever repr has no exponent (so no byte regression), and expands
    the exponent form otherwise (5e-07 → '0.0000005')."""
    if not math.isfinite(value):
        return json.dumps(value)          # NaN / Infinity — defer to stdlib token
    return format(Decimal(str(value)), "f")   # str(float) = shortest round-trip


def dumps(obj) -> str:
    """json.dumps equivalent in which every float is a fixed-decimal number token.

    Reproduces json.dumps' default layout (", "/": " separators, ensure_ascii string
    escaping, insertion order) so payloads with no scientific-notation floats
    serialize byte-for-byte identically."""
    return _encode(obj)


def _encode(o) -> str:
    if o is True:
        return "true"
    if o is False:
        return "false"
    if o is None:
        return "null"
    if isinstance(o, float):
        return to_fixed_str(o)
    if isinstance(o, int):
        return str(o)
    if isinstance(o, str):
        return json.dumps(o)              # exact JSON string escaping
    if isinstance(o, dict):
        return "{" + ", ".join(
            f"{json.dumps(str(k))}: {_encode(v)}" for k, v in o.items()) + "}"
    if isinstance(o, (list, tuple)):
        return "[" + ", ".join(_encode(v) for v in o) + "]"
    raise TypeError(f"not JSON-serializable: {type(o).__name__}")
