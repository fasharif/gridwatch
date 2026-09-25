# Forecast method

gridwatch forecasts GB national carbon intensity for the next 48 hours, one value per
half-hour, and tests that forecast honestly against simple baselines. Results are in
[findings.md](findings.md#forecasting-gb-intensity-24-to-48-hours-ahead).

## Problem set-up

- **Origin.** A forecast is issued at an origin: the last half-hour whose actual value is
  known. Horizons run from 1 to 96 half-hours after it (30 minutes to 48 hours).
- **Target.** The actual intensity in gCO2/kWh from `marts.fct_national_intensity`, after
  implausible upstream values have been removed (see [data.md](data.md)).
- **Strategy.** One direct model for all horizons, with the horizon as an input. A recursive
  model would feed its own predictions back in and compound its errors over 96 steps.

## Features

Every feature uses values at or before the origin, plus calendar facts about the target
half-hour, which are known in advance. `tests/test_features.py` checks this by overwriting
every value after the origin and asserting that the features do not change.

| Group | Features |
| --- | --- |
| Horizon | half-hours ahead |
| Target calendar (UK local time) | half-hour of day, day of week, England and Wales bank holiday, day of year (sine and cosine) |
| Recent level | last value, the value before it, mean, standard deviation, minimum and maximum over the last 24 hours, mean over the last 7 days, change over the last 24 hours |
| Same half-hour | on the most recent fully known day, mean over the 7 most recent known days, one week and two weeks before the target |

## Model

A scikit-learn `HistGradientBoostingRegressor` (squared error, 300 iterations, learning rate
0.05, 31 leaves, at least 50 samples per leaf, L2 penalty 1.0). The target is the change from
the trailing 24-hour mean rather than the level itself: the grid decarbonised quickly, and a
tree model cannot predict outside the range it was trained on, so a level target would lag a
falling series.

Training uses one origin every 6 hours over the previous 730 days. The length was chosen on
a validation period before the test year: 84 daily forecasts issued from 1 July to
22 September 2025, using only data before 24 September 2025.

| Training window | 24-48 h MAE | 0-24 h MAE |
| --- | ---: | ---: |
| 365 days | 36.7 | 29.6 |
| 730 days (default) | 33.7 | 27.2 |
| 1,460 days | 34.2 | 26.9 |

Reproduce with `uv run gridwatch backtest --until 2025-09-24 --test-days 84 --train-days 730`
(change `--train-days`); outputs get an `_until_20250924` suffix so the main backtest is kept.

**Interval.** Two quantile models (10th and 90th percentile) give a range. Raw quantile
models were too narrow: on the validation period the uncalibrated range covered 71.4% of
actuals instead of 80% (`--calibration-days 0`). gridwatch therefore uses conformalised
quantile regression (Romano, Patterson and Candès, 2019): the quantile models are trained
without the last 56 days of origins, and the range is widened until 80% of those held-out
targets fall inside it, separately for the first and second forecast day. With calibration
the validation coverage was 82.1%, and on the test year 79.5%.

## Why scikit-learn

| Option | Verdict |
| --- | --- |
| scikit-learn `HistGradientBoostingRegressor` | Chosen. The same histogram-based boosting algorithm as LightGBM, handles missing values natively, supports quantile loss, and ships wheels for every platform with no system libraries. |
| LightGBM | Similar accuracy expected, but its Linux wheels need the system `libgomp`, one more thing to install in slim containers. |
| statsforecast (ETS, MSTL, ARIMA) | Strong classical models for one series, but they cannot use calendar features such as bank holidays as flexibly, and they bring numba's compile step. The seasonal naive baselines already cover the classical reference point. |

## Backtest design

`gridwatch backtest` issues one forecast per day at 00:00 UTC over the last 365 days whose
48 hours of targets are all known. The model is retrained every 28 days using only data
before the first origin of each block, so no forecast sees its own targets.

It is compared with:

- **Same half-hour, last known day:** the value at the same half-hour on the most recent day
  fully known at the origin. For a midnight origin that is yesterday, for both forecast days.
- **Same half-hour, last week:** the value seven days before the target.
- **The API's retained forecast:** the forecast value the API keeps for each past half-hour.
  It was issued shortly before the half-hour, so it is a much easier task, and it is shown as
  a reference rather than a competitor.

All methods are scored on the same half-hours (those where every method has a value), by
horizon band (0-24 h, 24-48 h, 0-48 h), with MAE, RMSE, MAPE (skipping zero actuals) and
bias. The backtest also reports interval coverage, MAE by target month and the share of days
on which the model beat each baseline, so the months where it loses are visible.

## Limits

- No weather, demand or generation forecasts are used. They drive most of the day-ahead
  uncertainty, and adding them is the obvious next step.
- One origin time (00:00 UTC) is tested; the production forecast is issued whenever the
  pipeline runs.
- Scores refer to the API's estimate of actual intensity, which is itself modelled.
