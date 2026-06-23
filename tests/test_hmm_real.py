"""HMM trainer + get_regime integration (Fase 6).

The training/predict tests require hmmlearn (importorskip → run on NTDEV, skip
where the ML stack is absent). The fallback test needs no ML deps.
MODELS_DIR is isolated to a temp dir by the autouse conftest fixture.
"""
import math
import random

import pytest

from app.services.hmm_service import HMMService


def _three_regimes(n: int = 250, seed: int = 42) -> tuple[list[float], list[float]]:
    """Noisy (seeded → deterministic) up-trend, then ranging, then down-trend.

    Three distinct regimes so a 3-state HMM has data for every state (avoids
    unused states → NaN params). Real variance keeps the fit well-conditioned.
    """
    rng = random.Random(seed)
    price = 100.0
    closes: list[float] = []
    volumes: list[float] = []
    segments = (
        (n, 0.0010, 0.003, 1000),   # up-trend: positive drift, lower vol
        (n, 0.0000, 0.006, 1300),   # ranging: no drift, higher vol
        (n, -0.0010, 0.003, 1000),  # down-trend: negative drift, lower vol
    )
    for count, drift, vol, vbase in segments:
        for _ in range(count):
            price *= math.exp(rng.gauss(drift, vol))
            closes.append(price)
            volumes.append(float(vbase + rng.randint(0, 400)))
    return closes, volumes


def test_train_save_load_predict_roundtrip() -> None:
    pytest.importorskip("hmmlearn")
    from app.services import hmm_trainer

    closes, volumes = _three_regimes()
    obj = hmm_trainer.train_model(closes, volumes, n_states=3)
    assert obj is not None
    assert obj["n_states"] == 3
    assert set(obj["labels"].values()) <= {"trending_bull", "trending_bear", "ranging"}

    hmm_trainer.save_model(obj, "TESTSYM", "1h")
    loaded = hmm_trainer.load_model("TESTSYM", "1h")
    assert loaded is not None

    label = hmm_trainer.predict_regime(loaded, closes, volumes)
    assert label in ("trending_bull", "trending_bear", "ranging")


def test_train_model_insufficient_samples_returns_none() -> None:
    pytest.importorskip("hmmlearn")
    from app.services import hmm_trainer

    closes = [100.0 + i for i in range(50)]  # < _MIN_SAMPLES feature rows
    assert hmm_trainer.train_model(closes, [1000.0] * 50) is None


def test_load_model_none_when_absent() -> None:
    from app.services import hmm_trainer

    # MODELS_DIR is an empty temp dir (autouse fixture) → no model on disk.
    assert hmm_trainer.load_model("NOPE", "1h") is None


class _UptrendProvider:
    async def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> list[dict]:
        return [
            {"close": 100.0 + i, "volume": 1000} for i in range(60)
        ]


@pytest.mark.asyncio
async def test_get_regime_falls_back_to_baseline_without_model() -> None:
    # No trained model on disk → get_regime uses the deterministic baseline,
    # which classifies a steady uptrend as trending_bull.
    svc = HMMService(_UptrendProvider())
    assert await svc.get_regime("ES", "1h") == "trending_bull"
