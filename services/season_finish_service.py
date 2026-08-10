"""Works out where players actually finished in a past season.

Projections say where somebody is expected to land; this says where he landed.
Putting the two side by side is the whole point of a page like Path to WR1 --
the interesting player is the one the projections rate far above where he
finished, because that is a claim about change rather than a restatement of last
year.

Reads the season-long stat rows rather than any Daily Fantasy table: the pre-draft
half of the app loads several seasons of game logs and no play-by-play, and this
needs only the former.
"""

import pandas as pd

import scoring

# How the season-long stat table names the things scoring.py asks for. The two
# vocabularies differ in the same two places the Daily Fantasy game log does:
# the scorer wants one `interceptions` and one `fumbles_lost`, while the source
# prefixes the first and splits the second three ways by how it was lost.
SCORING_SOURCES = {
    "passing_yards": ("passing_yards",),
    "passing_tds": ("passing_tds",),
    "interceptions": ("passing_interceptions",),
    "rushing_yards": ("rushing_yards",),
    "rushing_tds": ("rushing_tds",),
    "receiving_yards": ("receiving_yards",),
    "receiving_tds": ("receiving_tds",),
    "receptions": ("receptions",),
    "fumbles_lost": ("sack_fumbles_lost", "rushing_fumbles_lost",
                     "receiving_fumbles_lost"),
}


def latest_season(stats) -> int:
    """Find the most recent season the stat table actually has games for.

    "Last year" has to be worked out rather than assumed: the season being
    drafted for has no games in it yet, so counting back from the draft's own
    year would look at a season nobody has played.

    Steps:
        1. Take the largest season number present.

    Args:
        stats: The season-long stat rows, from `NflReadRepo.player_stats`.

    Returns:
        int: The season, such as 2025. None if the table is empty.
    """
    if stats.empty:
        return None
    return int(stats["season"].max())


def season_finishes(stats, season, position, fmt,
                    passing_td_points=None) -> pd.DataFrame:
    """Rank one position by the fantasy points its players actually scored.

    Answers "where did he finish" -- the WR13 or RB4 that a projection is
    implicitly arguing with.

    Steps:
        1. Keep the season and position asked for.
        2. Total each of the scorer's inputs per player, summing the columns the
           source splits and treating a missing one as nothing.
        3. Score those totals with `scoring.fantasy_points` from scoring.py, so
           this and the projections are counted by the same rules.
        4. Rank by points, best first, and build the label people actually use.

    Args:
        stats: The season-long stat rows, from `NflReadRepo.player_stats`.
        season: Which season to measure.
        position: Which position to rank within, such as "WR".
        fmt: The ScoringFormat to score under.
        passing_td_points: What one passing touchdown is worth in the
            league. None uses the four-point default.

    Returns:
        pd.DataFrame: One row per player who appeared, with `canonical_id`,
            `points`, `finish` (1 is best) and `finish_label` (such as "WR13").
            Empty with those columns when the season has no rows.

    Note:
        RANKED WITHIN THE POSITION AND THE SEASON, over everyone who took a snap
        -- not over some qualifying subset. A player who missed most of the year
        finishes low, which is the honest answer: he did not produce.

        Scored through scoring.py rather than the source's own fantasy point
        column, so a finish and a projection are never counted by different
        rules. The source's column uses standard scoring and would silently
        disagree in any PPR league.
    """
    columns = ["canonical_id", "points", "finish", "finish_label"]

    rows = stats[(stats["season"] == season) & (stats["position"] == position)]
    if rows.empty:
        return pd.DataFrame(columns=columns)

    totals = {}
    for key, sources in SCORING_SOURCES.items():
        running = pd.Series(0.0, index=rows.index)
        for source in sources:
            if source in rows.columns:
                running = running + rows[source].fillna(0)
        totals[key] = running

    scored = rows[["player_id"]].assign(
        **{key: values for key, values in totals.items()})
    scored = scored.groupby("player_id", as_index=False).sum(numeric_only=True)

    scored["points"] = scoring.fantasy_points(
        {key: scored[key] for key in scoring.STAT_KEYS}, fmt, passing_td_points)

    # method="min" so two players who scored the same share the better finish,
    # which is how a fantasy site would report it.
    scored["finish"] = scored["points"].rank(method="min", ascending=False)
    scored["finish_label"] = (position
                              + scored["finish"].astype(int).astype(str))

    return (scored.rename(columns={"player_id": "canonical_id"})[columns]
            .sort_values("finish").reset_index(drop=True))
