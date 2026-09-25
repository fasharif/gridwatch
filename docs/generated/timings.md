# Pipeline timings

**Pending a measured run.** The build machine was shared with other heavy jobs while this
project was developed, so no timings from it are published. Run
`uv run python scripts/time_pipeline.py --repeats 3` on an idle machine to replace this file
with measured medians.

| Step | Median seconds |
| --- | ---: |
| ingest (replayed cassette) | pending |
| transform (dbt build and tests) | pending |
| forecast (train and predict) | pending |
| backtest (7 origins) | pending |
| report | pending |
| export | pending |
| site | pending |
