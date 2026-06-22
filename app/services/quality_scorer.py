"""QualityScorer — Level-4 quality filters (Fase 5).

Opt-in and additive: if no filters are enabled in
config["filters"] (strategy_profile.pipeline_config_json["filters"]), the score
is 100 (pass-through, backward compatible with Fase 1).

Filters (each returns a 0..1 sub-score; the weighted average → 0-100):
  - volume_relative : current bar volume vs the prior 20-bar average.
  - atr_normalized  : recent volatility vs its own baseline (best near normal).
  - vwap_position   : entry price vs VWAP, signed by direction (institutional
                      confluence: longs above VWAP, shorts below).
  - time_of_day     : session-quality by local hour (avoid open/lunch/close).

These are NOT redundant with LuxAlgo (momentum/trend/strength). hmm_regime is
Fase 6 (ignored here even if present).
"""
from __future__ import annotations

import statistics
from datetime import timezone
from zoneinfo import ZoneInfo

from app.models.normalized_signal import NormalizedSignal

_NAMES = ("volume_relative", "atr_normalized", "vwap_position", "time_of_day")


def _f(b: dict, key: str) -> float:
    try:
        return float(b.get(key, 0) or 0)
    except (ValueError, TypeError):
        return 0.0


def _volume_relative(signal, bars, config) -> float:
    vols = [_f(b, "volume") for b in bars]
    if len(vols) < 21:
        return 0.5
    cur = vols[-1]
    avg = statistics.fmean(vols[-21:-1])  # 20 bars before the current
    if avg <= 0:
        return 0.5
    ratio = cur / avg
    return max(0.0, min(1.0, ratio - 0.5))  # 0.5x→0, 1.0x→0.5, ≥1.5x→1.0


def _atr_normalized(signal, bars, config) -> float:
    trs: list[float] = []
    for i in range(1, len(bars)):
        h, low, pc = _f(bars[i], "high"), _f(bars[i], "low"), _f(bars[i - 1], "close")
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    if len(trs) < 20:
        return 0.5
    short = statistics.fmean(trs[-5:])
    long = statistics.fmean(trs[-20:])
    if long <= 0:
        return 0.5
    ratio = short / long
    return max(0.0, 1.0 - min(abs(ratio - 1.0), 1.0))  # best near 1.0


def _vwap_position(signal, bars, config) -> float:
    num = den = 0.0
    for b in bars:
        tp = (_f(b, "high") + _f(b, "low") + _f(b, "close")) / 3.0
        v = _f(b, "volume")
        num += tp * v
        den += v
    if den <= 0:
        return 0.5
    vwap = num / den
    price = float(signal.price) if signal.price is not None else None
    if price is None or vwap <= 0:
        return 0.5
    diff = (price - vwap) / vwap
    signed = diff if signal.action == "buy" else -diff
    return max(0.0, min(1.0, 0.5 + signed * 100.0))  # ±0.5% saturates


def _time_of_day(signal, bars, config) -> float:
    ts = signal.signal_ts
    if ts is None:
        return 0.5
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    lt = ts.astimezone(ZoneInfo(config.get("timezone") or "America/New_York"))
    m = lt.hour * 60 + lt.minute
    if 570 <= m < 600:   # 09:30-10:00 open volatility
        return 0.3
    if 600 <= m < 690:   # 10:00-11:30 prime
        return 1.0
    if 690 <= m < 840:   # 11:30-14:00 lunch lull
        return 0.5
    if 840 <= m < 930:   # 14:00-15:30 afternoon
        return 0.8
    if 930 <= m < 945:   # 15:30-15:45 pre-close
        return 0.3
    return 0.5


_SUBSCORES = {
    "volume_relative": _volume_relative,
    "atr_normalized": _atr_normalized,
    "vwap_position": _vwap_position,
    "time_of_day": _time_of_day,
}


class QualityScorer:
    """Weighted Level-4 quality score. No filters enabled → 100 (pass-through)."""

    async def score(
        self, signal: NormalizedSignal, bars: list[dict], config: dict
    ) -> int:
        filters = config.get("filters") or {}
        active: list[tuple[str, float]] = []
        for name in _NAMES:
            f = filters.get(name)
            if isinstance(f, dict) and f.get("enabled"):
                try:
                    w = float(f.get("weight", 0) or 0)
                except (ValueError, TypeError):
                    w = 0.0
                if w > 0:
                    active.append((name, w))
        if not active:
            return 100

        total = sum(w for _, w in active)
        acc = 0.0
        for name, w in active:
            acc += w * _SUBSCORES[name](signal, bars, config)
        return int(round(acc / total * 100.0))
