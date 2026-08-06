"""Turns expected-points data into the small tables the DFS pages plot.

"Expected points" is what a player's opportunities were worth on average,
whatever actually happened to them. A target at the one-yard line is worth more
than one at midfield whether or not it was caught, and a back who gets the ball
inside the ten every week is being set up to score whether or not he has yet.

Comparing that to what a player really scored is the most useful single thing
this data supports: it separates players who are producing because of their role
from players who are producing in spite of it, and the second kind stop.

Everything here reads `DfsReadRepo.ff_opportunity` and hands back something small
enough to draw.
"""

import pandas as pd

from services.dfs_scoring import DfsScoring, rescore

SPLITS = {
    "Total": "total",
    "Rushing": "rush",
    "Receiving": "rec",
}
"""How a player's points can be broken down, as shown to the reader.

The values are the prefixes `ff_opportunity` uses on its own columns, so
`"Rushing"` reads `rush_fantasy_points` against `rush_fantasy_points_exp`.

Taken straight from the source rather than added up here. `Total` really is the
source's own total, which for a receiver or a back is rushing plus receiving, and
for a quarterback also includes passing -- so selecting Total never quietly drops
part of what somebody did.
"""


def actual_vs_expected(repo, season, weeks=None, positions=None, split="Total",
                       scoring=DfsScoring.FANDUEL, minimum_games=1):
    """Compare what each player scored against what his chances were worth.

    The table behind the app's first Daily Fantasy plot. One row per player,
    covering whatever stretch of the season was asked for.

    Steps:
        1. Read the weekly expected-points table from the repository.
        2. Restate it in the chosen scoring with `rescore` from
           services/dfs_scoring.py, since the source arrives in full PPR and
           FanDuel is not full PPR.
        3. Keep the chosen season, week range and positions.
        4. Add up each player's actual and expected points across those weeks,
           and count how many games he appears in.
        5. Drop anyone below the minimum number of games -- see the note.
        6. Work out per-game averages and the gap between the two, which is the
           number the whole comparison exists to show.

    Args:
        repo: A `DfsReadRepo`, for the weekly expected-points table.
        season: Which season, as a year such as 2025.
        weeks: A `(first, last)` pair, both included. None means the whole
            season.
        positions: Which positions to keep, such as `["RB", "WR", "TE"]`. None
            keeps everyone.
        split: Which part of a player's game to measure -- a key of `SPLITS`
            above.
        scoring: Which scoring system to report in. Defaults to FanDuel.
        minimum_games: Drop players with fewer games than this.

    Returns:
        pd.DataFrame: One row per player, sorted by the gap between actual and
            expected, best first. Columns are `player_id`, `name`, `position`,
            `team`, `games`, `actual`, `expected`, `actual_per_game`,
            `expected_per_game` and `gap_per_game`. Empty, with those columns
            still present, if nothing matched -- so a caller can draw it without
            a special case.

    Raises:
        KeyError: If `split` is not one of `SPLITS`.

    Note:
        THE MINIMUM-GAMES FILTER IS NOT COSMETIC. Over a long week range a player
        with one good game sits further from the line than anyone who played all
        season, purely because one game is a small sample. Without a floor those
        players occupy the interesting corners of the chart and crowd out the
        ones the chart is for.

        PER-GAME IS THE HONEST COMPARISON and is what the gap is built from.
        Totals reward whoever played most, which is a fact about availability
        rather than about whether somebody is outperforming his opportunities.
        The totals are returned as well, because they are worth seeing on hover.
    """
    if split not in SPLITS:
        raise KeyError(f"unknown split {split!r}; expected one of {list(SPLITS)}")

    prefix = SPLITS[split]
    actual_column = f"{prefix}_fantasy_points"
    expected_column = f"{actual_column}_exp"

    frame = rescore(repo.ff_opportunity(), scoring)
    frame = frame[frame["season"] == season]

    if weeks is not None:
        first, last = weeks
        frame = frame[frame["week"].between(first, last)]
    if positions is not None:
        frame = frame[frame["position"].isin(list(positions))]

    columns = ["player_id", "name", "position", "team", "games", "actual",
               "expected", "actual_per_game", "expected_per_game", "gap_per_game"]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    totals = (
        frame.groupby("player_id", as_index=False)
        .agg(
            # `last` rather than `first` so a traded player shows the team he is
            # on now, which is the one that matters for picking him.
            name=("full_name", "last"),
            position=("position", "last"),
            team=("posteam", "last"),
            games=("week", "nunique"),
            actual=(actual_column, "sum"),
            expected=(expected_column, "sum"),
        )
    )

    totals = totals[totals["games"] >= minimum_games]
    if totals.empty:
        return pd.DataFrame(columns=columns)

    totals["actual_per_game"] = totals["actual"] / totals["games"]
    totals["expected_per_game"] = totals["expected"] / totals["games"]
    totals["gap_per_game"] = (totals["actual_per_game"]
                              - totals["expected_per_game"])

    return (totals[columns]
            .sort_values("gap_per_game", ascending=False)
            .reset_index(drop=True))


def week_range(repo, season):
    """Find which weeks a season actually has data for.

    Exists so the week filter offers real weeks rather than a hard-coded 1 to 18.
    A season in progress has fewer, and a completed one runs past 18 into the
    playoffs.

    Steps:
        1. Read the weekly table and keep the season asked for.
        2. Return its lowest and highest week numbers.

    Args:
        repo: A `DfsReadRepo`.
        season: Which season, as a year.

    Returns:
        tuple: `(first, last)` as whole numbers. Falls back to `(1, 18)` if the
            season has no data at all, so a slider can still be drawn.
    """
    weeks = repo.ff_opportunity()
    weeks = weeks.loc[weeks["season"] == season, "week"].dropna()

    if weeks.empty:
        return 1, 18
    return int(weeks.min()), int(weeks.max())


def seasons_available(repo):
    """List the seasons there is expected-points data for, newest first.

    Steps:
        1. Read the weekly table and collect its distinct seasons.
        2. Sort them with the most recent first, since that is what a season
           dropdown should open on.

    Args:
        repo: A `DfsReadRepo`.

    Returns:
        list: Seasons as whole numbers, newest first.
    """
    seasons = repo.ff_opportunity()["season"].dropna().unique()
    return sorted((int(season) for season in seasons), reverse=True)
