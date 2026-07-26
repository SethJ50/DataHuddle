"""
Scrape ESPN fantasy football season projections into a CSV.

The ESPN projections page (fantasy.espn.com/football/players/projections) is a
JavaScript-rendered app, so instead of scraping HTML this pulls from the same
JSON API the page itself uses.

Writes data/espn_projections.csv, so the existing `python scripts/load_data.py`
flow will pick it up as the 'espn_projections' collection.

Usage:
    python scripts/scrape_espn_projections.py [--season 2026] [--limit 400]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scoring

DATA_DIR = PROJECT_ROOT / "data"

API_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
    "seasons/{season}/segments/0/leaguedefaults/3"
)

POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}

PRO_TEAMS = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG",
    20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

# ESPN's numeric stat IDs within a stat entry's `stats` dict
STAT_IDS = {
    "0": "pass_attempts",
    "1": "pass_completions",
    "3": "pass_yards",
    "4": "pass_tds",
    "20": "interceptions",
    "23": "rush_attempts",
    "24": "rush_yards",
    "25": "rush_tds",
    "58": "targets",
    "53": "receptions",
    "42": "rec_yards",
    "43": "rec_tds",
    "72": "fumbles_lost",
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
    + list(STAT_IDS.values())
)

def _scoring_stats(stat_values):
    """Map this script's stat column names to scoring.py's canonical keys."""
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


def fetch_players(season, limit):
    fantasy_filter = {
        "players": {
            "filterSlotIds": {"value": [0, 2, 4, 6]},  # QB, RB, WR, TE slots
            "limit": limit,
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
        }
    }
    response = requests.get(
        API_URL.format(season=season),
        params={"view": "kona_player_info"},
        headers={
            "Accept": "application/json",
            "X-Fantasy-Filter": json.dumps(fantasy_filter),
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["players"]


def season_projection_entry(player, season):
    for entry in player.get("stats", []):
        if (
            entry.get("seasonId") == season
            and entry.get("statSourceId") == 1  # projected (0 = actual)
            and entry.get("statSplitTypeId") == 0  # full season
        ):
            return entry
    return None


def build_rows(players, season):
    rows = []
    for wrapper in players:
        player = wrapper.get("player", {})
        position = POSITION_IDS.get(player.get("defaultPositionId"))
        if position is None:
            continue

        projection = season_projection_entry(player, season)
        if projection is None:
            continue

        # ESPN publishes a single ADP aggregated across all its drafts, with no
        # scoring-format split (ESPN's default scoring is full PPR). Both ADP
        # columns therefore carry the same value here, unlike Sleeper's.
        adp = round(player.get("ownership", {}).get("averageDraftPosition", 0), 1)
        row = {
            "name": player.get("fullName"),
            "team": PRO_TEAMS.get(player.get("proTeamId"), "UNK"),
            "position": position,
            "half_ppr_adp": adp,
            "full_ppr_adp": adp,
            "projected_fantasy_points": round(projection.get("appliedTotal", 0), 1),
        }
        stats = projection.get("stats", {})
        for stat_id, col in STAT_IDS.items():
            row[col] = round(stats.get(stat_id, 0), 1)

        stat_values = {col: stats.get(stat_id, 0) for stat_id, col in STAT_IDS.items()}
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--limit", type=int, default=400, help="Max players to fetch")
    args = parser.parse_args()

    players = fetch_players(args.season, args.limit)
    rows = build_rows(players, args.season)

    if not rows:
        sys.exit(f"No projection data found for season {args.season}")

    out_path = DATA_DIR / "espn_projections.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} players to {out_path}")


if __name__ == "__main__":
    main()