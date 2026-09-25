# Data

## Sources and attribution

| Source | What gridwatch uses | Licence | Where |
| --- | --- | --- | --- |
| **Carbon Intensity API**, National Energy System Operator (NESO) | National half-hourly forecast and actual intensity (from 2017-09-26), national generation mix (from 2018-05-10), regional forecast intensity and mix for 14 DNO regions and 4 aggregates (from 2023-01-01 by default), and the 48-hour forecast as issued on each run | CC BY 4.0, under the [API terms of use](https://github.com/carbon-intensity/terms) | <https://api.carbonintensity.org.uk>, documented at <https://carbon-intensity.github.io/api-definitions/> |
| **Ember Yearly Electricity Data** | Annual generation, emissions and lifecycle emissions intensity by source for the UAE, Saudi Arabia, Qatar, Kuwait, Bahrain, Oman, the UK, the EU-27 and the world | CC BY 4.0 | <https://ember-energy.org/data/yearly-electricity-data/>, bulk file `release_generation_yearly_global.csv` |

Attribution text used on the dashboard and in exports: "Carbon Intensity API, National
Energy System Operator (CC BY 4.0)" and "Ember Yearly Electricity Data (CC BY 4.0)". gridwatch
is not affiliated with NESO or Ember and does not use their logos. The API terms say NESO
applies an unpublished rate limit, so the client waits at least one second between requests.

**Version used for the committed findings.** API data fetched on 2026-09-25 (UTC). Ember file
with `Last-Modified: Tue, 22 Sep 2026 16:24:55 GMT`, SHA-256
`ea214963f4a98b26f52aaf541736d4310349a905e64f83e63425bfc3ab6255d7`, 104,203 rows. Every run
records the ETag, Last-Modified time and hash in `data/raw/ember/yearly_electricity/manifest.json`.

## What the two sources measure

| | Carbon Intensity API | Ember |
| --- | --- | --- |
| Area | Great Britain (no Northern Ireland) | Country or region |
| Resolution | Half-hour | Year |
| Emissions counted | Operational emissions at the point of generation, including imports; wind, solar and nuclear count as zero (see `/intensity/factors`) | Lifecycle: fuel supply, methane leaks, construction; every gas converted to CO2-equivalent over 100 years |
| Unit | gCO2/kWh | gCO2e/kWh |
| Actual or model | Forecast plus an estimated actual (national only); regional values are forecasts | Annual statistics times emission factors |

Because of these differences Ember's UK figure (216.5 gCO2e/kWh in 2024) is far above the
API's GB mean (125.1 gCO2/kWh in 2024). Compare within a source, not across them.

## API behaviour gridwatch relies on

Found by probing the API in September 2026 and handled in `src/gridwatch/ingest/carbon_intensity.py`:

- A range query returns every half-hour whose **end** is in `[from, to]`. To fetch the
  half-hours starting in `[a, b)`, gridwatch asks for `from = a + 30 min`, `to = b`.
- `/intensity` and `/generation` reject ranges over 31 days; `/regional/intensity` rejects
  ranges of 14 days or more. gridwatch uses 30-day and 13-day chunks.
- `/generation` and `/regional/intensity` silently stop at the end of the calendar year of
  `from`: a request from 15 December to 10 January returns December only. The first full
  backfill lost up to 30 days at the start of each year to this, until chunks were split at
  year boundaries. The half-hour starting 23:30 on 31 December ends in the new year, and the
  API files it there.
- `from` must be earlier than `to`, so a single missing half-hour is requested with one
  extra half-hour, which the parser drops.
- For past half-hours the API keeps only its latest forecast. `/intensity/{t}/fw48h` for a
  past `t` returns those retained values, not the forecast as it stood at `t`.
- Actual values for the latest half-hours are revised: the same half-hour returned 180 and
  then 173 gCO2/kWh a few minutes apart. Each run re-requests the last two days
  (`GRIDWATCH_REFETCH_DAYS`).

## Raw storage

`gridwatch ingest` writes Parquet under `data/raw/`, one file per calendar month of the
period start (`carbon_intensity/national_intensity/2025/2025-01.parquet`). Timestamps are
naive UTC. Each row carries `fetched_at_utc`.

- **Idempotent.** A row is rewritten only when a value changed, and a month file only when a
  row in it changed. Running the same ingestion twice leaves every file byte-for-byte
  unchanged (`tests/test_pipeline.py`).
- **Incremental.** Each run requests the configured start to now if nothing is stored,
  backfills if the configured start moved earlier, and otherwise re-requests only the last two
  days. `--repair-gaps` also re-requests every hole inside the stored history.
- **Safe to interrupt.** Rows are saved after every request, and files are written to a
  temporary name and renamed. A failed run says how many requests succeeded and resumes on the
  next run.
- **Ember** is downloaded with a conditional GET (`If-None-Match`, `If-Modified-Since`), so a
  daily run downloads the 16 MB file only when Ember has published a new version.

The full history (September 2017 to September 2026, regional from 2023) takes about 27 MB of
Parquet.

## Data quality

**Gaps the API itself has.** After re-requesting every missing half-hour with
`--repair-gaps`, these remained, so they are upstream gaps rather than ingestion failures.
They are listed in `dbt/seeds/known_source_gaps.csv`, and the `no_half_hour_gaps` test fails on
any gap that is not listed.

| Dataset | Gaps | Missing half-hours |
| --- | ---: | ---: |
| National intensity | 5 (2021 to 2024) | 179 |
| National generation mix | 7 (2018 to 2025) | 414 |
| Regional intensity | 4 (2023 to 2025, all regions) | 129 |

**Implausible values.** The national history holds forecasts of 1,545 to 13,579 gCO2/kWh
(December 2018 to July 2019), forecasts of 5 to 7 when the actual was 145 to 181, and actuals
of 0 (2023 and 2026). GB national intensity has stayed roughly between 20 and 500. Values
outside 10 to 700 gCO2/kWh are set to null in `int_national_half_hours` and flagged
(`is_actual_implausible`, `is_forecast_implausible`): 6 actuals and 22 forecasts in the
current history. Warn-level tests in staging count them, and fail the build if more than 100
appear.

**Missing actuals.** 625 national half-hours that the API serves have no actual value, 308 of them in 2019.
They stay null; the gap-free fact table marks them with `is_actual_missing`.

**Ember anomalies outside the compared areas.** The September 2026 file has negative 2025
values for some areas gridwatch does not compare (for example Costa Rica's total emissions).
Staging tests over all areas report these as warnings; the strict tests sit on the country
marts, which contain only the compared areas.

## Warehouse layout

dbt builds a DuckDB file (`data/warehouse/gridwatch.duckdb`) with these schemas:

| Schema | Contents |
| --- | --- |
| `staging` | Views over the raw Parquet with types and names cleaned |
| `intermediate` | The gap-free national half-hour spine with UK local time keys and cleaning, the generation mix pivoted, Ember pivoted per country and year, and batch-job windows |
| `marts` | Dimensions `dim_date`, `dim_time_of_day`, `dim_region`, `dim_fuel`, `dim_country`; facts `fct_national_intensity`, `fct_generation_mix`, `fct_regional_intensity`, `fct_regional_generation_mix_daily`, `fct_api_forecast_snapshots`, `fct_country_electricity_annual`, `fct_country_intensity_annual` |
| `reporting` | One model per business question (`rpt_*`) |
| `reference` | Seeds: regions, fuels, countries, bank holidays, known source gaps |

Facts are keyed by UTC time; `date_key` and `time_key` point to UK local dates and half-hour
slots, because people schedule in local time. On clock-change days a local slot can occur
twice (October) or not at all (March).

## Exports

- `exports/annual_grid_intensity.csv`: annual lifecycle intensity for the UAE, the other GCC
  countries, the UK and the EU from 2000, with generation, emissions, source URL and licence
  on every row, for use in other projects' carbon estimates. Regenerate with
  `gridwatch export`.
- `powerbi/data/`: the star schema as CSV (see [powerbi/README.md](../powerbi/README.md)).
