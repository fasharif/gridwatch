"""Take screenshots of the built dashboard with headless Chromium (Playwright).

Needs only Playwright, so it runs in the official image without installing gridwatch:

    docker run --rm -v "$PWD:/work" -w /work mcr.microsoft.com/playwright/python:v1.63.0-noble \
        python scripts/screenshot.py --site site --out docs/images

On Git Bash for Windows prefix the command with MSYS_NO_PATHCONV=1 and use "$(pwd -W)".
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def capture(site: Path, out: Path, width: int, scheme: str, full_page: bool, name: str) -> Path:
    index = (site / "index.html").resolve()
    if not index.exists():
        raise SystemExit(f"{index} not found; build the dashboard with `gridwatch site` first")
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": 900},
            color_scheme="dark" if scheme == "dark" else "light",
            device_scale_factor=1,
        )
        page.goto(index.as_uri())
        # Every chart container gets Plotly's class once it has rendered.
        expected = page.locator(".chart").count()
        page.wait_for_function(
            "n => document.querySelectorAll('.chart.js-plotly-plot').length >= n",
            arg=expected,
            timeout=30_000,
        )
        page.wait_for_timeout(500)
        page.screenshot(path=str(target), full_page=full_page)
        browser.close()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", default="site", type=Path)
    parser.add_argument("--out", default="docs/images", type=Path)
    parser.add_argument("--width", default=1280, type=int)
    args = parser.parse_args()
    shots = [
        capture(args.site, args.out, args.width, "light", False, "dashboard.png"),
        capture(args.site, args.out, args.width, "light", True, "dashboard-full.png"),
        capture(args.site, args.out, args.width, "dark", False, "dashboard-dark.png"),
        capture(args.site, args.out, 390, "light", False, "dashboard-mobile.png"),
    ]
    for shot in shots:
        print(f"wrote {shot}")


if __name__ == "__main__":
    main()
