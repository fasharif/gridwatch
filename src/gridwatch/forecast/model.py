"""Gradient-boosted direct forecaster for GB national carbon intensity.

One scikit-learn ``HistGradientBoostingRegressor`` covers every horizon from 30 minutes to
48 hours, with the horizon as a feature (a "direct" strategy, so errors do not compound as
they would when feeding predictions back in). The target is the change from the mean of the
last 24 hours rather than the raw level: the GB grid has decarbonised quickly, and tree
models cannot predict values outside the range they were trained on, so a level target
would be biased upwards on a falling series.

Two quantile models give a 10-90% interval. Raw quantile models are usually too narrow,
so the interval is widened by conformalised quantile regression (Romano et al., 2019): the
quantile models are trained without the last ``calibration_days`` of origins, and the
interval is stretched until 80% of those held-out targets fall inside it, separately for
the first and second forecast day.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from gridwatch.forecast.calendar import CalendarArrays
from gridwatch.forecast.features import (
    FEATURE_NAMES,
    MAX_HORIZON,
    PERIODS_PER_DAY,
    FloatArray,
    IntArray,
    TrailingStats,
    build_features,
)

CATEGORICAL = frozenset({"target_day_of_week"})


@dataclass(frozen=True)
class ModelConfig:
    train_days: int = 730
    origin_step: int = 12  # train on an origin every 6 hours
    max_iter: int = 300
    learning_rate: float = 0.05
    max_leaf_nodes: int = 31
    min_samples_leaf: int = 50
    l2_regularization: float = 1.0
    quantiles: tuple[float, float] = (0.1, 0.9)
    calibration_days: int = 56
    random_state: int = 42

    def __post_init__(self) -> None:
        if self.train_days < 14:
            raise ValueError("train_days must be at least 14")
        if self.origin_step < 1:
            raise ValueError("origin_step must be at least 1")
        low, high = self.quantiles
        if not 0 < low < high < 1:
            raise ValueError("quantiles must satisfy 0 < low < high < 1")
        if not 0 <= self.calibration_days < self.train_days:
            raise ValueError("calibration_days must be between 0 and train_days")

    @property
    def interval_coverage(self) -> float:
        low, high = self.quantiles
        return high - low


@dataclass
class Forecast:
    origin: int
    horizons: IntArray
    point: FloatArray
    lower: FloatArray
    upper: FloatArray


class NotEnoughHistoryError(ValueError):
    """Too few observed values before the cutoff to train a model."""


@dataclass
class IntensityForecaster:
    config: ModelConfig = field(default_factory=ModelConfig)
    horizons: IntArray = field(default_factory=lambda: np.arange(1, MAX_HORIZON + 1))
    _models: dict[str, HistGradientBoostingRegressor] = field(default_factory=dict, init=False)
    _widening: dict[bool, float] = field(default_factory=dict, init=False)

    def _estimator(self, loss: str, quantile: float | None = None) -> HistGradientBoostingRegressor:
        categorical = [name in CATEGORICAL for name in FEATURE_NAMES]
        kwargs: dict[str, object] = {
            "loss": loss,
            "max_iter": self.config.max_iter,
            "learning_rate": self.config.learning_rate,
            "max_leaf_nodes": self.config.max_leaf_nodes,
            "min_samples_leaf": self.config.min_samples_leaf,
            "l2_regularization": self.config.l2_regularization,
            "categorical_features": categorical,
            "random_state": self.config.random_state,
        }
        if quantile is not None:
            kwargs["quantile"] = quantile
        return HistGradientBoostingRegressor(**kwargs)

    def training_origins(self, values: FloatArray, cutoff: int) -> IntArray:
        """Origins whose every target is at or before ``cutoff`` (the last known index)."""
        last = cutoff - int(self.horizons.max())
        first = max(2 * 7 * PERIODS_PER_DAY, cutoff - self.config.train_days * PERIODS_PER_DAY)
        if last <= first:
            raise NotEnoughHistoryError(
                f"need more than {first + int(self.horizons.max())} periods before the cutoff"
            )
        origins = np.arange(last, first - 1, -self.config.origin_step)[::-1]
        return origins[~np.isnan(values[origins])]

    def fit(
        self,
        values: FloatArray,
        calendar: CalendarArrays,
        cutoff: int,
        stats: TrailingStats | None = None,
    ) -> IntensityForecaster:
        """Train on values up to and including index ``cutoff``; later values are ignored."""
        known = values.copy()
        known[cutoff + 1 :] = np.nan
        stats = stats if stats is not None else TrailingStats.of(known)
        origins = self.training_origins(known, cutoff)
        features = build_features(known, calendar, origins, self.horizons, stats)
        targets = known[
            np.repeat(origins, self.horizons.size) + np.tile(self.horizons, origins.size)
        ]
        level = features[:, FEATURE_NAMES.index("mean_24h")]
        ok = ~(np.isnan(targets) | np.isnan(level))
        if ok.sum() < 1000:
            raise NotEnoughHistoryError(f"only {int(ok.sum())} usable training rows")
        x, y = features[ok], (targets - level)[ok]
        row_origin = np.repeat(origins, self.horizons.size)[ok]
        calibration_start = cutoff - self.config.calibration_days * PERIODS_PER_DAY
        fit_rows = row_origin + int(self.horizons.max()) <= calibration_start
        calibrate_rows = ~fit_rows
        if self.config.calibration_days == 0 or fit_rows.sum() < 1000 or calibrate_rows.sum() < 200:
            fit_rows = np.ones_like(fit_rows)
            calibrate_rows = np.zeros_like(fit_rows)
        low, high = self.config.quantiles
        self._models = {
            "point": self._estimator("squared_error").fit(x, y),
            "lower": self._estimator("quantile", low).fit(x[fit_rows], y[fit_rows]),
            "upper": self._estimator("quantile", high).fit(x[fit_rows], y[fit_rows]),
        }
        self._widening = self._calibrate(x[calibrate_rows], y[calibrate_rows])
        return self

    def _calibrate(self, x: FloatArray, y: FloatArray) -> dict[bool, float]:
        """Conformal widening per forecast day (key: horizon beyond 24 hours)."""
        if y.size == 0:
            return {False: 0.0, True: 0.0}
        lower = self._models["lower"].predict(x)
        upper = self._models["upper"].predict(x)
        scores = np.maximum(lower - y, y - upper)
        second_day = x[:, FEATURE_NAMES.index("horizon")] > PERIODS_PER_DAY
        widening: dict[bool, float] = {}
        for day in (False, True):
            part = scores[second_day == day]
            if part.size == 0:
                widening[day] = 0.0
                continue
            level = min(1.0, np.ceil((part.size + 1) * self.config.interval_coverage) / part.size)
            widening[day] = float(np.quantile(part, level))
        return widening

    def predict(
        self,
        values: FloatArray,
        calendar: CalendarArrays,
        origin: int,
        stats: TrailingStats | None = None,
    ) -> Forecast:
        """Forecast every horizon from ``origin`` using values up to ``origin`` only."""
        if not self._models:
            raise RuntimeError("call fit() before predict()")
        known = values.copy()
        known[origin + 1 :] = np.nan
        stats = stats if stats is not None else TrailingStats.of(known)
        features = build_features(known, calendar, np.array([origin]), self.horizons, stats)
        level = features[:, FEATURE_NAMES.index("mean_24h")]
        point = self._models["point"].predict(features) + level
        second_day = self.horizons > PERIODS_PER_DAY
        widen = np.where(second_day, self._widening.get(True, 0.0), self._widening.get(False, 0.0))
        lower = self._models["lower"].predict(features) + level - widen
        upper = self._models["upper"].predict(features) + level + widen
        # Quantile models are fitted separately and can cross; keep the interval ordered.
        lower, upper = np.minimum(lower, point), np.maximum(upper, point)
        return Forecast(origin, self.horizons.copy(), point, lower, upper)
