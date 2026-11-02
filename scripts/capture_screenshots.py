"""Capture README screenshots of every app page from a running Streamlit app.

    streamlit run app/streamlit_app.py           # in one terminal (with real data + saved rosters)
    pip install playwright && playwright install chromium
    python scripts/capture_screenshots.py         # writes docs/screenshots/*.png

Options: --url http://localhost:8501  --theme light|dark  --chromium /path/to/chrome
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {
    "rankings": "",
    "my_team": "my_team",
    "waivers": "waivers",
    "trade": "trade",
    "matchup": "matchup",
    "method": "about",
}
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--theme", choices=["light", "dark"], default="light")
    parser.add_argument("--chromium", default=None, help="Path to a Chromium binary (optional)")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, color_scheme=args.theme)
        for name, path in PAGES.items():
            page.goto(f"{args.url.rstrip('/')}/{path}", wait_until="networkidle")
            page.wait_for_timeout(9000 if name == "matchup" else 4000)  # let charts / the simulation finish
            target = OUT / f"{name}.png"
            page.screenshot(path=str(target), full_page=True)
            print("saved", target)
        browser.close()


if __name__ == "__main__":
    main()
