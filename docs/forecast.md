# Forecast method

gridwatch forecasts GB national carbon intensity for the next 48 hours, one value per
half-hour, and tests that forecast against simple baselines. Results are in
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
| Relative to the last value | each recent-level and same-half-hour value above, minus the last value |

## Model

A scikit-learn `HistGradientBoostingRegressor` (squared error, 300 iterations, learning rate
0.05, 31 leaves, at least 50 samples per leaf, L2 penalty 1.0).

- **Target.** The change from the last known value rather than the level itself. The grid
  decarbonised quickly, and a tree model cannot predict outside the range it was trained on,
  so a level target would lag a falling series. Anchored at the last value, "no change" is
  also the natural prediction for the first half-hours, where it is hard to beat.
- **Relative features.** A tree cannot subtract one input from another, so each level feature
  is also given as its difference from the last value.
- **Equal weight for every horizon.** The change grows with the horizon, so under squared
  error the long horizons would dominate training and the first hours would be fitted
  poorly. Each training row is therefore weighted by the inverse variance of its horizon's
  target.

**How the design was reached.** The first version (the model code before commit 7a39552)
predicted the change from the trailing 24-hour mean, with every row weighted equally, and
persistence was not among the baselines. When persistence was added, the first version turned
out several times worse than it in the first hours, on the test year and on the validation
period alike. On the validation period below, its first-hour MAE was 20.3 against 3.3 for
persistence; the current design's is 3.6. The current design was run on the validation period
before the test year was run again. Because the change was prompted by results for the test
year, that year is not an untouched hold-out for it; the order of work is recorded in
[decision 10](decisions.md#10-evaluate-against-baselines-and-the-api-and-record-the-order-of-work).

Training uses one origin every 6 hours over the previous 730 days. The training window was
compared on a validation period before the test year: 84 daily forecasts issued from 1 July
to 22 September 2025, using only data before 24 September 2025. The runs are in the
validation table of [generated/report.md](generated/report.md#validation-runs):

| Training window | Calibration | 0-1 h MAE | 0-24 h MAE | 24-48 h MAE | 10-90% coverage |
| --- | --- | ---: | ---: | ---: | ---: |
| 365 days | 56 days | 4.1 | 25.7 | 35.2 | 82.1% |
| 730 days (default) | 56 days | 3.6 | 23.7 | 33.2 | 82.6% |
| 730 days | off | 3.6 | 23.7 | 33.2 | 83.5% |
| 1,460 days | 56 days | 3.5 | 23.6 | 34.2 | 81.0% |

Persistence had a first-hour MAE of 3.3 on the same forecasts. 730 days gave the lowest
24-48 h MAE, the band the settings are chosen on; 1,460 days was marginally better for the
first day. 730 training days and 56 calibration days were already the first version's
defaults, so these runs confirmed them rather than chose them.

Reproduce each row with `uv run gridwatch backtest --until 2025-09-24 --test-days 84
--train-days 730` (change `--train-days`, or add `--calibration-days 0`), then
`uv run gridwatch report`. Each run writes files named after its cutoff and settings, for
example `backtest_summary_until_20250924_train730_cal56.json`, so no run overwrites another
and the main backtest is kept.

**Interval.** Two quantile models (10th and 90th percentile) give a range, and conformalised
quantile regression (Romano, Patterson and Candès, 2019) adjusts it: the quantile models are
trained without the last 56 days of origins, and the range is widened or narrowed until 80%
of those held-out targets fall inside it, separately for each 6-hour band of lead time,
because the error grows fastest in the first hours. With the first version the raw range was
too narrow; with the current one it is slightly too wide on the validation period (83.5%),
and calibration brings it to 82.6%. On the test year the calibrated range covered 81.1% of
actual values, but coverage at 24-48 hours ranged from 70.6% to 89.9% from month to month.
Calibration does not change the point forecast, which is why the two 730-day rows have the
same MAE.

## Why scikit-learn

| Option | Verdict |
| --- | --- |
| scikit-learn `HistGradientBoostingRegressor` | Chosen. The same histogram-based boosting algorithm as LightGBM, handles missing values natively, supports quantile loss, and ships wheels for every platform with no system libraries. |
| LightGBM | Similar accuracy expected, but its Linux wheels need the system `libgomp`, one more thing to install in slim containers. |
| statsforecast (ETS, MSTL, ARIMA) | Strong classical models for one series, but they cannot use calendar features such as bank holidays as flexibly, and they bring numba's compile step. Persistence and the seasonal naive baselines cover the classical reference point; a statsforecast model has not been backtested here. |

## Backtest design

`gridwatch backtest` issues one forecast per day at 00:00 UTC over the last 365 days whose
48 hours of targets are all known. `--last-origin` fixes the last issue time instead, so a
later run tests the same days; the published run used `--last-origin 2026-09-23`. The model
is retrained every 28 days using only data before the first origin of each block, so no
forecast sees its own targets.

It is compared with:

- **Persistence:** the last known value, repeated for every horizon. It is hard to beat for
  the first hours, because intensity changes little from one half-hour to the next, and poor
  a day ahead, because it ignores the daily cycle.
- **Same half-hour, last known day:** the value at the same half-hour on the most recent day
  fully known at the origin. For a midnight origin that is yesterday, for both forecast days.
- **Same half-hour, last week:** the value seven days before the target.
- **The API's retained forecast:** the forecast value the API keeps for each past half-hour.
  It was issued shortly before the half-hour, so it is a much easier task, and it is shown as
  a reference rather than a competitor.

All methods are scored on the same half-hours (those where every method has a value), by
horizon band (0-24 h, 24-48 h, 0-48 h) and by finer bands of lead time (0-1, 1-4, 4-12,
12-24, 24-36 and 36-48 hours), with MAE, RMSE, MAPE (skipping zero actuals) and bias. The
backtest also reports interval coverage overall, by lead time and by month, MAE by target
month, and the share of days on which the model beat each baseline, so the months where it
loses are visible. If the value at an origin is missing, neither the model nor persistence
can forecast from it, so that origin drops out for every method.

**Is the difference real?** `gridwatch report` runs a Diebold-Mariano test of equal accuracy
between the model and each naive baseline, per horizon band. The loss is each forecast day's
MAE, so there is one value per origin and the targets of consecutive origins do not overlap
within a band. Errors on neighbouring days are still correlated (weather comes in spells), so
the variance is a Newey-West estimate with 7 lags, and the p-value is two-sided from the
normal distribution, which is adequate for a year of daily values. The test is in
`src/gridwatch/stats.py`, with unit tests against hand-computed values.

## Limits

- No weather, demand or generation forecasts are used. They drive most of the day-ahead
  uncertainty, and adding them is the obvious next step.
- In the first four hours the model is slightly worse than persistence. Blending the two for
  short horizons, with weights chosen on validation data, has not been tried.
- The model's design was changed after the test-year results had been seen (see
  [Model](#model)).
- One origin time (00:00 UTC) is tested; the production forecast is issued whenever the
  pipeline runs.
- Scores refer to the API's estimate of actual intensity, which is itself modelled.
