# Design decisions

Short records of the choices that shaped gridwatch: the context, the decision and what
follows from it.

## 1. Python 3.12, uv and a lockfile

**Context.** dbt-core and its DuckDB adapter lag new Python releases, and the development
machine runs Python 3.14.
**Decision.** Target Python 3.12 (`requires-python = ">=3.12,<3.14"`), manage dependencies with
uv and commit `uv.lock`. CI installs with `uv sync --locked`.
**Consequences.** Everyone gets the same versions; Dependabot proposes updates. uv can
download Python 3.12 itself, so no system Python change is needed.

## 2. DuckDB with dbt-duckdb as the warehouse

**Context.** The data is small (about 27 MB of Parquet), the pipeline runs once a day in CI,
and the dashboard must build without secrets.
**Decision.** DuckDB, a single file, read by dbt through dbt-duckdb. dbt sources point
straight at the raw Parquet with `read_parquet`.
**Consequences.** No server to run or pay for, and CI can build the whole warehouse. The
trade-off is one writer at a time, which is why dbt runs in a child process (record 13).

## 3. Raw layer: monthly Parquet with idempotent upserts

**Context.** The API revises recent actual values, the history needs backfilling, and CI runs
can fail half-way.
**Decision.** Keep raw data as Parquet, one file per dataset and calendar month, keyed by
period (and fuel or region). An upsert rewrites a month only if a row is new or changed, and
writes through a temporary file and a rename. Each run re-requests the last two days.
**Consequences.** Re-running is always safe and leaves files unchanged when nothing changed.
Revised actuals replace old ones; the previous value is not kept (no history of revisions).

## 4. Ember bulk CSV instead of the Ember API

**Context.** The Ember API answers `{"detail": "No API key set"}` without a key; the bulk CSV
is public under CC BY 4.0.
**Decision.** Download the yearly CSV with a conditional GET and store it as Parquet with
snake_case columns, failing loudly if an expected column disappears.
**Consequences.** No secret to manage. The file is 16 MB but is only downloaded when Ember
publishes a new version.

## 5. Work with the API as it behaves, not as documented

**Context.** Probing showed that range queries return half-hours by their end time, that
`/generation` and `/regional/intensity` stop at the end of the `from` year, and that the API
rate-limits without saying how much (details in [data.md](data.md)). The first backfill lost
up to 30 days at the start of each year before this was understood.
**Decision.** Shift request windows by 30 minutes, split chunks at year boundaries, keep
chunks within the documented range limits, wait at least one second between requests, retry
transient errors with jittered back-off and honour `Retry-After`. Add `--repair-gaps` to
re-request holes.
**Consequences.** A fake API in the tests reproduces these behaviours, so a regression shows
up as a failing test rather than silently missing data.

## 6. Data quality: flag, do not hide

**Context.** The history holds upstream errors (forecasts of 13,579 gCO2/kWh, actuals of 0)
and some half-hours the API never serves.
**Decision.** Keep raw values in staging. In the intermediate layer, null out national values
outside 10 to 700 gCO2/kWh and flag them. List known upstream gaps in a seed so the gap test
fails only on new gaps. Warn-level tests count known anomalies and turn into errors past a
threshold.
**Consequences.** Analyses use clean values, the anomalies stay visible and counted, and a
new ingestion fault fails the build. So does a new permanent gap upstream, which has happened
about once a year: the daily deploy then waits until someone confirms the gap with
`--repair-gaps` and adds a seed row. That is a deliberate trade of availability for
correctness; the steps are in the
[runbook in data.md](data.md#when-the-gap-test-fails).

## 7. Dimensional model keyed by UTC, described in UK local time

**Context.** Settlement periods are defined in UTC, but people schedule jobs in local time and
clock changes make local time ambiguous.
**Decision.** Facts are keyed by the UTC period start. `date_key` and `time_key` point to UK
local calendar and time-of-day dimensions. The national fact sits on a gap-free half-hour
spine, with missing values as nulls.
**Consequences.** Window functions can count rows as time (eight rows are four hours).
Local slots can repeat or vanish on clock-change days, which the reports tolerate.

## 8. A fixed, overridable reporting window

**Context.** Findings should describe a full year and be reproducible later.
**Decision.** Reports cover the last 12 complete calendar months by default, and
`--report-start` and `--report-end` pin a window.
**Consequences.** The daily dashboard moves forward month by month; the committed findings
state their window and the command to reproduce them.

## 9. Forecast with scikit-learn gradient boosting

**Context.** The spec allows statsforecast, scikit-learn or LightGBM. The model needs
calendar features (bank holidays), many horizons and prediction intervals.
**Decision.** One scikit-learn `HistGradientBoostingRegressor` for all 96 horizons, predicting
the change from the trailing 24-hour mean, with conformally calibrated quantile models for
the interval. Details and the comparison with LightGBM and statsforecast are in
[forecast.md](forecast.md).
**Consequences.** Pure wheels, no system libraries, one dependency already common in data
teams. Without weather inputs the model cannot match the API's own forecast, which the
findings say plainly.

## 10. Honest evaluation against baselines and the API

**Context.** A forecast is only useful if it beats something simple, and the API's own
forecast history is not a day-ahead record.
**Decision.** Rolling-origin backtest over a year, retraining every 28 days, against two
seasonal naive baselines and the API's retained forecast, on a common sample, with monthly
errors and win rates. Store the API's 48-hour forecast on every run so a like-for-like
comparison becomes possible.
**Consequences.** The results show where the model loses, and Diebold-Mariano tests say
whether its gains are larger than chance (record 19). Settings are chosen on validation runs
before the test year; each run keeps its own files (named after its cutoff, training window
and calibration period) and the report lists them all. The snapshot comparison needs weeks of
daily runs before it says anything.

## 11. Dashboard: generated HTML with Plotly

**Context.** The spec allows Evidence.dev, Observable Framework or generated HTML with Plotly.
The dashboard must build in CI without secrets and deploy to GitHub Pages.
**Decision.** Generate one static page from Python with Jinja2 and Plotly, serving Plotly's
JavaScript from the site itself.
**Consequences.** One language and one toolchain for the whole project, no Node build, and
charts read the same marts the tests cover. The trade-off is fewer layout conveniences than
Evidence or Observable; the page is hand-built but small. Every chart has a table view, and
the colours come from a colour-blind checked palette with separate light and dark steps. The
template is always autoescaped. dbt's own static documentation (models, tests, lineage and
exposures) is published beside the page under `dbt/`, rather than rebuilding lineage views in
the dashboard.

## 12. Keeping history between scheduled runs

**Context.** The daily workflow needs yesterday's raw data to run incrementally, and bulky
data must not be committed to git. Most raw data can be fetched again from the API, but the
48-hour forecast *as issued* cannot: the API keeps only its latest forecast for each
half-hour, so the snapshots stored on each run are the only record of them.
**Decision.** Store `data/raw` and `data/outputs` in the GitHub Actions cache. Each run
restores the most recent cache, ingests, and saves a new cache entry straight after ingestion
(so a later failure does not lose the fetch) and again after the model steps. The forecast
snapshots are also published with the dashboard on GitHub Pages
(`data/forecast_snapshots/`, Parquet files plus a manifest with SHA-256 hashes), and each run
first merges the published copy into the local store (`gridwatch restore-snapshots`). A 404
before the first deploy is normal; any other failure stops the run, so a site with fewer
snapshots than the live one is never deployed. Each run also uploads the files as a 90-day
workflow artifact, as a second copy.
**Consequences.** No extra service or credentials, and nothing in git. GitHub evicts caches
unused for 7 days or beyond 10 GB; if that happens the next run backfills the GB and Ember
history, which the idempotent ingestion handles at the cost of about 335 API requests, and
restores the snapshots from the site. The snapshot archive lives as long as the Pages site
does, and it is public, which suits CC BY 4.0 data with attribution. GitHub disables
scheduled workflows in a public repository after 60 days without activity; the dashboard then
stops updating and snapshots stop accumulating, but the published archive is kept.
Re-enable the workflow under *Actions > Daily pipeline > Enable workflow*. Release assets or
an orphan data branch were rejected because they would put data into the repository's
history or need a write token.

## 13. dbt runs in a child process

**Context.** Running dbt in-process kept its DuckDB connection open, and DuckDB refused the
forecast step's read-only connection to the same file.
**Decision.** Run `python -m dbt.cli.main` as a child process with its target and log paths
under the data directory, and read `run_results.json`.
**Consequences.** Clean separation, no dbt state left behind, and the repository stays free
of dbt build output. Because dbt writes only to the data directory, the dbt project can also
ship read-only inside the wheel (`gridwatch/dbt_project`), so an installed `gridwatch` works
outside a source checkout.

## 14. Tests and CI never call the network

**Context.** Tests that depend on a live API are slow and flaky, and results change daily.
**Decision.** Unit tests use recorded responses and a fake API; pytest-socket blocks the
network. CI replays a recorded cassette through the whole pipeline, including the dbt build
and tests, with the freshness test pinned to the recording time.
**Consequences.** CI is deterministic. The cassette must be re-recorded with
`scripts/record_fixtures.py` if the API changes format.

## 15. The backtest runs weekly in the daily workflow

**Context.** The year-long backtest retrains the model 14 times and is the slowest step.
**Decision.** Run it on Mondays, on request, or when no cached result exists; reuse the cached
summary on other days.
**Consequences.** Daily runs stay short; the backtest on the dashboard can be up to a week
old, and the page says when it was generated.

## 16. Power BI as a documented star schema, not a .pbix

**Context.** A Power BI file cannot be authored on this machine.
**Decision.** Export the star schema as CSV, write the DAX measures and a step-by-step build
guide in `powerbi/`, and mark the report as not built.
**Consequences.** Anyone with Power BI Desktop can build the report from the guide; nothing
claims that a report exists.

## 17. What data is committed

**Decision.** Commit only small, useful outputs: the annual intensity CSV (36 KB), the Power
BI snapshot (about 2 MB, 90 days of half-hourly data plus daily history) and the recorded test
fixtures (280 KB). Raw data, the warehouse and model outputs are rebuilt and ignored by git;
the forecast snapshot archive lives on the Pages site (record 12), not in git.

## 18. Pin third-party actions to a commit, and check every reference

**Context.** GitHub's own actions publish moving major tags (`actions/checkout@v7`).
`astral-sh/setup-uv` stopped doing so, so `setup-uv@v10` does not exist and every job would
have failed at set-up. actionlint does not notice, because it does not resolve references.
**Decision.** Use major tags for actions owned by GitHub, and pin third-party actions to a
full commit SHA with the version as a comment (`@c18668a... # v10.2.0`). CI runs
`scripts/check_action_refs.py`, which asks GitHub with `git ls-remote` whether each tag or
branch exists and whether each pinned SHA is the commit its version comment names.
**Consequences.** A missing or mistyped version fails the lint job instead of every job, and
a third-party tag cannot be moved under the workflow. Dependabot updates SHA pins and their
comments together.

## 19. State the uncertainty of headline results

**Context.** The scheduling saving and the model's gain over the naive baselines were point
estimates from one year. Daily results are autocorrelated, so treating days as independent
would overstate the precision.
**Decision.** Report 95% intervals from a moving-block bootstrap (blocks of 7 consecutive
days, 2,000 resamples, a fixed seed, days paired across rules) for every scheduling saving,
and Diebold-Mariano tests with a Newey-West variance (7 lags) for the model against each
naive baseline. Both are written in numpy in `stats.py` rather than adding a statistics
dependency.
**Consequences.** The findings can say which differences one year of data can and cannot
support: the profile rule's saving against 09:00 is clearly positive, while a midnight start
cannot be told apart from 09:00. The intervals describe sampling variation between weeks, not
errors in the API's estimates.

## 20. Numbers in the prose come from the pipeline, and are reproducible

**Context.** The findings were typed by hand from the generated tables, and three figures
did not match. One of them, the number of days the profile rule chose 11:00, could not be
reproduced at all: `arg_min` broke ties between equal means arbitrarily, so two builds of the
same data differed.
**Decision.** `gridwatch report` computes every derived figure the prose quotes (a headline
table), and `tests/test_docs_numbers.py` fails if the findings, the README's results or the
validation part of forecast.md quote a number the report does not contain. The scheduling
rules break ties by the earliest start, pinned by a dbt unit test.
**Consequences.** Two rebuilds of the warehouse give identical results, and a stale or
mistyped number fails CI. Updating the findings means regenerating the report first.
