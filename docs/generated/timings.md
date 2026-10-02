# Pipeline timings: fixture workload

**Pending a measured run.** The build machine was shared with other heavy jobs while this
project was developed, so no timings from it are published. Run
`uv run python scripts/time_pipeline.py --repeats 3` on an otherwise idle machine to replace
this file with measured medians. The workload is the recorded fixture (59 days of GB data,
30-day training window, 7-day backtest), so results compare across machines.

| Step | Median seconds |
| --- | ---: |
| ingest (replayed cassette) | pending |
| transform (dbt build and tests) | pending |
| forecast (train and predict) | pending |
| backtest (7 origins) | pending |
| report | pending |
| export | pending |
| docs (dbt docs generate) | pending |
| site | pending |
