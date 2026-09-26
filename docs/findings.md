# Findings

## In short

- **In Great Britain, when you use electricity matters.** A four-hour job started at 17:00
  caused 41% more CO2 per kWh than the same job started at 10:30. Simply starting each day at
  the time that was cleanest over the previous four weeks cut emissions by 15.1% against a
  fixed 09:00 start (95% interval 10.8% to 19.4%), with no forecast at all.
- **The UAE is cleaning up its grid; the rest of the GCC is not yet.** In 2024 a kWh in the
  UAE carried 2.16 times the emissions of a kWh in the UK, down 30.8% since 2015 thanks to
  nuclear power. The GCC as a whole was at 2.93 times the UK and has barely changed.
- **Forecasting from past intensity alone has limits.** gridwatch's forecast beats simple
  rules of thumb by a small but statistically clear margin, and it is far behind the grid
  operator's own forecast, which uses weather and demand forecasts.

Four business questions, answered in SQL by the reporting models in
`dbt/models/marts/reporting/`, plus the forecast backtest. Every number below appears in the
generated tables, [generated/report.md](generated/report.md), at the precision quoted here;
`tests/test_docs_numbers.py` checks that, so a number typed by hand cannot drift from the
pipeline.

**The run.** GB and Ember data were ingested on 2026-09-25 (UTC). The warehouse was rebuilt
and the report tables regenerated from the same raw data on 2026-09-26, on a Windows 11
machine with Python 3.12.14, dbt-core 1.12.5, dbt-duckdb 1.11.0, DuckDB 1.5.5 and
scikit-learn 1.9.1 (the report lists every version). GB data runs from 2017-09-26 to
2026-09-25 21:00 UTC (157,771 half-hours). The Ember file is the version Ember last modified
on 2026-09-22. The GB reporting window is the last 12 complete months, **1 September 2025 to
31 August 2026** in UK local time.

**To reproduce:** `uv run gridwatch ingest`, then `uv run gridwatch transform --report-start
2025-09-01 --report-end 2026-08-31`, `uv run gridwatch backtest`, the validation runs in
[forecast.md](forecast.md#model), and `uv run gridwatch report`. The API revises recent
actuals, so a later run can differ slightly in the last decimals.

**Units.** GB figures are gCO2/kWh of operational emissions from the Carbon Intensity API.
Country figures are Ember's lifecycle gCO2e/kWh, which also count fuel supply, methane leaks
and construction. The two scales are not comparable: Ember puts the UK at 216.5 gCO2e/kWh
for 2024, while the API's GB mean for 2024 is 125.1 gCO2/kWh.

## (a) When should a flexible batch job run in Great Britain?

The job studied runs for four hours at a constant load, once a day, and may start at any
half-hour. For each possible start, the model `int_batch_job_windows` averages the actual
intensity over the next eight half-hours.

**Best time of day: late morning, not the night.** Over the window, a job starting at
**10:30 UK time** saw 106.1 gCO2/kWh on average, the lowest of the 48 start times. Starting at
**17:00** was the worst, at 149.5, which is 41% more carbon per kWh. The late-morning slot
wins because solar output peaks around midday; the evening peak is when demand is highest and
gas fills the gap.

**Best time of the week: weekend mornings.** Saturday at 10:00 or 10:30 (92.1 gCO2/kWh) and
Sunday at 09:30 to 10:30 (92.2 to 92.7) were the lowest-carbon starts of the week. Monday at
17:00 was the highest (162.9).

**Carbon saved against a fixed schedule.** Each rule below schedules one run per day over the
same 363 complete days. The 95% intervals come from a moving-block bootstrap that resamples
the days in blocks of 7 consecutive days, 2,000 times, keeping every rule on the same days:

| Rule | Mean gCO2/kWh | Saving vs 09:00 | 95% interval | Saving vs 17:00 | kg CO2 per run (100 kW, 4 h) |
| --- | ---: | ---: | --- | ---: | ---: |
| Fixed start 00:00 | 114.5 | -4.6% | -15.8% to 5.0% | 23.3% | 45.8 |
| Fixed start 09:00 | 109.5 | 0.0% | n/a | 26.7% | 43.8 |
| Fixed start 17:00 | 149.4 | -36.4% | n/a | 0.0% | 59.7 |
| Historical profile (previous 28 days) | **92.9** | **15.1%** | 10.8% to 19.4% | **37.8%** | 37.2 |
| API forecast (optimistic, see caveats) | 79.2 | 27.7% | 22.9% to 32.1% | 47.0% | 31.7 |
| Perfect foresight (lower bound) | 77.5 | 29.2% | 24.5% to 33.7% | 48.1% | 31.0 |

The **historical profile** rule needs no forecast: each day it starts the job at the time
that was lowest on average over the 28 days ending two days earlier. It saved **15.1%**
against a fixed 09:00 start (95% interval 10.8% to 19.4%) and **37.8%** against 17:00
(34.6% to 40.9%). For the illustrative 100 kW job that is 6.6 kg of CO2 per run against
09:00, or about 2.4 tonnes over the 363 days. It captured 52% of the saving that perfect
foresight would have given (16.6 of 32.0 gCO2/kWh against 09:00). It chose 11:00 on 127
days, mostly 11:00 or 11:30 from April to October, and moved to just after midnight (00:30
or 01:00) from November to March.

A midnight start, a common default for batch jobs, was 4.6% worse than 09:00 over this year,
but the 95% interval (-15.8% to 5.0%) includes zero: one year of data cannot separate the
two.

**Caveats.** The kg figures scale linearly with the job's energy. The API-forecast rule uses
the forecast the API keeps for past half-hours, which was issued shortly before each
half-hour; a scheduler deciding a day ahead would see a less accurate forecast, so that row
flatters the rule. The intervals show how much the result depends on which weeks the year
happened to contain; they do not cover errors in the API's own estimates. When two start
times tie, every rule takes the earlier one. Real jobs are rarely constant-load or fully
flexible, and shifting load changes marginal emissions, which this average-intensity
analysis does not model.

## (b) How do the UAE and the GCC compare with the UK and the EU?

Ember's latest year with data for every compared area is **2024**.

| Area | 2024 gCO2e/kWh | Times the UK | Change since 2015 | Main sources in 2024 |
| --- | ---: | ---: | ---: | --- |
| EU-27 | 211.6 | 0.98 | -39.2% | Nuclear 23%, wind and solar 29%, gas 16% |
| United Kingdom | 216.5 | 1.00 | -45.6% | Wind and solar 35%, gas 30%, nuclear 14% |
| United Arab Emirates | 467.5 | 2.16 | -30.8% | Gas 68%, nuclear 23%, wind and solar 9% |
| World (benchmark) | 473.7 | 2.19 | -11.3% | |
| Oman | 542.9 | 2.51 | -4.3% | Gas 91% |
| Qatar | 581.7 | 2.69 | -3.4% | Gas 96% |
| GCC, generation-weighted | 633.7 | 2.93 | -7.1% | |
| Kuwait | 635.3 | 2.93 | -2.3% | Gas 62%, oil and other fossil 36% |
| Saudi Arabia | 691.9 | 3.20 | -0.2% | Gas 63%, oil and other fossil 34% |
| Bahrain | 902.5 | 4.17 | -0.2% | Gas 100% |

- **The UAE is the GCC outlier.** Its intensity fell from 660.1 gCO2e/kWh in 2019 to 467.5 in
  2024 while generation grew from 138 to 177 TWh. Nuclear rose from 0% to 23% of generation
  over those years (the Barakah plant) and solar from 3% to 9%. In 2023 the UAE dropped
  below the world average for the first time in this series (483.8 against 485.5), and it
  stayed just below in 2024 (467.5 against 473.7).
- **The rest of the GCC barely moved.** The generation-weighted GCC figure fell 7.1% from
  2015 to 2024, against 45.6% for the UK and 39.2% for the EU. In Kuwait and Saudi Arabia,
  oil and other non-gas fossil fuels still supply about a third of electricity.
- **The gap is wide.** A kWh in the GCC carried 2.93 times the lifecycle emissions of a kWh
  in the UK in 2024; in Bahrain, 4.17 times.
- **2025.** Ember already has 2025 figures for the UK (218.2, up slightly), the EU (209.9),
  Kuwait, Oman and Qatar, but not yet for the UAE, Saudi Arabia or Bahrain.

**Caveats.** Ember multiplies generation by fuel by an emission factor per fuel, and uses one
2017 gas factor per country (Ember methodology, gas section). An almost all-gas system such as
Bahrain's therefore shows a nearly flat line, and efficiency gains at gas plants would not
show up. GCC generation data comes mainly from the EIA and the Energy Institute and is less
detailed than UK and EU data. The numbers are exported for reuse in
[`exports/annual_grid_intensity.csv`](../exports/annual_grid_intensity.csv).

## (c) Seasonal and regional variation in GB

**Year by year.** The national mean fell from 247.7 gCO2/kWh in 2018 to 125.1 in 2024, then
rose slightly to 129.2 in 2025 (both complete years). Coal fell from 3.4% of generation in
2018 to zero in 2025.

**By season** (report window):

| Season | Mean | 10th to 90th percentile | Lowest half-hour | Highest half-hour | Wind | Solar |
| --- | ---: | --- | --- | --- | ---: | ---: |
| Winter | 133.7 | 63 to 207 | 03:00 (98.5) | 16:30 (165.1) | 40% | 2% |
| Spring | 111.2 | 51 to 182 | 12:30 (79.7) | 19:30 (146.4) | 32% | 9% |
| Summer | 126.1 | 61 to 198 | 13:00 (79.7) | 20:30 (165.8) | 25% | 12% |
| Autumn | 125.8 | 55 to 219 | 03:00 (103.3) | 18:00 (157.8) | 39% | 4% |

Spring was the cleanest season. The shape of the day changes with the season: in winter and
autumn the cleanest hours are overnight, when demand is low; in spring and summer they are at
midday, when solar output peaks. The evening peak is the dirtiest part of the day all year.

**By region.** Mean forecast intensity over the window ranged from **8.0 gCO2/kWh in South
Scotland** and 13.2 in North Scotland, where wind makes up 66% and 92% of the regional mix,
to **243.5 in South West England** and **247.6 in South Wales**, where gas plays a larger
part. London averaged 142.3. Scotland as a whole averaged 18.9, England 131.6 and Wales 180.2.

**Caveats.** The API publishes only forecasts for regions, not actuals. According to its
methodology, regional values model the intensity of electricity consumed in each region,
using forecasts of regional demand and generation and a power-flow model, so they are model
outputs rather than measurements.

## (d) How accurate is the API's own forecast?

Over the window the API's forecast had a **mean absolute error of 9.4 gCO2/kWh** (RMSE 13.2,
MAPE 8.9%, bias +0.6). 67.0% of half-hours were within 10 gCO2/kWh of the actual value. Error
was lowest in winter (MAE 8.7) and highest in summer (10.3).

The absolute error has stayed between 9.1 and 10.3 gCO2/kWh every year since 2019, after
24.2 in 2017 and 2018. Because the grid got cleaner, the same absolute error is a growing
percentage: MAPE rose from 5.0% in 2019 to 9.7% in 2024.

**Caveats.** For past half-hours the API keeps only its latest forecast, which was issued
shortly before the half-hour, and it does not document the lead time. These figures therefore
describe a very short-horizon forecast, not a day-ahead one. To measure day-ahead accuracy
like for like, the pipeline stores the API's 48-hour forecast on every run
(`fct_api_forecast_snapshots`) and publishes the archive with the dashboard, because the API
cannot provide it later; `rpt_api_forecast_snapshot_accuracy` reports it by lead time once
enough snapshots have matured. The "actual" values are themselves estimates, so this is
agreement between two of the API's own numbers.

## Forecasting GB intensity 24 to 48 hours ahead

365 daily forecasts, issued at 00:00 UTC from 24 September 2025 to 23 September 2026, with the
model retrained every 28 days on the previous 730 days (method in [forecast.md](forecast.md)).

| Metric | gridwatch model | Same half-hour, last known day | Same half-hour, last week | API retained forecast |
| --- | ---: | ---: | ---: | ---: |
| MAE, 0-24 h | **32.3** | 39.9 | 54.4 | 9.9 |
| MAE, 24-48 h | **42.5** | 47.9 | 54.3 | 9.9 |
| RMSE, 24-48 h | **51.6** | 61.4 | 68.9 | 14.1 |
| MAPE, 24-48 h | **44.4%** | 47.5% | 53.4% | 9.4% |

- **Against the naive baselines the model helps, modestly, and the gain is not noise.** For
  the 24-48 hour band its MAE is 11% lower than repeating the last known day and 22% lower
  than repeating last week. Diebold-Mariano tests on the daily errors reject equal accuracy
  against both baselines: at 24-48 h the statistic is -2.91 (p = 0.004) against the last
  known day and -5.28 (p < 0.001) against last week.
- **It still loses often.** It beat the last-known-day baseline on only 211 of 365 days
  (57.8%) for the 24-48 hour band, so it lost on 42.2% of days. It was worse in September and
  October 2025 (October MAE 64.7 against 50.6) and worse than the last-week baseline in April
  2026 (43.1 against 39.7).
- **It is far behind the API.** The API's retained forecast (MAE 9.9 on the same test
  sample) is refreshed every half-hour and, according to its methodology, is built from
  forecasts of demand and of generation by fuel type. It is issued close to each half-hour,
  so it is not a like-for-like comparison, but the gap shows how much of GB intensity depends
  on wind and demand, which a model that sees only past intensity and the calendar cannot
  anticipate a day ahead.
- **Its uncertainty is well calibrated.** The 10-90% interval covered 79.6% (0-24 h) and
  79.4% (24-48 h) of actual values, close to the 80% target, with a mean width of 126.0
  gCO2/kWh at 24-48 hours.
- **The settings were chosen before the test year.** On a validation period that ended
  before the first test forecast, 730 training days gave a 24-48 h MAE of 33.7, against 36.7
  for 365 days and 34.2 for 1,460 days (validation table in the report).
- **MAPE looks large** because low-intensity half-hours turn small absolute errors into large
  percentages.

The change that would matter most is adding wind and demand forecasts as inputs. Once the
stored API snapshots cover several months, a model that takes the API's own day-ahead
forecast as an input can be backtested like for like.
