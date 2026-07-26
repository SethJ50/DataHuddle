"""
Parse a saved copy of Yahoo's Draft Analysis page into a CSV.

Yahoo renders this table with JavaScript behind a login, so it can't be
fetched directly. Instead, save the rendered page from your logged-in browser:

    1. Open https://football.fantasysports.yahoo.com/f1/draftanalysis
       (set the position filter to ALL and scroll so all rows are loaded)
    2. Open DevTools (Cmd+Option+I) -> Elements tab
    3. Right-click the <html> element at the top -> Copy -> Copy outerHTML
    4. Paste into a file saved as data/raw/yahoo_draftanalysis.html

Then run:
    python scripts/parse_yahoo_draftanalysis.py

Writes data/yahoo_draftanalysis.csv, so `python scripts/load_data.py` will
pick it up as the 'yahoo_draftanalysis' collection.

Note: Yahoo's ADP here is based on standard scoring. Columns locked behind
Yahoo Fantasy Plus (Pos Rank, CER, Last 7 Days, Plus ADP) are left blank.
"""

import argparse
import csv
import sys
from pathlib import Path

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_INPUT = DATA_DIR / "raw" / "yahoo_draftanalysis.html"

CSV_COLUMNS = [
    "yahoo_rank",
    "name",
    "team",
    "position",
    "percent_drafted",
    "preseason_adp",
    "adp",
]

# td order within each table row (0 is the player cell)
CELL_INDEXES = {
    "yahoo_rank": 1,
    "percent_drafted": 4,
    "preseason_adp": 5,
    "adp": 6,  # "All Drafts" ADP
}


def cell_value(cells, index):
    """Return the numeric text of a cell, or None if missing/locked (Plus-only
    cells contain a lock icon link instead of text)."""
    if index >= len(cells):
        return None
    text = cells[index].get_text(strip=True).replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_rows(soup):
    rows = []
    for tr in soup.select('tr[data-tst^="table-row"]'):
        name_div = tr.select_one('[data-tst="player-name"]')
        pos_span = tr.select_one('[data-tst="player-position"]')
        if name_div is None:
            continue

        # Team appears as e.g. "Atl - RB" in the div containing the position span
        team = None
        if pos_span is not None and pos_span.parent is not None:
            team_text = pos_span.parent.get_text(strip=True)
            team = team_text.split("-")[0].strip().upper() or None

        cells = tr.find_all("td")
        row = {
            "name": name_div.get_text(strip=True),
            "team": team,
            "position": pos_span.get_text(strip=True) if pos_span else None,
        }
        for col, index in CELL_INDEXES.items():
            row[col] = cell_value(cells, index)
        if row["yahoo_rank"] is not None:
            row["yahoo_rank"] = int(row["yahoo_rank"])
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "html_path",
        nargs="?",
        default=str(DEFAULT_INPUT),
        help=f"Path to the saved page HTML (default: {DEFAULT_INPUT})",
    )
    args = parser.parse_args()

    html_path = Path(args.html_path)
    if not html_path.exists():
        sys.exit(
            f"{html_path} not found.\n"
            "Save the rendered page there first — see the instructions at the "
            "top of this script (docstring) for the DevTools copy steps."
        )

    soup = BeautifulSoup(html_path.read_text(), "lxml")
    rows = parse_rows(soup)
    if not rows:
        sys.exit(
            "No table rows found in the HTML. Make sure you copied the "
            "rendered DOM (DevTools -> Copy outerHTML), not the page source."
        )

    rows.sort(key=lambda r: r["yahoo_rank"] if r["yahoo_rank"] else float("inf"))

    out_path = DATA_DIR / "yahoo_draftanalysis.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} players to {out_path}")


if __name__ == "__main__":
    main()