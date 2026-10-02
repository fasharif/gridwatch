"""Point forecast error metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from gridwatch.forecast.features import FloatArray


@dataclass(frozen=True)
class ErrorMetrics:
    n: int
    mae: float
    rmse: float
    mape: float
    bias: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def score(predicted: FloatArray, actual: FloatArray) -> ErrorMetrics:
    """MAE, RMSE, MAPE (%) and bias over pairs where both values exist.

    MAPE ignores pairs whose actual is zero or negative, because the percentage is
    undefined there; GB intensity is never zero in practice after cleaning.
    """
    predicted = np.asarray(predicted, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)
    if predicted.shape != actual.shape:
        raise ValueError("predicted and actual must have the same shape")
    ok = ~(np.isnan(predicted) | np.isnan(actual))
    if not ok.any():
        return ErrorMetrics(0, float("nan"), float("nan"), float("nan"), float("nan"))
    err = predicted[ok] - actual[ok]
    positive = actual[ok] > 0
    mape = (
        float(100.0 * np.mean(np.abs(err[positive]) / actual[ok][positive]))
        if positive.any()
        else float("nan")
    )
    return ErrorMetrics(
        n=int(ok.sum()),
        mae=float(np.mean(np.abs(err))),
        rmse=float(np.sqrt(np.mean(err**2))),
        mape=mape,
        bias=float(np.mean(err)),
    )
