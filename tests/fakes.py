"""A fake Carbon Intensity API for unit tests.

It reproduces the behaviour gridwatch depends on, as observed on the real API:

* a range returns every half-hour whose *end* lies in ``[from, to]``;
* ``from`` must be before ``to``; ranges over 31 days (14 for regional) are rejected;
* ``/generation`` and ``/regional/intensity`` only return half-hours whose end falls in
  the calendar year of ``from`` (the year-boundary truncation).

Values are deterministic functions of time, so two fetches of the same half-hour agree.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

HALF_HOUR = timedelta(minutes=30)
FMT = "%Y-%m-%dT%H:%MZ"
FUELS = ("biomass", "coal", "imports", "gas", "nuclear", "other", "hydro", "solar", "wind")
SHARES = (5.0, 0.0, 10.0, 30.0, 15.0, 0.5, 1.5, 8.0, 30.0)
REGIONS = ((1, "North Scotland"), (13, "London"), (18, "GB"))


def intensity_at(start: datetime) -> int:
    hours = start.timestamp() / 3600
    return round(150 + 60 * math.sin(2 * math.pi * hours / 24))


def _error(status: int, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": f"{status}", "message": message}})


@dataclass
class FakeCarbonApi:
    """Callable httpx handler; pass ``httpx.MockTransport(FakeCarbonApi())``."""

    missing: set[datetime] = field(default_factory=set)
    # (region id, half-hour start) pairs the regional endpoint leaves out
    missing_regions: set[tuple[int, datetime]] = field(default_factory=set)
    fail_on: dict[int, int] = field(default_factory=dict)  # request number -> status
    actual_offset: int = 0  # add to actual values, to simulate revisions
    requests: list[str] = field(default_factory=list)
    on_request: Callable[[str], None] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if self.on_request is not None:
            self.on_request(url)
        number = len(self.requests)
        if number in self.fail_on:
            return _error(self.fail_on[number], "injected failure")
        path = request.url.path
        match = re.fullmatch(r"/intensity/([^/]+)/fw48h", path)
        if match:
            start = datetime.strptime(match.group(1), FMT).replace(tzinfo=UTC)
            return self._national(start, start + timedelta(hours=48), limit=None)
        match = re.fullmatch(r"/(intensity|generation|regional/intensity)/([^/]+)/([^/]+)", path)
        if not match:
            return _error(404, "not found")
        kind, raw_from, raw_to = match.groups()
        start = datetime.strptime(raw_from, FMT).replace(tzinfo=UTC)
        end = datetime.strptime(raw_to, FMT).replace(tzinfo=UTC)
        if start >= end:
            return _error(400, "The start datetime should be less than the end datetime")
        if kind == "intensity":
            return self._national(start, end, limit=timedelta(days=31))
        if kind == "generation":
            return self._generation(start, end)
        return self._regional(start, end)

    def _periods(self, start: datetime, end: datetime, same_year: bool) -> list[datetime]:
        periods = []
        cursor = start - HALF_HOUR
        while cursor + HALF_HOUR <= end:
            finish = cursor + HALF_HOUR
            if cursor not in self.missing and (not same_year or finish.year == start.year):
                periods.append(cursor)
            cursor += HALF_HOUR
        return periods

    def _national(self, start: datetime, end: datetime, limit: timedelta | None) -> httpx.Response:
        if limit is not None and end - start > limit:
            return _error(400, "The date range you have specified is greater than 31 days.")
        data = [
            {
                "from": p.strftime(FMT),
                "to": (p + HALF_HOUR).strftime(FMT),
                "intensity": {
                    "forecast": intensity_at(p) + 3,
                    "actual": intensity_at(p) + self.actual_offset,
                    "index": "moderate",
                },
            }
            for p in self._periods(start, end, same_year=False)
        ]
        return httpx.Response(200, json={"data": data})

    def _generation(self, start: datetime, end: datetime) -> httpx.Response:
        if end - start > timedelta(days=31):
            return _error(400, "The date range you have specified is greater than 31 days.")
        data = [
            {
                "from": p.strftime(FMT),
                "to": (p + HALF_HOUR).strftime(FMT),
                "generationmix": [
                    {"fuel": f, "perc": s} for f, s in zip(FUELS, SHARES, strict=True)
                ],
            }
            for p in self._periods(start, end, same_year=True)
        ]
        return httpx.Response(200, json={"data": data})

    def _regional(self, start: datetime, end: datetime) -> httpx.Response:
        if end - start >= timedelta(days=14):
            return _error(400, "The date range you have specified is greater than 14 days.")
        data = []
        for p in self._periods(start, end, same_year=True):
            regions = [
                {
                    "regionid": rid,
                    "dnoregion": f"DNO {rid}",
                    "shortname": name,
                    "intensity": {"forecast": intensity_at(p) - rid, "index": "low"},
                    "generationmix": [
                        {"fuel": f, "perc": s} for f, s in zip(FUELS, SHARES, strict=True)
                    ],
                }
                for rid, name in REGIONS
                if (rid, p) not in self.missing_regions
            ]
            data.append(
                {"from": p.strftime(FMT), "to": (p + HALF_HOUR).strftime(FMT), "regions": regions}
            )
        return httpx.Response(200, json={"data": data})
