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
    """Fetch every year and format combination, keeping successes and failures apart.

    Downloading is separated from reporting and writing so that --dry-run can show
    exactly what would be stored without storing any of it.

    Steps:
        1. Start two lists, one for successful pulls and one for the misses.
        2. Loop over every year in the range, and every scoring format within
           each year.
        3. Fetch that combination through the adapter.
        4. Sort the answer into the successes or the misses. A miss records the
           reason rather than raising, since a season FFC simply has no data for
           is a normal answer here.
        5. Pause between requests, because FFC recomputes once a day and asks not
           to be hammered. This is a one-time job, so there is no reason to rush.

    Args:
        start_year: The first season to pull, included.
        end_year: The last season to pull, included.
        adapter: An `FfcAdapter`, shared so every request reuses one network
            connection.

    Returns:
        tuple: `(results, misses)`. Each entry of `results` is a dictionary with
            `year`, `fmt`, `ffc_format`, and `pull` — where `pull` is an FfcPull
            whose `.players` is the canonical table (ffc_player_id, name,
            position, team, adp, stdev, high, low, times_drafted, bye) and whose
            `.meta` carries total_drafts and the start_date/end_date observation
            window. Each entry of `misses` has `year`, `fmt`, and `reason`.

    Note:
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
    """Print exactly what was fetched, before anything is written.

    The whole point of --dry-run: see the coverage and the row counts, and decide
    whether they look right, before committing any of it to the database.

    Steps:
        1. Print a header row for the table.
        2. Walk the successes sorted by year then format, printing the player
           count, the number of real drafts behind it, and the observation
           window, while accumulating the total.
        3. If anything was skipped, list each miss with its reason.
        4. Print the totals.

    Args:
        results: The successful pulls, the first value from `pull_all` above.
        misses: The failures, the second value from `pull_all`.

    Returns:
        int: The total number of player rows across every successful pull.

    Note:
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
    """Write every successful pull into the snapshot history collection.

    The only step here that changes anything. Called after `report` above, and
    only when --dry-run was not passed.

    Steps:
        1. Walk the successes sorted by year then format, so the output reads in
           order.
        2. Append each pull to the history, tagging it with its source, season,
           format, league size, and observation date.
        3. Turn off the unchanged-payload skip, since every year and format here
           is a genuinely distinct set of rows.
        4. Print how many rows were written, split into newly created and
           updated.

    Args:
        results: The successful pulls, the first value from `pull_all` above.
        repo: The `AdpSnapshotRepo` to write into.

    Returns:
        None: Progress is reported by printing one line per pull.

    Note:
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
    """Fetch the historical range, report it, and write it unless told not to.

    The entry point when this file is run from the command line.

    Steps:
        1. Define the command-line options, using this file's module docstring as
           the help text.
        2. Print how many requests are about to be made and how far apart, since
           this job takes a while on purpose.
        3. Fetch everything with `pull_all` above and print it with `report`.
        4. Stop here if --dry-run was passed, or if nothing came back.
        5. Create the collection's indexes BEFORE writing — see the inline
           comment for why the order matters.
        6. Write with `write` above, then print the resulting coverage so the
           outcome is visible.

    Returns:
        None: Progress is printed as it goes.
    """
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
