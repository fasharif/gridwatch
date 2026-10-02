from __future__ import annotations

import math

import numpy as np
import pytest

from gridwatch.stats import (
    diebold_mariano,
    moving_block_indices,
    newey_west_variance,
    saving_interval,
)


def test_moving_blocks_are_runs_of_consecutive_days() -> None:
    indices = moving_block_indices(20, 7, 50, np.random.default_rng(1))
    assert indices.shape == (50, 20)
    assert indices.min() >= 0
    assert indices.max() <= 19
    for row in indices:
        for start in (0, 7, 14):
            block = row[start : start + 7]
            assert (np.diff(block) == 1).all()


@pytest.mark.parametrize(("n", "block", "resamples"), [(0, 1, 1), (5, 0, 1), (5, 6, 1), (5, 2, 0)])
def test_moving_blocks_reject_bad_sizes(n: int, block: int, resamples: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        moving_block_indices(n, block, resamples, np.random.default_rng(0))


def test_saving_interval_of_a_constant_ratio_is_exact() -> None:
    baseline = np.linspace(80.0, 160.0, 60)
    result = saving_interval(0.85 * baseline, baseline, resamples=200)
    assert result.estimate == pytest.approx(15.0)
    assert result.lower == pytest.approx(15.0)
    assert result.upper == pytest.approx(15.0)


def test_saving_interval_brackets_the_estimate_and_is_reproducible() -> None:
    rng = np.random.default_rng(3)
    baseline = 120.0 + rng.normal(0, 20, 365)
    rule = 0.85 * baseline + rng.normal(0, 10, 365)
    first = saving_interval(rule, baseline)
    second = saving_interval(rule, baseline)
    assert first == second
    assert first.lower < first.estimate < first.upper
    assert 10.0 < first.lower < first.upper < 20.0
    assert first.estimate == pytest.approx(100 * (1 - rule.mean() / baseline.mean()))


def test_saving_interval_validates_inputs() -> None:
    with pytest.raises(ValueError, match="one value per day"):
        saving_interval(np.ones(3), np.ones(4))
    with pytest.raises(ValueError, match="missing or infinite"):
        saving_interval(np.array([1.0, np.nan]), np.ones(2))
    with pytest.raises(ValueError, match="level"):
        saving_interval(np.ones(3), np.ones(3), level=1.0)


def test_newey_west_without_lags_is_the_variance() -> None:
    values = np.array([1.0, 4.0, 2.0, 8.0, 5.0])
    assert newey_west_variance(values, 0) == pytest.approx(np.var(values))


def test_newey_west_by_hand() -> None:
    values = np.array([1.0, 3.0, 2.0, 6.0])  # mean 3, centred -2, 0, -1, 3
    gamma0 = (4 + 0 + 1 + 9) / 4
    gamma1 = (0 * -2 + -1 * 0 + 3 * -1) / 4
    assert newey_west_variance(values, 1) == pytest.approx(gamma0 + 2 * 0.5 * gamma1)


def test_diebold_mariano_detects_a_consistently_better_forecast() -> None:
    rng = np.random.default_rng(5)
    baseline = 40.0 + rng.normal(0, 8, 365)
    better = baseline - 3.0 + rng.normal(0, 4, 365)
    result = diebold_mariano(better, baseline)
    assert result.mean_difference == pytest.approx(float(np.mean(better - baseline)))
    assert result.statistic < -5
    assert result.p_value < 1e-6
    assert result.n == 365


def test_diebold_mariano_statistic_matches_the_formula() -> None:
    a = np.array([3.0, 5.0, 4.0, 6.0, 2.0, 7.0, 5.0, 4.0])
    b = np.array([4.0, 4.0, 5.0, 5.0, 4.0, 6.0, 6.0, 3.0])
    result = diebold_mariano(a, b, lags=2)
    d = a - b
    expected = d.mean() / math.sqrt(newey_west_variance(d, 2) / d.size)
    assert result.statistic == pytest.approx(expected)
    assert result.p_value == pytest.approx(math.erfc(abs(expected) / math.sqrt(2)))


def test_diebold_mariano_on_equal_forecasts_is_undefined() -> None:
    with pytest.raises(ValueError, match="do not vary"):
        diebold_mariano(np.ones(30), np.ones(30))
    with pytest.raises(ValueError, match="at least 9 periods"):
        diebold_mariano(np.ones(5), np.zeros(5))
