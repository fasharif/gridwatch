# Test fixtures

`cassette/` holds HTTP responses recorded on 2026-09-25 (UTC) by `scripts/record_fixtures.py`,
under the settings in `fixture.env` (data from 2026-07-23 to 2026-09-20 UTC, regional data
from 2026-09-16). Bodies are gzip-compressed; `index.json` maps each URL to its status,
kept headers and body file. The tests and CI replay these files, so they never call the
network.

- Carbon Intensity API responses are stored exactly as received.
- The Ember yearly CSV is trimmed to the nine areas in `dbt/seeds/countries.csv`, with every
  column and year kept. The ETag and Last-Modified headers are those of the full file.

The fixture window contains two real upstream errors that the pipeline must handle: an
actual of 0 gCO2/kWh on 2026-08-06 at 11:00 UTC and a forecast of 5 gCO2/kWh on 2026-08-23
at 20:30 UTC.

The recorded 48-hour forecast snapshot (`/intensity/2026-09-20T00:00Z/fw48h`) was fetched on
2026-09-25, after the half-hours it covers. For a past time the API returns its retained
short-lead values, not the forecast as it stood then, so this snapshot is far more accurate
than a real day-ahead one. It is fine as test data for the snapshot tables, but it is not a
day-ahead record. A normal `gridwatch ingest --as-of` in the past skips the snapshot for
this reason; only replays and `scripts/record_fixtures.py` store one.

## Licences and attribution

- Carbon Intensity API data: National Energy System Operator (NESO), CC BY 4.0,
  <https://carbonintensity.org.uk/>.
- Ember Yearly Electricity Data: Ember, CC BY 4.0,
  <https://ember-energy.org/data/yearly-electricity-data/>.

To re-record, run `uv run python scripts/record_fixtures.py` (needs network access), then
run the tests: some assertions pin values in the recorded window.
