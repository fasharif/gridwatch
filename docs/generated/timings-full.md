# Pipeline timings: full workload

**Pending a measured run.** This is the workload that decides whether the daily workflow
fits its 120-minute limit: a first run against the live API and Ember from an empty data
directory (the backfill since 2017, the dbt build over every regional row, and the 365-day
backtest with 14 retrains). It has not been timed on a quiet machine. Two ways to fill it:

- `uv run python scripts/time_pipeline.py --workload full` on an otherwise idle machine
  (needs network access), which replaces this file; or
- the first run of the *Daily pipeline* workflow on GitHub, which writes each step's
  duration and the runner's CPU count and memory to the job summary.

| Step | Median seconds |
| --- | ---: |
| ingest (full backfill from the live API) | pending |
| transform (dbt build and tests) | pending |
| forecast (train and predict) | pending |
| backtest (365 origins, 14 retrains) | pending |
| report | pending |
| export | pending |
| docs (dbt docs generate) | pending |
| site | pending |
