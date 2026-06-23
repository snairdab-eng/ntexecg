"""Regime feature engineering + HMM state labeling (Fase 6).

Pure Python (no numpy / hmmlearn) so it is fully unit-testable. The trainer
converts the feature rows to a numpy array at the hmmlearn boundary.

Features per bar (Gaussian-HMM friendly, contract 05 §Fase 6):
  - log_return  : log(close_t / close_{t-1})
  - volatility  : population std-dev of the last `vol_window` log returns
  - volume_ratio: volume_t / mean(volume over `vmean_window`) - 1
"""
from __future__ import annotations

import math
import statistics

FEATURE_NAMES = ("log_return", "volatility", "volume_ratio")

# Hidden-state labels (3-state model).
TRENDING_BULL = "trending_bull"
TRENDING_BEAR = "trending_bear"
RANGING = "ranging"


def build_features(
    closes: list[float],
    volumes: list[float] | None = None,
    vol_window: int = 10,
    vmean_window: int = 20,
) -> list[list[float]]:
    """Build feature rows aligned to the END of the series.

    The last row corresponds to closes[-1] (used for current-state inference).
    Returns [] when there are not enough bars for one full feature window.
    """
    n = len(closes)
    if volumes is None:
        volumes = [0.0] * n
    if n < 2:
        return []

    # Log returns (rets[i] uses closes[i-1], closes[i]).
    rets = [0.0] * n
    for i in range(1, n):
        c0, c1 = closes[i - 1], closes[i]
        if c0 and c1 and c0 > 0 and c1 > 0:
            rets[i] = math.log(c1 / c0)

    start = max(vol_window, vmean_window) + 1
    rows: list[list[float]] = []
    for i in range(start, n):
        window_rets = rets[i - vol_window + 1: i + 1]
        vol = statistics.pstdev(window_rets) if len(window_rets) > 1 else 0.0
        window_vol = volumes[i - vmean_window + 1: i + 1]
        vmean = statistics.fmean(window_vol) if window_vol else 0.0
        vratio = (volumes[i] / vmean - 1.0) if vmean > 0 else 0.0
        rows.append([rets[i], vol, vratio])
    return rows


def label_states(means: list[list[float]]) -> dict[int, str]:
    """Map HMM hidden-state indices to regime labels by mean log-return.

    `means[s][0]` is the mean log_return of state s. Highest → trending_bull,
    lowest → trending_bear, the rest → ranging. Robust to 1, 2 or 3+ states.
    """
    if not means:
        return {}
    order = sorted(range(len(means)), key=lambda s: means[s][0])  # ascending return
    if len(order) == 1:
        return {order[0]: RANGING}
    labels = {order[0]: TRENDING_BEAR, order[-1]: TRENDING_BULL}
    for s in order[1:-1]:
        labels[s] = RANGING
    return labels
