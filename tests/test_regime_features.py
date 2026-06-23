"""Regime feature engineering + state labeling (pure, no ML deps)."""
from app.services.regime_features import (
    RANGING,
    TRENDING_BEAR,
    TRENDING_BULL,
    build_features,
    label_states,
)


def test_build_features_empty_or_too_short() -> None:
    assert build_features([]) == []
    assert build_features([100.0]) == []
    # fewer bars than the feature window (max(10,20)+1 = 21) → no rows
    assert build_features([100.0 + i for i in range(15)]) == []


def test_build_features_shape_and_alignment() -> None:
    closes = [100.0 + i * 0.1 for i in range(60)]
    volumes = [1000.0] * 60
    rows = build_features(closes, volumes, vol_window=10, vmean_window=20)
    assert len(rows) == 60 - (max(10, 20) + 1)  # 39
    assert all(len(r) == 3 for r in rows)
    # steady uptrend → final bar's log_return is positive
    assert rows[-1][0] > 0


def test_build_features_volume_ratio() -> None:
    closes = [100.0] * 40
    volumes = [1000.0] * 39 + [2000.0]  # last bar double the average
    rows = build_features(closes, volumes, vol_window=10, vmean_window=20)
    assert rows[-1][2] > 0  # volume_ratio positive when above its mean


def test_label_states_three() -> None:
    # state 0 highest return (bull), state 1 lowest (bear), state 2 middle (ranging)
    labels = label_states([[0.002, 0, 0], [-0.003, 0, 0], [0.00001, 0, 0]])
    assert labels[0] == TRENDING_BULL
    assert labels[1] == TRENDING_BEAR
    assert labels[2] == RANGING
    assert set(labels.values()) == {TRENDING_BULL, TRENDING_BEAR, RANGING}


def test_label_states_two_and_one_and_empty() -> None:
    two = label_states([[0.001, 0, 0], [-0.001, 0, 0]])
    assert two == {0: TRENDING_BULL, 1: TRENDING_BEAR}
    assert label_states([[0.0, 0, 0]]) == {0: RANGING}
    assert label_states([]) == {}
