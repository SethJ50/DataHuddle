"""
Scrape Sleeper fantasy football season projections into a CSV.

Pulls from Sleeper's projections API (the same JSON API the Sleeper app uses).
No authentication required.

Writes data/sleeper_projections.csv, so the existing `python scripts/load_data.py`
flow will pick it up as the 'sleeper_projections' collection.

Usage:
    python scripts/scrape_sleeper_projections.py [--season 2026]
"""

import argparse
import csv
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scoring

DATA_DIR = PROJECT_ROOT / "data"

API_URL = "https://api.sleeper.app/projections/nfl/{season}"

POSITIONS = ["QB", "RB", "WR", "TE"]

# Sleeper stat keys -> our column names (aligned with espn_projections.csv;
# Sleeper does not project targets, so that column is absent here)
STAT_KEYS = {
    "pass_att": "pass_attempts",
    "pass_cmp": "pass_completions",
    "pass_yd": "pass_yards",
    "pass_td": "pass_tds",
    "pass_int": "interceptions",
    "rush_att": "rush_attempts",
    "rush_yd": "rush_yards",
    "rush_td": "rush_tds",
    "rec": "receptions",
    "rec_yd": "rec_yards",
    "rec_td": "rec_tds",
    "fum_lost": "fumbles_lost",
}

FANTASY_POINT_COLUMNS = [
    "half_ppr_season",
    "half_ppr_per_game",
    "full_ppr_season",
    "full_ppr_per_game",
]

CSV_COLUMNS = (
    ["name", "team", "position", "half_ppr_adp", "full_ppr_adp", "projected_fantasy_points"]
    + FANTASY_POINT_COLUMNS
    + list(STAT_KEYS.values())
)

def _scoring_stats(stat_values):
    """Rename this script's stat columns to the keys scoring.py expects.

    A small translation layer, matching the one in
    scripts/scrape_espn_projections.py. This file names its columns after
    Sleeper's vocabulary, while scoring.py uses the app's, and the scoring rules
    must not be duplicated just to accommodate that.

    Steps:
        1. Build a new dictionary, reading each value under this script's column
           name and storing it under the app's canonical name.

    Args:
        stat_values: The stats keyed by this script's own column names, as built
            from STAT_KEYS.

    Returns:
        dict: The same numbers keyed by the names in `scoring.STAT_KEYS`, ready
            to hand to `scoring.fantasy_points`.

    Raises:
        KeyError: If any expected stat is missing. Callers default absent stats
            to 0 before calling, so this should not happen in practice.
    """
    return {
        "passing_yards": stat_values["pass_yards"],
        "passing_tds": stat_values["pass_tds"],
        "interceptions": stat_values["interceptions"],
        "rushing_yards": stat_values["rush_yards"],
        "rushing_tds": stat_values["rush_tds"],
        "receiving_yards": stat_values["rec_yards"],
        "receiving_tds": stat_values["rec_tds"],
        "receptions": stat_values["receptions"],
        "fumbles_lost": stat_values["fumbles_lost"],
    }


def fetch_projections(season):
    """Download the raw projection records from Sleeper's public API.

    This is the same JSON API the Sleeper app itself uses, and it needs no
    login, which makes it far more reliable than scraping a web page.

    Steps:
        1. Build the query: regular-season projections, ordered by half-PPR ADP.
        2. Add one "position[]" entry per position, which is how this API takes a
           list of values.
        3. Send the request and raise if the response was an error status.
        4. Return the parsed JSON body.

    Args:
        season: Which season's projections to fetch.

    Returns:
        list: One record per player, in Sleeper's own shape. Each has a "player"
            key holding the name, team, and eligible positions, and a "stats" key
            holding the projected numbers and ADP.

    Raises:
        requests.exceptions.HTTPError: If Sleeper returns an error status.
    """
    params = [("season_type", "regular"), ("order_by", "adp_half_ppr")]
    params += [("position[]", pos) for pos in POSITIONS]
    response = requests.get(
        API_URL.format(season=season),
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def build_rows(entries):
    """Turn Sleeper's records into flat rows ready to write as CSV.

    The counterpart to `build_rows` in scripts/scrape_espn_projections.py, and
    deliberately producing the same columns so the two platforms' files line up.

    Steps:
        1. Walk every record, pulling out its stats and player details and
           treating either being absent as empty.
        2. Skip players with no half-PPR point total, which is how Sleeper
           represents someone it has not actually projected.
        3. Choose the player's position from his eligible list, skipping anyone
           who does not play one this script handles.
        4. Build the identifying fields, joining the first and last name and
           defaulting a player with no team to "FA" for free agent.
        5. Copy each projected stat across under this script's column names,
           defaulting a missing stat to 0.
        6. Convert those to the app's vocabulary with `_scoring_stats` above and
           compute half-PPR and full-PPR points through
           `scoring.fantasy_points`, plus the per-game figures.

    Args:
        entries: The raw records from `fetch_projections` above.

    Returns:
        list: One dictionary per player, with exactly the keys listed in
            CSV_COLUMNS. Unprojected players and unhandled positions are left
            out, so this is usually shorter than the input.

    Note:
        Unlike ESPN, Sleeper publishes genuinely different ADP for half-PPR and
        full-PPR, so the two ADP columns here really do differ.
    """
    rows = []
    for entry in entries:
        stats = entry.get("stats") or {}
        player = entry.get("player") or {}

        # Skip players Sleeper hasn't actually projected
        if not stats.get("pts_half_ppr"):
            continue

        positions = player.get("fantasy_positions") or []
        position = next((p for p in positions if p in POSITIONS), None)
        if position is None:
            continue

        row = {
            "name": f"{player.get('first_name', '')} {player.get('last_name', '')}".strip(),
            "team": player.get("team") or "FA",
            "position": position,
            "half_ppr_adp": round(stats.get("adp_half_ppr", 0), 1),
            "full_ppr_adp": round(stats.get("adp_ppr", 0), 1),
            "projected_fantasy_points": round(stats.get("pts_ppr", 0), 1),
        }

        stat_values = {col: stats.get(key, 0) for key, col in STAT_KEYS.items()}
        for col, value in stat_values.items():
            row[col] = round(value, 1)

        scoring_stats = _scoring_stats(stat_values)
        half_ppr = scoring.fantasy_points(scoring_stats, scoring.ScoringFormat.HALF_PPR)
        full_ppr = scoring.fantasy_points(scoring_stats, scoring.ScoringFormat.FULL_PPR)
        row["half_ppr_season"] = round(half_ppr, 1)
        row["half_ppr_per_game"] = round(half_ppr / scoring.GAMES_PER_SEASON, 1)
        row["full_ppr_season"] = round(full_ppr, 1)
        row["full_ppr_per_game"] = round(full_ppr / scoring.GAMES_PER_SEASON, 1)
        rows.append(row)
    return rows


def main():
    """Fetch Sleeper's projections and write them to a CSV in data/.

    The entry point when this file is run from the command line. The output file
    is named so that `python scripts/load_data.py` picks it up automatically as
    the 'sleeper_projections' collection.

    Steps:
        1. Define the command-line options, using this file's module docstring as
           the help text.
        2. Download the raw records with `fetch_projections` above and flatten
           them with `build_rows`.
        3. Exit with a message if nothing came back, rather than writing an empty
           file over a good one.
        4. Sort by half-PPR ADP, treating an ADP of 0 as infinity so unranked
           players sink to the bottom instead of appearing to be the top picks.
        5. Write the rows to data/sleeper_projections.csv using CSV_COLUMNS as
           the header, which also fixes the column order.

    Returns:
        None: The row count and destination are printed on success.

    Raises:
        SystemExit: If no projection data was found for the requested season.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    args = parser.parse_args()

    entries = fetch_projections(args.season)
    rows = build_rows(entries)

    if not rows:
        sys.exit(f"No projection data found for season {args.season}")

    rows.sort(key=lambda r: r["half_ppr_adp"] if r["half_ppr_adp"] > 0 else float("inf"))

    out_path = DATA_DIR / "sleeper_projections.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} players to {out_path}")


if __name__ == "__main__":
    main()
