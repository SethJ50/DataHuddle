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
    """Read one table cell as a number, returning None whenever that is not possible.

    Yahoo's table has three separate ways a cell can fail to hold a number: the
    row can be short, the cell can be empty, or it can be locked behind Yahoo
    Fantasy Plus and contain a padlock icon instead of text. All three mean the
    same thing here, so all three give None.

    Steps:
        1. Return None if the row does not have that many cells.
        2. Take the cell's visible text, trimmed, with any percent sign removed.
        3. Return None if that text is empty.
        4. Try to read it as a number, returning None if it is not one — which is
           what happens for the locked cells.

    Args:
        cells: The table cells for one row, in document order.
        index: Which cell to read, counting from 0.

    Returns:
        float | None: The cell's number, or None when it is missing, empty, or
            locked. Callers must handle None; it is normal, not an error.
    """
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
    """Pull one record per player out of the saved Yahoo page.

    Yahoo builds this table with JavaScript behind a login, so the input is a
    copy of the rendered page saved by hand rather than anything fetched. This
    walks that HTML and extracts the columns worth keeping.

    Steps:
        1. Find every table row, matched on Yahoo's own `data-tst` marker rather
           than on CSS classes, which change far more often.
        2. Locate the player name and position elements, skipping any row with no
           name — that filters out header and spacer rows.
        3. Work the team out of the text around the position, which reads like
           "Atl - RB", by taking the part before the dash and uppercasing it.
        4. Read each numeric column by its fixed cell position, using
           `cell_value` above so missing and locked cells become None.
        5. Convert the rank to a whole number when it is present, since a rank of
           "12.0" would be odd.

    Args:
        soup: The parsed HTML of the saved page, as a BeautifulSoup object.

    Returns:
        list: One dictionary per player with the keys listed in CSV_COLUMNS. Any
            value can be None where Yahoo did not supply it. An empty list means
            the page source was copied instead of the rendered DOM.
    """
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
    """Parse the saved Yahoo page and write the results to a CSV in data/.

    The entry point when this file is run from the command line. See the module
    docstring at the top of this file for how to save the page in the first
    place.

    Steps:
        1. Define the command-line options, using this file's module docstring as
           the help text. The input path is optional and defaults to the usual
           location.
        2. Exit with instructions if that file is not there.
        3. Parse the HTML and extract the rows with `parse_rows` above.
        4. Exit with a hint if no rows were found — that almost always means the
           page source was copied instead of the rendered DOM.
        5. Sort by Yahoo's rank, treating a missing rank as infinity so those
           players sink to the bottom.
        6. Write the rows to data/yahoo_draftanalysis.csv using CSV_COLUMNS as
           the header, which also fixes the column order.

    Returns:
        None: The row count and destination are printed on success.

    Raises:
        SystemExit: If the input file is missing, or if it contained no rows.
            Both print instructions for fixing it.
    """
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