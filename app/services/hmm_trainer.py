"""HMM trainer + model store (Fase 6).

Trains one GaussianHMM per (symbol, timeframe) from ohlcv_bars features, labels
its hidden states, and persists it to MODELS_DIR with joblib. Inference loads the
model (cached by file mtime) and predicts the current regime.

hmmlearn / numpy / joblib are imported lazily so this module (and the app) import
fine where the ML stack is absent; callers fall back to the baseline classifier.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.core.config import settings
from app.services.regime_features import build_features, label_states

# Minimum feature rows required to attempt a fit (≈ a few months of 1h bars).
_MIN_SAMPLES = 200

# Module-level cache: (symbol, timeframe) -> (mtime, model_obj)
_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}


def model_path(symbol: str, timeframe: str) -> Path:
    return Path(settings.MODELS_DIR) / f"hmm_{symbol}_{timeframe}.joblib"


def train_model(
    closes: list[float],
    volumes: list[float],
    n_states: int | None = None,
) -> dict | None:
    """Fit a GaussianHMM and return a serializable model object, or None.

    Returns None when there are too few samples or the fit fails — the caller
    then keeps using the baseline classifier.
    """
    n_states = n_states or settings.HMM_N_STATES
    rows = build_features(closes, volumes)
    if len(rows) < _MIN_SAMPLES:
        logger.warning("hmm_train_skipped reason=insufficient_samples n={}", len(rows))
        return None
    try:
        import numpy as np
        from hmmlearn.hmm import GaussianHMM

        X = np.asarray(rows, dtype=float)
        # Standardize features — they live on very different scales (returns
        # ~1e-3 vs volume_ratio ~1e-1), which destabilizes the Gaussian fit.
        # The scaler is stored and re-applied at inference time.
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std < 1e-9] = 1.0
        Xs = (X - mean) / std

        model = GaussianHMM(
            n_components=n_states, covariance_type="diag",
            n_iter=200, random_state=42, tol=1e-3,
        )
        model.fit(Xs)

        # Reject a degenerate fit (e.g. an unused state → NaN params); the
        # caller then falls back to the baseline classifier.
        if not all(
            np.all(np.isfinite(p))
            for p in (model.startprob_, model.transmat_, model.means_, model.covars_)
        ):
            logger.warning("hmm_train_degenerate non_finite_params n={}", len(rows))
            return None

        labels = label_states(model.means_.tolist())
        return {
            "model": model,
            "labels": labels,
            "scaler_mean": mean.tolist(),
            "scaler_std": std.tolist(),
            "n_states": n_states,
            "n_samples": len(rows),
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "feature_version": 1,
        }
    except Exception as exc:
        logger.error("hmm_train_failed error={}", exc)
        return None


def save_model(obj: dict, symbol: str, timeframe: str) -> Path:
    import joblib

    path = model_path(symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(obj, path)
    _CACHE.pop((symbol, timeframe), None)  # invalidate cache
    logger.info("hmm_model_saved symbol={} tf={} path={}", symbol, timeframe, path)
    return path


def load_model(symbol: str, timeframe: str) -> dict | None:
    """Load a trained model, cached by file mtime. None if no model on disk."""
    path = model_path(symbol, timeframe)
    try:
        if not path.exists():
            return None
        mtime = os.path.getmtime(path)
    except OSError:
        return None

    key = (symbol, timeframe)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        import joblib

        obj = joblib.load(path)
    except Exception as exc:
        logger.error("hmm_model_load_failed symbol={} tf={} error={}", symbol, timeframe, exc)
        return None
    _CACHE[key] = (mtime, obj)
    return obj


def _training_bars_from_csv(symbol: str, timeframe: str
                            ) -> tuple[list[float], list[float]]:
    """(closes, volumes) oldest→newest desde el HOLC CSV master (CSV-only).

    Reemplaza la lectura de `ohlcv_bars` (jubilada): el CSV es la única fuente de
    historia. Sin CSV del símbolo/tf → ([], []) y el entrenamiento se salta
    limpio (no revienta el job)."""
    from scripts.lab_analyze import load_holc

    try:
        bars = load_holc(symbol, timeframe)
    except (FileNotFoundError, OSError, SystemExit):
        return [], []
    closes: list[float] = []
    volumes: list[float] = []
    for ts in sorted(bars):
        o, h, lo, c, v = bars[ts]
        closes.append(float(c))
        volumes.append(float(v or 0))
    return closes, volumes


async def train_symbol(db, symbol: str, timeframe: str | None = None) -> dict | None:
    """Read history from the HOLC CSV master (CSV-only), fit + save a model.

    `db` se conserva por compat de la firma (train_active_symbols / el job lo
    pasan) pero YA NO se usa: la historia sale del CSV, no de `ohlcv_bars`."""
    timeframe = timeframe or settings.HMM_REGIME_TIMEFRAME
    closes, volumes = _training_bars_from_csv(symbol, timeframe)
    obj = train_model(closes, volumes)
    if obj is not None:
        save_model(obj, symbol, timeframe)
    return obj


async def train_active_symbols(db, timeframe: str | None = None) -> dict[str, bool]:
    """Train a model for every active DATA symbol. Returns {symbol: trained?}."""
    from sqlalchemy import select
    from app.models.symbol_map import SymbolMap
    from app.services.symbol_mapper import SymbolMapper

    timeframe = timeframe or settings.HMM_REGIME_TIMEFRAME
    mapper = SymbolMapper()
    res = await db.execute(select(SymbolMap).where(SymbolMap.active.is_(True)))
    symbols: set[str] = set()
    for sm in res.scalars().all():
        ds = await mapper.resolve_market_data_symbol(db, sm.tv_symbol)
        if ds:
            symbols.add(ds)

    results: dict[str, bool] = {}
    for s in sorted(symbols):
        obj = await train_symbol(db, s, timeframe)
        results[s] = obj is not None
    return results


def predict_regime(model_obj: dict, closes: list[float], volumes: list[float]) -> str:
    """Predict the CURRENT regime label from the latest bars. 'unknown' if N/A."""
    rows = build_features(closes, volumes)
    if not rows:
        return "unknown"
    try:
        import numpy as np

        X = np.asarray(rows, dtype=float)
        mean = model_obj.get("scaler_mean")
        std = model_obj.get("scaler_std")
        if mean is not None and std is not None:
            X = (X - np.asarray(mean)) / np.asarray(std)
        states = model_obj["model"].predict(X)
        last_state = int(states[-1])
        return model_obj["labels"].get(last_state, "ranging")
    except Exception as exc:
        logger.warning("hmm_predict_failed error={}", exc)
        return "unknown"
