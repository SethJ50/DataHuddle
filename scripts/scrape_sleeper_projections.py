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


def fetch_projections(season):
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
