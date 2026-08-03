"""Validate hand-placed data files BEFORE loading them into Mongo.

Manual CSV exports are the most error-prone input this app has: a renamed column,
a reordered export, or a file in the wrong folder all load without complaint and
then produce quietly wrong numbers downstream.

The check that matters most is COLUMN ORDER. The Fantasy Footballers projection
exports contain DUPLICATE headers -- `YDS` and `TDS` each appear twice, once for
rushing and once for receiving. pandas silently renames the second occurrence to
`YDS.1`/`TDS.1`, and the adapter maps them BY POSITION. So a file whose columns
are correct but reordered will load fine and attribute receiving yards to
rushing, with nothing visibly wrong.

Usage:
    python scripts/check_data_files.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

DATA_DIR = PROJECT_ROOT / "data"
FFB_DIR = DATA_DIR / "ffb"

ANALYSTS = ("andy", "mike", "jason")

# Exact expected header ORDER. Duplicates are intentional -- see module docstring.
QB_COLUMNS = ["Name", "Team", "Bye Week", "Rank", "PPG",
              "YDS", "TDS", "YDS", "TDS", "INT", "FUM"]
FLEX_COLUMNS = ["Name", "Team", "Bye Week", "Pos", "Rank", "PPG",
                "ATTS", "YDS", "TDS", "REC", "YDS", "TDS", "FUM"]
UDK_COLUMNS = ["Name", "Position", "Team", "Bye Week", "Rank", "Points",
               "Risk", "Upside", "ADP", "Tier", "Outlook", "Dynasty", "Markers"]

# K and DST are a genuinely different export: UDK publishes RANKINGS for them, not
# projections. There is a consensus Rank plus each analyst's own rank, and no
# points/risk/upside/ADP at all -- so no VORP is possible for these positions
# regardless of what else changes.
UDK_KDST_COLUMNS = ["Name", "Position", "Team", "Bye Week", "Rank",
                    "Andy", "Jason", "Mike", "Markers"]


def raw_header(path):
    """The header line exactly as written, before pandas de-duplicates it."""
    with open(path, encoding="utf-8-sig") as handle:
        return [c.strip().strip('"') for c in handle.readline().strip().split(",")]


def check(path, expected, required=True):
    """
    Purpose: Verify one CSV exists and has exactly the expected header order.

    Parameters:
        path (Path): File to check.
        expected (list[str]): Header names in order. May contain duplicates.
        required (bool): If False, a missing file is reported as optional.

    Returns:
        bool: True if the file is present and correct.
    """
    label = path.relative_to(PROJECT_ROOT)

    if not path.exists():
        print(f"  {'MISSING ' if required else 'optional'}  {label}")
        return not required

    header = raw_header(path)
    if header != expected:
        print(f"  BAD       {label}")
        if sorted(header) == sorted(expected):
            # The dangerous case: right columns, wrong order.
            print(f"            columns are right but ORDER differs -- this loads "
                  f"without error and mis-assigns stats")
        print(f"            expected: {expected}")
        print(f"            found   : {header}")
        return False

    rows = len(pd.read_csv(path))
    print(f"  ok        {label}  ({rows} rows)")
    return True


def main():
    print("Fantasy Footballers projections (one QB + one flex file per analyst):")
    ffb_ok = True
    for analyst in ANALYSTS:
        ffb_ok &= check(FFB_DIR / f"ffb_qb_projections_{analyst}.csv", QB_COLUMNS)
        ffb_ok &= check(FFB_DIR / f"ffb_flex_projections_{analyst}.csv", FLEX_COLUMNS)

    print("\nUDK rankings (K and DST are optional -- the model works without them):")
    for position in ("qb", "rb", "wr", "te"):
        check(FFB_DIR / f"udk_{position}_rankings_ppr.csv", UDK_COLUMNS)
    for position in ("k", "dst"):
        check(FFB_DIR / f"udk_{position}_rankings_ppr.csv", UDK_KDST_COLUMNS, required=False)

    print("\nEvery CSV load_data will pick up, and the collection each becomes:")
    for csv_path in sorted(DATA_DIR.rglob("*.csv")):
        if "raw" in csv_path.relative_to(DATA_DIR).parts[:-1]:
            continue
        print(f"  {csv_path.relative_to(DATA_DIR)}  ->  {csv_path.stem}")

    if not ffb_ok:
        print("\nSome required files are missing or malformed. Fix those before running "
              "scripts/load_data.py -- a bad load overwrites the good collection.")


if __name__ == "__main__":
    main()
