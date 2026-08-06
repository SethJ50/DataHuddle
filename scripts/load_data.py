"""
Clear and reload every MongoDB collection from the CSV files in data/,
including subfolders (e.g. data/ffb/). data/raw/ is skipped — it holds
scratch inputs (saved HTML pages), not data to load.

Each CSV's filename stem (without extension) is used as its collection name,
e.g. data/ffb/ffb_qb_projections.csv -> the 'ffb_qb_projections' collection.

Also pulls the current season's ADP from Fantasy Football Calculator, which is
the app's only source of draft-position spread (`stdev`). Unlike the CSVs, that
comes from a live API rather than a file, so it gets its own step.

Requires the MONGODB_URI environment variable to be set.

Usage:
    python scripts/load_data.py
    python scripts/load_data.py --skip-ffc     # CSVs only, no network
    python scripts/load_data.py --year 2027
"""

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.ffc_adapter import FfcAdapter
from db.loader import reload_collection, reload_collection_from_csv
from registry import Collections
from repositories.adp_snapshot_repo import AdpSnapshotRepo
from scoring import ScoringFormat

DATA_DIR = PROJECT_ROOT / "data"
SKIP_DIRS = {"raw"}

# All three formats are loaded because scoring format is a per-draft setting, and
# FFC returns a genuinely different player pool for each (246/204/186 in 2026).
FFC_FORMATS = (ScoringFormat.REGULAR, ScoringFormat.HALF_PPR, ScoringFormat.FULL_PPR)


def load_csvs():
    """Replace every MongoDB collection that is backed by a CSV file in data/.

    Walks the data folder rather than taking a hardcoded list, so dropping a new
    CSV in is all it takes to get it loaded.

    Steps:
        1. Find every .csv file under data/, including subfolders, sorted so the
           output order is stable between runs.
        2. Look at the folders each file sits in and skip anything under a folder
           named in SKIP_DIRS, which is how data/raw/ stays out.
        3. Use the filename without its extension as the collection name, so
           data/ffb/ffb_qb_projections.csv becomes the 'ffb_qb_projections'
           collection.
        4. Call `reload_collection_from_csv` from db/loader.py, which wipes that
           collection and refills it from the file.
        5. Print how many rows landed.

    Returns:
        None: Progress is reported by printing one line per collection.

    Raises:
        KeyError: From the database layer if MONGODB_URI is not set.
    """
    for csv_path in sorted(DATA_DIR.rglob("*.csv")):
        relative_parts = csv_path.relative_to(DATA_DIR).parts[:-1]
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        collection_name = csv_path.stem
        count = reload_collection_from_csv(collection_name, csv_path)
        print(f"Loaded {count} rows into '{collection_name}'")


def load_ffc(year):
    """Pull this season's FFC ADP and store it two different ways.

    FFC is the app's only source of draft-position spread, and unlike the other
    inputs it comes from a live web service rather than a file, so it gets its
    own step.

    Steps:
        1. Build the adapter and the snapshot repository, and make sure the
           history collection's indexes exist before any bulk write.
        2. For each of the three scoring formats, fetch that format's pool.
        3. Skip a format the fetch could not return, printing why. A network
           problem should not stop the rest of the load.
        4. Tag every row with its format, season, and observation date before the
           formats are combined, since afterwards there would be no way to tell a
           PPR row from a standard one.
        5. Append the same rows to the history collection, which quietly does
           nothing if the data has not changed since last time.
        6. If nothing at all came back, leave the existing collection untouched
           rather than wiping it.
        7. Otherwise combine every format into one table and replace the
           collection with it.

    Args:
        year: Which season to pull.

    Returns:
        int: The total number of player rows fetched across all three formats,
            or 0 when nothing could be fetched.

    Note:
        The two destinations are deliberately NOT the same thing:

        - `ffc_adp` is CURRENT STATE. Wiped and replaced, exactly like the CSV
          collections. This is what the draft model reads.
        - `adp_snapshots` is HISTORY. Appended to, never overwritten. Nothing reads
          it; it exists purely so a record of how ADP moved survives (DESIGN.md 5.5).

        The snapshot append is free and idempotent. Its content-hash guard means
        running load_data five times in one day writes one snapshot, not five --
        and if FFC hasn't recomputed since the last run, zero.

        A failed FFC pull is reported and skipped rather than raised: the network
        being down should not stop your CSVs from loading.
    """
    adapter = FfcAdapter()
    snapshots = AdpSnapshotRepo()
    snapshots.ensure_indexes()

    frames, total = [], 0

    for fmt in FFC_FORMATS:
        pull = adapter.fetch(fmt, year)
        if not pull.ok:
            print(f"  FFC {year} {fmt.value}: skipped -- {pull.error}")
            continue

        # Tag each row with its provenance before the formats get concatenated,
        # otherwise there is no way to tell a PPR row from a standard one.
        players = pull.players.assign(
            format=pull.requested["format"],
            season=year,
            pulled_at=pull.meta.get("end_date"),
        )
        frames.append(players)
        total += len(players)

        outcome = snapshots.append(
            source="ffc", season=year, fmt=pull.requested["format"],
            num_teams=pull.requested["teams"],
            snapshot_date=pull.meta["end_date"],
            players=pull.players, key_column="ffc_player_id", meta=pull.meta,
        )
        archived = "unchanged, not re-archived" if outcome["skipped"] else \
                   f"archived {outcome['written']}"
        print(f"  FFC {year} {fmt.value}: {len(players)} rows  ({archived})")

    if not frames:
        print("  FFC: nothing fetched, leaving 'ffc_adp' as-is")
        return 0

    import pandas as pd
    reload_collection(Collections.FFC_ADP, pd.concat(frames, ignore_index=True))
    print(f"Loaded {total} rows into '{Collections.FFC_ADP}'")
    return total


def main():
    """Run the whole load: CSV files first, then FFC unless told to skip it.

    The entry point when this file is run from the command line.

    Steps:
        1. Set up the command-line arguments, using this file's own module
           docstring as the help text.
        2. Load every CSV with `load_csvs` above.
        3. Unless --skip-ffc was passed, pull FFC ADP with `load_ffc` above.

    Returns:
        None: Progress is printed as it goes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-ffc", action="store_true",
                        help="load CSVs only; make no network calls")
    parser.add_argument("--year", type=int, default=date.today().year,
                        help="season to pull from FFC (default: current year)")
    args = parser.parse_args()

    load_csvs()

    if not args.skip_ffc:
        print(f"\nFantasy Football Calculator ({args.year}):")
        load_ffc(args.year)


if __name__ == "__main__":
    main()
