"""Scores team defences, which nothing else in this app knows how to do.

Every other position is scored on yards and touchdowns, and the expected-points
source covers them all. A defence is scored on something else entirely -- sacks,
takeaways, and above all how few points it let the other side have -- and it
appears in no player table anywhere.

So this builds the defensive week from scratch: the counting stats come from the
team totals, the points allowed come from the other team's score, and the two are
put through the tier table below.
"""

import numpy as np
import pandas as pd

POINTS_ALLOWED_TIERS = (
    (0, 10.0),
    (6, 7.0),
    (13, 4.0),
    (20, 1.0),
    (27, 0.0),
    (34, -1.0),
)
"""What a defence earns for holding the other side to at most this many points.

Read as "up to and including": a shutout pays 10, one to six pays 7, and so on.
Anything past the last band pays `BLOWOUT_POINTS` below.

FanDuel and DraftKings use the SAME tiers, which is why one table serves both.
They differ on almost everything else, so this is worth stating rather than
assuming next time somebody adds a site.
"""

BLOWOUT_POINTS = -4.0
"""What a defence earns for giving up more than the last tier allows."""

EVENT_POINTS = {
    "def_sacks": 1.0,
    "def_interceptions": 2.0,
    "fumble_recovery_opp": 2.0,
    "def_tds": 6.0,
    "def_safeties": 2.0,
    "special_teams_tds": 6.0,
}
"""What each defensive event is worth. Both sites agree on all of these.

A FUMBLE RECOVERED, NOT ONE FORCED. Forcing a fumble the other side falls on
scores nothing, and `def_fumbles_forced` counts those too -- so the recovery
column is the one that pays.

BLOCKED KICKS ARE MISSING, worth 2 points on both sites. nflreadpy does not
expose them, and they happen a handful of times a season, so a defence's score
can be short by two on a rare week. Stated here rather than quietly absent.
"""


def points_allowed_score(points):
    """Turn points conceded into what the tier table pays for them.

    Steps:
        1. Start everyone at the blowout figure.
        2. Walk the tiers from the most generous down, overwriting any defence
           that kept the other side within that band.

    Args:
        points: How many points each defence gave up. May be a number or a whole
            column of them.

    Returns:
        The tier payment, matching the type it was given.

    Note:
        Walking DOWNWARD matters. Each tier is written as an upper bound, so
        applying them from the strictest first would let the loosest overwrite
        everything and pay a shutout the same as a 20-point game.
    """
    values = pd.Series(points) if not isinstance(points, pd.Series) \
        else points
    scored = pd.Series(BLOWOUT_POINTS, index=values.index, dtype=float)

    for ceiling, payment in reversed(POINTS_ALLOWED_TIERS):
        scored = scored.mask(values <= ceiling, payment)

    return scored.where(values.notna(), np.nan)


def dst_weeks(repo) -> pd.DataFrame:
    """Build one row per team defence per week, scored.

    The defensive counterpart to `player_weeks`. Everything the Cheat Sheet
    shows for a DST comes from here.

    Steps:
        1. Read the team totals, which carry the defensive counting stats.
        2. Work out what each defence gave up, using `_points_allowed` below,
           since no table records that directly.
        3. Pay for each event at the rates in `EVENT_POINTS` above.
        4. Pay for the points allowed through the tier table.
        5. Add the two together.

    Args:
        repo: A `DfsReadRepo`.

    Returns:
        pd.DataFrame: `team`, `season`, `week`, `opponent`, `points_allowed`,
            `sacks`, `interceptions`, `fumble_recoveries`, `defensive_tds`,
            `safeties` and `total_fantasy_points`. Empty with those columns if
            there is nothing to score.

    Note:
        The column is called `total_fantasy_points` to match what every player
        row calls it, so a page can read the two the same way. There is no
        expected-points equivalent -- nothing projects a defence's opportunities,
        and inventing one here would put a modelled number beside measured ones.
    """
    columns = ["team", "season", "week", "opponent", "points_allowed", "sacks",
               "interceptions", "fumble_recoveries", "defensive_tds",
               "safeties", "total_fantasy_points"]

    stats = repo.team_stats()
    if stats.empty:
        return pd.DataFrame(columns=columns)

    frame = stats[["team", "season", "week"]].copy()
    frame["opponent"] = stats.get("opponent_team")

    def counted(column):
        """One event column as numbers, or zeroes if the source lacks it."""
        if column not in stats.columns:
            return pd.Series(0.0, index=stats.index)
        return pd.to_numeric(stats[column], errors="coerce").fillna(0)

    frame["sacks"] = counted("def_sacks")
    frame["interceptions"] = counted("def_interceptions")
    frame["fumble_recoveries"] = counted("fumble_recovery_opp")
    frame["defensive_tds"] = counted("def_tds") + counted("special_teams_tds")
    frame["safeties"] = counted("def_safeties")

    frame["points_allowed"] = _points_allowed(repo, stats)

    events = sum(counted(column) * points
                 for column, points in EVENT_POINTS.items())
    frame["total_fantasy_points"] = (events
                                     + points_allowed_score(frame["points_allowed"]))

    return frame[columns]


def _points_allowed(repo, stats):
    """Work out how many points each defence gave up, from the scoreboard.

    Steps:
        1. Read the schedule, which is the only place the scores live.
        2. Turn each game into two rows -- one per team -- carrying what that
           team's defence conceded, which is the OTHER side's score.
        3. Line those up with the team-week rows being built.

    Args:
        repo: A `DfsReadRepo`.
        stats: The team totals, for the rows to line up against.

    Returns:
        pd.Series: Points conceded, lined up with `stats`. NaN for a game with no
            score recorded, which is every game not yet played.
    """
    games = repo.schedules()
    needed = {"season", "week", "home_team", "away_team", "home_score",
              "away_score"}
    if games.empty or not needed <= set(games.columns):
        return pd.Series(np.nan, index=stats.index)

    conceded = pd.concat([
        games[["season", "week", "home_team", "away_score"]].rename(
            columns={"home_team": "team", "away_score": "points_allowed"}),
        games[["season", "week", "away_team", "home_score"]].rename(
            columns={"away_team": "team", "home_score": "points_allowed"}),
    ])

    keyed = stats[["season", "week", "team"]].merge(
        conceded, on=["season", "week", "team"], how="left")
    return pd.to_numeric(keyed["points_allowed"], errors="coerce").to_numpy()
