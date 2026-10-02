"""How sure are the headline results? Block-bootstrap intervals and the Diebold-Mariano test.

Daily results are autocorrelated: a windy week is windy on every day in it. Resampling single
days would treat those days as independent and give intervals that are too narrow, so the
bootstrap resamples blocks of consecutive days (a moving-block bootstrap), and the
Diebold-Mariano test uses a Newey-West long-run variance for the same reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
IndexArray = NDArray[np.int64]

BOOTSTRAP_SEED = 20260925  # fixed, so the report is reproducible


@dataclass(frozen=True)
class Interval:
    estimate: float
    lower: float
    upper: float
    level: float
    resamples: int
    block_length: int


@dataclass(frozen=True)
class DieboldMariano:
    """Result of a test of equal accuracy; a negative statistic favours the first forecast."""

    mean_difference: float
    statistic: float
    p_value: float
    n: int
    lags: int


def _vector(values: FloatArray, name: str) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or array.size == 0:
        raise ValueError(f"{name} must be a non-empty one-dimensional array")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains missing or infinite values")
    return array


def moving_block_indices(
    n: int, block_length: int, resamples: int, rng: np.random.Generator
) -> IndexArray:
    """Row indices for ``resamples`` moving-block bootstrap samples of a length-n series.

    Each sample joins randomly chosen runs of ``block_length`` consecutive indices and is cut
    back to n, so the dependence inside a block is preserved.
    """
    if n < 1:
        raise ValueError("n must be positive")
    if not 1 <= block_length <= n:
        raise ValueError("block_length must be between 1 and n")
    if resamples < 1:
        raise ValueError("resamples must be positive")
    blocks = math.ceil(n / block_length)
    starts = rng.integers(0, n - block_length + 1, size=(resamples, blocks))
    runs = starts[:, :, np.newaxis] + np.arange(block_length)[np.newaxis, np.newaxis, :]
    return runs.reshape(resamples, blocks * block_length)[:, :n].astype(np.int64)


def saving_interval(
    rule: FloatArray,
    baseline: FloatArray,
    block_length: int = 7,
    resamples: int = 2000,
    level: float = 0.95,
    seed: int = BOOTSTRAP_SEED,
) -> Interval:
    """Percentage saving of ``rule`` against ``baseline`` with a bootstrap interval.

    Both arrays hold one value per day (the mean intensity a job saw under each rule). The
    saving is ``100 * (1 - mean(rule) / mean(baseline))``, as in rpt_batch_job_savings. Days
    are resampled in pairs, in blocks of ``block_length`` consecutive days, and the interval
    is the percentile interval of the resampled savings.
    """
    rule = _vector(rule, "rule")
    baseline = _vector(baseline, "baseline")
    if rule.size != baseline.size:
        raise ValueError("rule and baseline need one value per day each")
    if not 0 < level < 1:
        raise ValueError("level must be between 0 and 1")
    indices = moving_block_indices(rule.size, block_length, resamples, np.random.default_rng(seed))
    boot = 100.0 * (1.0 - rule[indices].mean(axis=1) / baseline[indices].mean(axis=1))
    tail = (1.0 - level) / 2.0
    lower, upper = np.quantile(boot, [tail, 1.0 - tail])
    return Interval(
        estimate=100.0 * (1.0 - rule.mean() / baseline.mean()),
        lower=float(lower),
        upper=float(upper),
        level=level,
        resamples=resamples,
        block_length=block_length,
    )


def newey_west_variance(values: FloatArray, lags: int) -> float:
    """Long-run variance of a series with Bartlett weights (Newey and West, 1987)."""
    series = _vector(values, "values")
    if not 0 <= lags < series.size:
        raise ValueError("lags must be between 0 and the series length minus one")
    centred = series - series.mean()
    n = centred.size
    total = float(centred @ centred) / n
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1.0)
        total += 2.0 * weight * float(centred[lag:] @ centred[:-lag]) / n
    return total


def diebold_mariano(loss_a: FloatArray, loss_b: FloatArray, lags: int = 7) -> DieboldMariano:
    """Diebold-Mariano test that two forecasts are equally accurate.

    ``loss_a`` and ``loss_b`` hold one loss per period (here the mean absolute error of each
    forecast day). The statistic is the mean loss difference over its Newey-West standard
    error, compared with the standard normal distribution (two-sided). With a year of daily
    values the normal approximation is adequate.
    """
    a = _vector(loss_a, "loss_a")
    b = _vector(loss_b, "loss_b")
    if a.size != b.size:
        raise ValueError("loss_a and loss_b need one value per period each")
    if a.size < lags + 2:
        raise ValueError(f"need at least {lags + 2} periods for {lags} lags")
    difference = a - b
    variance = newey_west_variance(difference, lags)
    if variance <= 0.0:
        raise ValueError("the loss differences do not vary, so the test is undefined")
    statistic = float(difference.mean()) / math.sqrt(variance / difference.size)
    return DieboldMariano(
        mean_difference=float(difference.mean()),
        statistic=statistic,
        p_value=math.erfc(abs(statistic) / math.sqrt(2.0)),
        n=int(difference.size),
        lags=lags,
    )
