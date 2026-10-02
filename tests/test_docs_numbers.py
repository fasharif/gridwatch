"""Every number the findings and the README's results quote must come from the pipeline.

docs/generated/report.md is written by `gridwatch report` from the warehouse and the model
outputs. The prose in docs/findings.md, the results at the top of the README and the
validation part of docs/forecast.md is written by hand, so this test extracts every number,
date and time from that prose and checks that the report contains it at the same precision.
A mistyped or stale figure (for example 126 days where the report says 127) fails here.

Numbers that are settings rather than results (a model parameter, a year in a citation) are
listed in ALLOWED with the reason.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "generated" / "report.md"

LINK_TARGET = re.compile(r"\]\([^)]*\)|https?://\S+")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}")
TIME = re.compile(r"\b\d{2}:\d{2}\b")
# A number not glued to a word or a decimal point on its left, so "CO2", "P10" and the
# ".5" of a version "1.12.5" are not read as numbers. Thousands separators are removed.
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")

# (file, first line of the checked region, text that ends it; None means end of file)
REGIONS = [
    ("docs/findings.md", "# Findings", None),
    ("README.md", "**Headline results**", "## Features"),
    ("docs/forecast.md", "## Model", "## Why scikit-learn"),
]

ALLOWED = {
    "docs/findings.md": {
        "21:30": "--as-of time of the ingest step in the reproduction commands, a setting",
        "22:00": "--as-of time of the transform step in the reproduction commands, a setting",
    },
    "docs/forecast.md": {
        "0.05": "learning rate, a model setting",
        "300": "boosting iterations, a model setting",
        "31": "leaves per tree, a model setting",
        "50": "minimum samples per leaf, a model setting",
        "1.0": "L2 penalty, a model setting",
        "20.3": (
            "first-hour validation MAE of the first model design, which the current code no "
            "longer has; reproduced on 2026-09-27 by running the code before commit 7a39552"
        ),
    },
}


def tokens(text: str, expand_dates: bool = False) -> set[str]:
    """Dates, times and numbers in ``text``; with ``expand_dates`` also each date's parts."""
    text = LINK_TARGET.sub("]", text)
    found: set[str] = set()
    for date in DATE.findall(text):
        found.add(date)
        if expand_dates:
            year, month, day = date.split("-")
            found |= {year, str(int(month)), str(int(day))}
    text = DATE.sub(" ", text)
    found |= set(TIME.findall(text))
    text = TIME.sub(" ", text)
    found |= {number.replace(",", "") for number in NUMBER.findall(text)}
    return found


def region(path: str, start: str, end: str | None) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    assert start in text, f"{path} no longer contains {start!r}"
    body = text[text.index(start) :]
    if end is not None:
        assert end in body, f"{path} no longer contains {end!r} after {start!r}"
        body = body[: body.index(end)]
    return body


def test_tokens_read_numbers_dates_and_times() -> None:
    text = "In 2023 (483.8 against 485.5), 157,771 half-hours, CO2 and P10, dbt 1.12.5, 10:30."
    assert tokens(text) == {"2023", "483.8", "485.5", "157771", "1.12", "10:30"}
    assert {"2025-09-24", "2025", "9", "24"} <= tokens("from 2025-09-24", expand_dates=True)
    assert tokens("from 2025-09-24") == {"2025-09-24"}
    assert tokens("[findings](docs/findings-2024.md) and https://example.test/v2") == set()


@pytest.mark.parametrize(("path", "start", "end"), REGIONS)
def test_every_quoted_number_is_in_the_generated_report(
    path: str, start: str, end: str | None
) -> None:
    available = tokens(REPORT.read_text(encoding="utf-8"), expand_dates=True)
    allowed = ALLOWED.get(path, {})
    missing = sorted(tokens(region(path, start, end)) - available - allowed.keys())
    assert not missing, (
        f"{path} quotes {missing}, which docs/generated/report.md does not contain. "
        "Quote the report's value, or add the derived figure to the report's headline table."
    )


def test_a_mistyped_number_is_caught() -> None:
    """The findings once said '11:00 on 126 days'; the report's own figure is different."""
    available = tokens(REPORT.read_text(encoding="utf-8"), expand_dates=True)
    assert "126" not in available
    assert "486" not in available
