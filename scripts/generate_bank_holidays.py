"""Regenerate dbt/seeds/uk_bank_holidays.csv from the `holidays` package.

England and Wales bank holidays are used for GB-wide demand patterns because they cover
most of GB demand. Run: uv run python scripts/generate_bank_holidays.py
"""

from __future__ import annotations

import csv
from pathlib import Path

from gridwatch.forecast.calendar import uk_bank_holidays

FIRST_YEAR = 2017
LAST_YEAR = 2028


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "dbt" / "seeds" / "uk_bank_holidays.csv"
    rows = uk_bank_holidays(range(FIRST_YEAR, LAST_YEAR + 1))
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["holiday_date", "holiday_name"])
        for day, name in sorted(rows.items()):
            writer.writerow([day.isoformat(), name])
    print(f"wrote {len(rows)} holidays to {target}")


if __name__ == "__main__":
    main()
