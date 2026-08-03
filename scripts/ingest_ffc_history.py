"""One-time backfill of historical Fantasy Football Calculator ADP into adp_snapshots.

WHY THIS IS TIME-SENSITIVE
--------------------------
FFC is the only source of draft-position spread (`stdev`) this app has, and it's a
free API with no contract. If it ever goes away, the archive written by this script
is the only raw material left for estimating spread another way. Historical seasons
cannot be collected after the fact -- nobody else keeps them.

Note the archive is INSURANCE ONLY. Nothing in the pipeline reads it, and no model
is planned (draft_model/DESIGN.md 5.5). It exists so the option stays open.

TWO THINGS THAT LOOK LIKE BUGS AND ARE NOT
------------------------------------------
1. 2025 comes back EMPTY -- HTTP 200 with zero players, sitting between populated
   years. That is why this iterates a fixed range and skips gaps rather than
   walking backward until a request fails; a walk-until-failure loop would stop
   at 2025 and silently collect nothing.
2. The `teams` parameter is echoed back but ignored by FFC -- verified identical
   ADP for 8/10/12/14. So this loops FORMATS only. Looping team counts would
   quadruple the requests and the stored rows for zero additional information.

Usage:
    python scripts/ingest_ffc_history.py --dry-run     # look before writing
    python scripts/ingest_ffc_history.py               # actually write
"""

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.ffc_adapter import FfcAdapter
from repositories.adp_snapshot_repo import AdpSnapshotRepo
from scoring import ScoringFormat

# Deliberately narrow (decision 2026-07-30). FFC still serves 2010-2019; those are
# not pulled, on the view that pre-2020 fantasy is a different enough game that
# older rows would mislead more than help.
DEFAULT_START_YEAR = 2020
DEFAULT_END_YEAR = 2024

# The three formats the app can actually express (scoring.ScoringFormat). FFC also
# has "2qb", excluded because it has no ScoringFormat value and no current use.
FORMATS = (ScoringFormat.REGULAR, ScoringFormat.HALF_PPR, ScoringFormat.FULL_PPR)

# FFC recomputes once a day and asks not to be hammered. This is a one-time job --
# there is no reason for it to be fast.
SLEEP_SECONDS = 0.5


def pull_all(start_year, end_year, adapter):
    """
    Purpose:
        Fetch every (year, format) combination in range, keeping the successes and
        recording why each failure failed.

    Parameters:
        start_year (int), end_year (int): Inclusive season range.
        adapter (FfcAdapter): Shares one HTTP session across all requests.

    Returns:
        tuple (results, misses) where
          results -- list of dicts: {year, fmt, ffc_format, pull}. `pull` is an
                     FfcPull whose .players is the canonical DataFrame
                     (ffc_player_id, name, position, team, adp, stdev, high, low,
                     times_drafted, bye) and whose .meta carries total_drafts and
                     the start_date/end_date observation window.
          misses  -- list of dicts: {year, fmt, reason}, for the report.

    Notes:
        Never raises on an absent season. "FFC has no 2025 data" is a normal answer
        when looping years, not an error -- FfcPull.ok collapses both of FFC's
        no-data signals (HTTP 400, and HTTP 200 with an empty list) into one flag.
    """
    results, misses = [], []

    for year in range(start_year, end_year + 1):
        for fmt in FORMATS:
            pull = adapter.fetch(fmt, year)
            if pull.ok:
                results.append({"year": year, "fmt": fmt,
                                "ffc_format": pull.requested["format"], "pull": pull})
            else:
                misses.append({"year": year, "fmt": fmt.value, "reason": pull.error})
            time.sleep(SLEEP_SECONDS)

    return results, misses


def report(results, misses):
    """
    Purpose: Show exactly what was found before anything is written.

    Parameters: pull_all()'s two return values.
    Returns: int -- total player rows across all successful pulls.

    Notes:
        This is the whole point of --dry-run. The observation window is worth
        reading: historical rows are 2-4 day snapshots taken at season start, so
        each season contributes ONE datapoint per format, not a time series.
    """
    print(f"\n{'year':6s} {'format':10s} {'players':>8s} {'drafts':>8s}  window")
    print("-" * 66)

    total_rows = 0
    for row in sorted(results, key=lambda r: (r["year"], r["ffc_format"])):
        meta = row["pull"].meta
        n = len(row["pull"].players)
        total_rows += n
        print(f"{row['year']:<6d} {row['ffc_format']:10s} {n:>8d} "
              f"{meta.get('total_drafts', '?'):>8}  "
              f"{meta.get('start_date')} -> {meta.get('end_date')}")

    if misses:
        print(f"\nskipped ({len(misses)}):")
        for miss in misses:
            print(f"  {miss['year']} {miss['fmt']:10s} -- {miss['reason']}")

    print(f"\n  {len(results)} successful pulls, {total_rows:,} player rows total")
    return total_rows


def write(results, repo):
    """
    Purpose: Persist every successful pull into adp_snapshots.

    Parameters:
        results (list[dict]): From pull_all().
        repo (AdpSnapshotRepo): Target repository.

    Returns: None. Prints one line per pull.

    Notes:
        Safe to re-run. Each row is upserted on
        (source, season, format, num_teams, player_key, snapshot_date), so a second
        run updates in place rather than duplicating.

        skip_if_unchanged is OFF here. That guard exists to stop an unrefreshed CSV
        from faking a new observation; for a historical backfill each (year, format)
        is a genuinely distinct row set, and leaving it on would make a re-run
        confusingly skip everything.

        snapshot_date is meta.end_date -- the last day of drafts feeding that ADP,
        i.e. the honest "as of" date. The season year alone would be wrong: 2024's
        data describes Aug 31 - Sep 1, not the whole year.
    """
    for row in sorted(results, key=lambda r: (r["year"], r["ffc_format"])):
        pull = row["pull"]
        outcome = repo.append(
            source="ffc",
            season=row["year"],
            fmt=row["ffc_format"],
            num_teams=pull.requested["teams"],
            snapshot_date=pull.meta["end_date"],
            players=pull.players,
            key_column="ffc_player_id",
            meta=pull.meta,
            skip_if_unchanged=False,
        )
        counts = outcome.get("counts", {})
        print(f"  {row['year']} {row['ffc_format']:10s} "
              f"wrote {outcome['written']:>4d}  "
              f"(new {counts.get('upserted', 0)}, updated {counts.get('matched', 0)})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report coverage and row counts, write nothing")
    parser.add_argument("--start-year", type=int, default=DEFAULT_START_YEAR)
    parser.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    args = parser.parse_args()

    years = args.end_year - args.start_year + 1
    print(f"Fetching FFC {args.start_year}-{args.end_year} x {len(FORMATS)} formats "
          f"= {years * len(FORMATS)} requests, ~{SLEEP_SECONDS}s apart")

    results, misses = pull_all(args.start_year, args.end_year, FfcAdapter())
    report(results, misses)

    if args.dry_run:
        print("\n  --dry-run: nothing written.")
        return

    if not results:
        print("\n  nothing to write.")
        return

    repo = AdpSnapshotRepo()
    # Index first, not after: every upsert has to FIND its target, and without the
    # index that's a scan of a collection which is itself growing as we write.
    repo.ensure_indexes()

    print("\nwriting:")
    write(results, repo)

    print("\ncoverage now in adp_snapshots:")
    print(repo.coverage().to_string(index=False))


if __name__ == "__main__":
    main()
