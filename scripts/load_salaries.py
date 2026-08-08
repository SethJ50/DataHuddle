"""Load a week's Daily Fantasy salary exports into the database.

Run this each week after downloading the two CSVs from the sites. It works out
which week the slate is for, attaches player ids to the names, and stores the
result alongside every week already loaded.

Which week a file belongs to is DERIVED rather than typed: the DraftKings export
carries a kickoff date, and matching that against the NFL schedule gives the
season and week exactly. FanDuel's export has no date at all, so it inherits the
same answer. Pass --season and --week to override, which is what you need if
DraftKings changes its format or you only have the FanDuel file.

Requires the MONGODB_URI environment variable to be set.

Usage:
    python scripts/load_salaries.py
    python scripts/load_salaries.py --season 2026 --week 2
    python scripts/load_salaries.py --dir data/dfs --dry-run
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from adapters.dfs_salary_adapter import (
    build_name_lookup, read_draftkings, read_fanduel,
)
from repositories.dfs_salary_repo import DfsSalaryRepo

DEFAULT_DIR = PROJECT_ROOT / "data" / "dfs"
FANDUEL_FILE = "FDSalaries.csv"
DRAFTKINGS_FILE = "DKSalaries.csv"


def kickoff_from_draftkings(path):
    """Read the slate's kickoff date out of a DraftKings export.

    Steps:
        1. Read the file and take its `Game Info` column, which reads like
           "NO@DET 09/13/2026 01:00PM ET".
        2. Pull the date out of the middle of that.
        3. Return the earliest one, since a slate's week is the week its first
           game falls in.

    Args:
        path: The DraftKings CSV.

    Returns:
        pd.Timestamp: The earliest kickoff, or None if no date could be read.
    """
    raw = pd.read_csv(path, encoding="utf-8-sig")
    if "Game Info" not in raw.columns:
        return None

    dates = raw["Game Info"].astype(str).str.extract(r"(\d{2}/\d{2}/\d{4})")[0]
    parsed = pd.to_datetime(dates, format="%m/%d/%Y", errors="coerce")
    return None if parsed.isna().all() else parsed.min()


def week_from_schedule(kickoff, seasons):
    """Find which season and week a kickoff date belongs to.

    Steps:
        1. Pull the schedule for the seasons given.
        2. Find the games played on that date.
        3. Return the season and week they belong to.

    Args:
        kickoff: The date to look up.
        seasons: Seasons to search, as a list of years.

    Returns:
        tuple: `(season, week)`, or `(None, None)` if no game was played then.

    Note:
        Matching on the DATE rather than counting weeks forward from an assumed
        opener. Weeks do not have fixed dates -- the season starts on a different
        day each year and a game can be moved -- so the schedule is the only
        thing that actually knows.
    """
    import nflreadpy as nfl

    games = nfl.load_schedules(seasons).to_pandas()
    if "gameday" not in games.columns:
        return None, None

    played = pd.to_datetime(games["gameday"], errors="coerce")
    same_day = games[played.dt.date == pd.Timestamp(kickoff).date()]

    if same_day.empty:
        return None, None
    return int(same_day["season"].iloc[0]), int(same_day["week"].iloc[0])


def load(directory, season=None, week=None, dry_run=False):
    """Read whichever salary files are present and store them.

    Steps:
        1. Work out the season and week, from the DraftKings date unless they
           were given.
        2. Build the name-to-id lookup from nflreadpy's player reference.
        3. Read each file that exists.
        4. Report what could not be matched to a player, which is the part worth
           looking at each week.
        5. Store both slates, unless this is a dry run.

    Args:
        directory: Where the CSVs are.
        season: Override the derived season.
        week: Override the derived week.
        dry_run: Read and report, but write nothing.

    Returns:
        int: 0 if everything worked, 1 if it could not proceed.
    """
    import nflreadpy as nfl

    directory = Path(directory)
    fanduel_path = directory / FANDUEL_FILE
    draftkings_path = directory / DRAFTKINGS_FILE

    if not fanduel_path.exists() and not draftkings_path.exists():
        print(f"No salary files in {directory}. Expected {FANDUEL_FILE} "
              f"and/or {DRAFTKINGS_FILE}.")
        return 1

    # ---- which week ----
    if season is None or week is None:
        if not draftkings_path.exists():
            print("Cannot work out the week: that comes from the DraftKings "
                  "file, which is not here. Pass --season and --week.")
            return 1

        kickoff = kickoff_from_draftkings(draftkings_path)
        if kickoff is None:
            print("No kickoff date in the DraftKings file. Pass --season and "
                  "--week.")
            return 1

        found_season, found_week = week_from_schedule(
            kickoff, [kickoff.year, kickoff.year - 1])
        if found_season is None:
            print(f"No NFL game on {kickoff.date()}, so the week cannot be "
                  f"derived. Pass --season and --week.")
            return 1

        season = season if season is not None else found_season
        week = week if week is not None else found_week
        print(f"Derived from kickoff {kickoff.date()}: "
              f"season {season}, week {week}")
    else:
        print(f"Using the season and week given: {season}, week {week}")

    # ---- read ----
    lookup = build_name_lookup(nfl.load_players().to_pandas())
    slates = []

    for path, reader, label in ((fanduel_path, read_fanduel, "FanDuel"),
                                (draftkings_path, read_draftkings, "DraftKings")):
        if not path.exists():
            print(f"  {label}: no file, skipped")
            continue

        slate = reader(path, season, week, lookup)
        slates.append(slate)

        skill = slate[slate["position"] != "DST"]
        unmatched = skill[skill["canonical_id"].isna()]
        print(f"  {label}: {len(slate)} rows, "
              f"{len(slate) - len(skill)} defences, "
              f"{len(skill) - len(unmatched)}/{len(skill)} players matched "
              f"({100 * (1 - len(unmatched) / max(len(skill), 1)):.1f}%)")

        if not unmatched.empty:
            # Worth reading every week: these are rosterable players whose
            # statistics will show as blank until somebody maps them.
            names = ", ".join(sorted(unmatched["name"])[:12])
            print(f"    unmatched: {names}"
                  + (f" … and {len(unmatched) - 12} more"
                     if len(unmatched) > 12 else ""))

    if not slates:
        return 1

    if dry_run:
        print("\nDry run, nothing written.")
        return 0

    repo = DfsSalaryRepo()
    repo.ensure_indexes()
    written = sum(repo.save_slate(slate) for slate in slates)
    print(f"\nStored {written} rows for season {season}, week {week}.")
    return 0


def main():
    """Read the command line and run the load."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=str(DEFAULT_DIR),
                        help="Folder holding the CSVs.")
    parser.add_argument("--season", type=int, default=None,
                        help="Override the derived season.")
    parser.add_argument("--week", type=int, default=None,
                        help="Override the derived week.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Read and report without writing.")
    args = parser.parse_args()

    return load(args.dir, args.season, args.week, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
