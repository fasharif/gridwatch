"""Re-record the HTTP cassette used by the tests and by CI (needs network access).

The cassette holds real API responses for a fixed window, so the pipeline, the dbt project
and the dashboard can be built in CI without network access and with stable results:

    uv run python scripts/record_fixtures.py

Carbon Intensity API responses are stored exactly as received. The Ember CSV is about 16 MB,
so it is trimmed to the areas in dbt/seeds/countries.csv (all columns and years kept) before
it is stored; the recorded ETag and Last-Modified headers are those of the full file.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from gridwatch.config import EMBER_YEARLY_CSV, USER_AGENT, Settings
from gridwatch.ingest.cassette import KEPT_HEADERS, RecordingTransport, body_name
from gridwatch.ingest.http import HttpFetcher, RateLimiter, RetryPolicy, build_client
from gridwatch.ingest.pipeline import run_ingest

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "tests" / "fixtures" / "cassette"
FIXTURE_ENV = ROOT / "tests" / "fixtures" / "fixture.env"


def read_fixture_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in FIXTURE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def record_carbon_intensity(env: dict[str, str]) -> None:
    as_of = datetime.fromisoformat(env["FIXTURE_AS_OF"]).replace(tzinfo=UTC)
    with tempfile.TemporaryDirectory() as scratch:
        settings = Settings.from_env({**env, "GRIDWATCH_DATA_DIR": scratch})
        transport = RecordingTransport(CASSETTE)
        client = build_client(USER_AGENT, 60.0, transport)
        fetcher = HttpFetcher(client, RateLimiter(1.0), RetryPolicy())
        # FIXTURE_AS_OF is in the past, so the recorded "snapshot" holds the API's retained
        # short-lead values, not a forecast as issued (see tests/fixtures/README.md). That is
        # fine for test data; the real archive never stores a snapshot for a past time.
        report = run_ingest(
            settings,
            fetcher,
            as_of,
            ["national", "generation", "regional", "snapshot"],
            allow_past_snapshot=True,
        )
        print(report.to_json())


def record_trimmed_ember() -> None:
    areas = {
        row["ember_area"]
        for row in csv.DictReader((ROOT / "dbt" / "seeds" / "countries.csv").open(encoding="utf-8"))
    }
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=300, follow_redirects=True
    ) as client:
        response = client.get(EMBER_YEARLY_CSV)
        response.raise_for_status()
    reader = csv.reader(io.StringIO(response.text))
    header = next(reader)
    area_index = header.index("Area")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    kept = 0
    for row in reader:
        if row[area_index] in areas:
            writer.writerow(row)
            kept += 1
    name = body_name(EMBER_YEARLY_CSV)
    (CASSETTE / name).write_bytes(gzip.compress(out.getvalue().encode("utf-8"), mtime=0))
    index_path = CASSETTE / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index[EMBER_YEARLY_CSV] = {
        "status": 200,
        "headers": {k: response.headers[k] for k in KEPT_HEADERS if k in response.headers},
        "body": name,
    }
    index_path.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Ember: kept {kept} rows for {len(areas)} areas")


def main() -> None:
    env = read_fixture_env()
    if CASSETTE.exists():
        shutil.rmtree(CASSETTE)
    CASSETTE.mkdir(parents=True)
    record_carbon_intensity(env)
    record_trimmed_ember()


if __name__ == "__main__":
    main()
