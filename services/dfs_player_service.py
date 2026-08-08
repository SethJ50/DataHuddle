"""Gathers everything known about a player's week into one row.

Six sources describe the same player-week and none of them agree on what to call
him. The box score is keyed by the NFL's own id, snap counts and Pro Football
Reference's charting are keyed by PFR's, and the tracking data uses a third name
for the first one. This module puts them together, once, so that every page above
it can work in single rows rather than in joins.

What comes out is the table behind the Player Profile, the Cheat Sheet, and the
usage half of the Team Profile.
"""

import numpy as np
import pandas as pd

from services.dfs_scoring import DfsScoring, rescore

SEASON_TOTAL_WEEK = 0
"""The week number some sources use for a season summary row.

Next Gen Stats ships one of these per player per season alongside the real weeks.
Joining them in would attach a season's worth of averages to whichever week they
landed on, so they are dropped on the way in. Nothing announces they are there --
they simply look like a week nobody played.
"""

# What each source contributes, kept as lists so the join can be read at a glance
# and so a column that disappears upstream fails where it is named rather than
# somewhere further down.
BOX_SCORE_COLUMNS = [
    "completions", "attempts", "passing_yards", "passing_tds",
    "passing_interceptions", "sacks_suffered", "passing_air_yards",
    "passing_epa", "passing_cpoe", "pacr",
    "carries", "rushing_yards", "rushing_tds", "rushing_epa",
    "receptions", "targets", "receiving_yards", "receiving_tds",
    "receiving_air_yards", "receiving_yards_after_catch", "receiving_epa",
    "racr", "target_share", "air_yards_share", "wopr",
]

EXPECTED_COLUMNS = [
    "rush_fantasy_points", "rec_fantasy_points", "pass_fantasy_points",
    "total_fantasy_points", "rush_fantasy_points_exp", "rec_fantasy_points_exp",
    "pass_fantasy_points_exp", "total_fantasy_points_exp",
]

TRACKING_COLUMNS = {
    "receiving": ["avg_cushion", "avg_separation", "avg_intended_air_yards",
                  "percent_share_of_intended_air_yards", "avg_yac",
                  "avg_expected_yac"],
    "rushing": ["efficiency", "percent_attempts_gte_eight_defenders",
                "rush_yards_over_expected_per_att"],
    "passing": ["avg_time_to_throw", "avg_completed_air_yards",
                "aggressiveness"],
}

CHARTING_COLUMNS = {
    "rec": ["receiving_drop_pct", "receiving_broken_tackles"],
    "rush": ["rushing_yards_before_contact_avg",
             "rushing_yards_after_contact_avg"],
}


def player_weeks(repo, scoring=DfsScoring.FANDUEL) -> pd.DataFrame:
    """Build one row per player per week, with every source joined on.

    The single table the Daily Fantasy pages read. Expensive enough that callers
    should hold on to the result rather than asking twice, and cheap enough that
    asking once per page view is fine.

    Steps:
        1. Start from the box score, which covers every player who took a snap
           and is keyed by the id the rest of the app uses.
        2. Add expected points, restated in the chosen scoring by `rescore` from
           services/dfs_scoring.py.
        3. Add snap counts, which are keyed by Pro Football Reference's id and so
           have to come through the crosswalk -- see `_with_snaps` below.
        4. Add the tracking numbers and the charting numbers, each with
           `_join_optional` below, so a source being unavailable costs those
           columns rather than the whole table.
        5. Add red-zone touches, counted from play-by-play by `_red_zone_touches`
           below, since no summary table carries them.

    Args:
        repo: A `DfsReadRepo`.
        scoring: Which scoring the points columns should be in. Defaults to
            FanDuel.

    Returns:
        pd.DataFrame: One row per player per week. Always carries
            `canonical_id`, `name`, `position`, `team`, `opponent`, `season` and
            `week`; everything else is present when its source had something to
            say and blank when it did not.

    Note:
        A LEFT JOIN EVERY TIME, from the box score outward. Every other source
        covers fewer players -- tracking data only exists for players with enough
        snaps, charting only for some positions -- and an inner join anywhere
        would quietly delete players rather than leaving a column blank.

        NOTHING HERE IS FILTERED BY POSITION OR BY GAMES. That belongs to the
        caller, because the Cheat Sheet and the Player Profile want different
        slices of the same table.
    """
    stats = repo.player_stats()

    frame = stats[["player_id", "player_display_name", "position", "team",
                   "opponent_team", "season", "week", "headshot_url"]
                  + [c for c in BOX_SCORE_COLUMNS if c in stats.columns]].copy()
    frame = frame.rename(columns={"player_id": "canonical_id",
                                  "player_display_name": "name",
                                  "opponent_team": "opponent"})

    frame = _with_expected(frame, repo, scoring)
    frame = _with_snaps(frame, repo)

    for kind, columns in TRACKING_COLUMNS.items():
        frame = _join_optional(frame, repo.nextgen_stats(kind), columns,
                               key="player_gsis_id")

    crosswalk = repo.player_id_crosswalk()
    for kind, columns in CHARTING_COLUMNS.items():
        frame = _join_optional(frame, _resolved(repo.pfr_advstats(kind), crosswalk),
                               columns, key="canonical_id")

    frame = frame.merge(_red_zone_touches(repo),
                        on=["canonical_id", "season", "week"], how="left")
    frame = frame.merge(_goal_line_carries(repo),
                        on=["canonical_id", "season", "week"], how="left")
    frame["inside_5_carries"] = frame["inside_5_carries"].fillna(0).astype(int)

    # Every row here is a player who APPEARED, so having no red-zone row means
    # he got none rather than that nobody counted. Zero is the truthful answer
    # and a blank would read as missing data.
    frame["red_zone_touches"] = frame["red_zone_touches"].fillna(0).astype(int)
    return frame


def _with_expected(frame, repo, scoring):
    """Attach expected fantasy points, restated in the chosen scoring.

    Steps:
        1. Restate the expected-points table with `rescore`, so both the actual
           and expected sides move together.
        2. Keep the points columns and join them on by player and week.

    Args:
        frame: The table being built.
        repo: A `DfsReadRepo`.
        scoring: Which scoring to convert to.

    Returns:
        pd.DataFrame: The frame with the eight points columns added, or unchanged
            if there were no expected points to add.

    Note:
        The actual points come from the SAME source as the expected ones rather
        than from the box score, even though the box score has a fantasy points
        column of its own. Everything on the page is really a difference between
        the two, and mixing sources would put a small sourcing artefact inside
        that difference where nobody would ever find it.
    """
    source = repo.ff_opportunity()
    if source.empty or "player_id" not in source.columns:
        # Guarded like every other source: a season without expected points
        # should cost those columns, not the whole table.
        return frame

    expected = rescore(source, scoring)
    columns = ["player_id", "season", "week"] + [
        c for c in EXPECTED_COLUMNS if c in expected.columns]

    return frame.merge(
        expected[columns].rename(columns={"player_id": "canonical_id"}),
        on=["canonical_id", "season", "week"], how="left",
    )


def _with_snaps(frame, repo):
    """Attach how many snaps each player was on the field for.

    The purest measure of opportunity there is, and the one that usually moves
    before the production does.

    Steps:
        1. Read the snap counts, which identify players by Pro Football
           Reference's id rather than the NFL's.
        2. Translate that id with the crosswalk, dropping rows it cannot resolve
           -- see the note.
        3. Join the snap columns on by player and week.

    Args:
        frame: The table being built.
        repo: A `DfsReadRepo`.

    Returns:
        pd.DataFrame: The frame with `offense_snaps` and `snap_share` added.

    Note:
        UNRESOLVED ROWS ARE DROPPED, NOT LEFT BLANK-KEYED. The crosswalk covers
        98% of quarterbacks, backs, receivers and tight ends; the misses are
        practice-squad players with a handful of snaps. Keeping them with an
        empty id would let them all collide into one meaningless row.
    """
    snaps = repo.snap_counts()
    crosswalk = repo.player_id_crosswalk()

    resolved = snaps.merge(crosswalk, on="pfr_player_id", how="inner")
    resolved = resolved[["canonical_id", "season", "week", "offense_snaps",
                         "offense_pct"]].rename(
        columns={"offense_pct": "snap_share"})

    # One player can appear twice in a week if a game was suspended and resumed.
    resolved = resolved.groupby(["canonical_id", "season", "week"],
                                as_index=False).agg(
        offense_snaps=("offense_snaps", "sum"),
        snap_share=("snap_share", "max"),
    )
    return frame.merge(resolved, on=["canonical_id", "season", "week"],
                       how="left")


def _resolved(source, crosswalk):
    """Translate a table keyed by Pro Football Reference's id into the app's own.

    Steps:
        1. Hand back an empty table if the source has nothing, or has no id
           column to translate -- a source can be missing for a whole season.
        2. Join the crosswalk on, keeping only the rows it can resolve.

    Args:
        source: A table keyed by `pfr_player_id`.
        crosswalk: The lookup from `DfsReadRepo.player_id_crosswalk`.

    Returns:
        pd.DataFrame: The same rows carrying `canonical_id`, or an empty frame
            when there was nothing to translate.

    Note:
        THE GUARD IS THE POINT. Without it, a source that failed to load takes
        the whole player table down with it -- and it would do so inside a join,
        several steps from anything that names the source.
    """
    if source.empty or "pfr_player_id" not in source.columns:
        return pd.DataFrame()
    return source.merge(crosswalk, on="pfr_player_id", how="inner")


def _join_optional(frame, source, columns, key):
    """Join a source's columns on, quietly doing nothing if it has none of them.

    The sources beyond the box score are each partial -- tracking data covers
    only players with enough snaps, charting only some positions -- and any of
    them can be missing entirely. This makes that cost a few blank columns
    rather than an error.

    Steps:
        1. Give up and return the frame untouched if the source is empty or
           carries none of the wanted columns.
        2. Drop the season-summary rows some sources mix in -- see
           `SEASON_TOTAL_WEEK` at the top of this module.
        3. Reduce to one row per player per week, so the join cannot multiply
           rows.
        4. Join by player and week, keeping every row of the frame.

    Args:
        frame: The table being built.
        source: The table to take columns from.
        columns: Which columns to take.
        key: The source's name for the player id.

    Returns:
        pd.DataFrame: The frame, with whatever could be added.
    """
    wanted = [c for c in columns if c in source.columns]
    if source.empty or not wanted or key not in source.columns:
        return frame

    narrowed = source[[key, "season", "week"] + wanted].rename(
        columns={key: "canonical_id"})
    narrowed = narrowed[narrowed["week"] != SEASON_TOTAL_WEEK]

    # `last` rather than a sum: these are averages and rates, so adding two rows
    # together would be meaningless where taking either is merely arbitrary.
    narrowed = narrowed.groupby(["canonical_id", "season", "week"],
                                as_index=False).last()

    return frame.merge(narrowed, on=["canonical_id", "season", "week"],
                       how="left")


def _red_zone_touches(repo) -> pd.DataFrame:
    """Count how often each player got the ball inside the twenty.

    Red-zone work is where touchdowns come from, and touchdowns are most of what
    separates a good fantasy week from an ordinary one. No summary table carries
    this, so it is counted from the plays themselves.

    Steps:
        1. Keep the plays inside the twenty.
        2. Count the carries credited to each rusher and the targets credited to
           each receiver.
        3. Add the two together, since a touch is a touch.

    Args:
        repo: A `DfsReadRepo`, for the play-by-play table.

    Returns:
        pd.DataFrame: `canonical_id`, `season`, `week` and `red_zone_touches`.
            Only players who had at least one -- the caller fills the rest with
            zero, which is what a blank means here.

    Note:
        A TARGET COUNTS, NOT ONLY A CATCH. Being thrown at on the two-yard line
        is the opportunity; whether it was caught is the outcome, and the whole
        reason to look at usage separately from production is to see the
        opportunity on its own.
    """
    plays = repo.pbp()
    inside = plays[plays["yardline_100"] <= 20]

    touches = []
    for column in ("rusher_player_id", "receiver_player_id"):
        counted = (inside[inside[column].notna()]
                   .groupby([column, "season", "week"], as_index=False)
                   .agg(touches=("play_id", "size"))
                   .rename(columns={column: "canonical_id"}))
        touches.append(counted)

    if not touches:
        return pd.DataFrame(columns=["canonical_id", "season", "week",
                                     "red_zone_touches"])

    return (pd.concat(touches)
            .groupby(["canonical_id", "season", "week"], as_index=False)
            .agg(red_zone_touches=("touches", "sum")))


def _goal_line_carries(repo) -> pd.DataFrame:
    """Count each back's carries from inside the five-yard line.

    The single most valuable carry in fantasy football. A back who gets the ball
    on the two scores roughly half the time, and a back who never does depends
    on long runs that may not come -- so this separates two players who otherwise
    look identical on volume.

    Steps:
        1. Keep the runs starting inside the five.
        2. Count them per rusher per week.

    Args:
        repo: A `DfsReadRepo`, for the play-by-play table.

    Returns:
        pd.DataFrame: `canonical_id`, `season`, `week` and `inside_5_carries`.
            Only players who had at least one -- the caller fills the rest with
            zero, which is what a blank means here.

    Note:
        CARRIES ONLY, unlike `_red_zone_touches` above which counts targets too.
        A goal-line carry is a designed handoff from a yard or two out; a target
        from there is a different play with a different success rate, and pooling
        the two would hide exactly the distinction this column exists to draw.
    """
    plays = repo.pbp()
    inside = plays[(plays["yardline_100"] <= 5)
                   & plays["rusher_player_id"].notna()]

    return (inside.groupby(["rusher_player_id", "season", "week"],
                           as_index=False)
            .agg(inside_5_carries=("play_id", "size"))
            .rename(columns={"rusher_player_id": "canonical_id"}))


def rolling_form(frame, canonical_id, games=5) -> dict:
    """Summarise a player's last few games in the handful of numbers that matter.

    Answers "is he trending?" without making anyone read a table. A player
    beating expectation for five weeks running is either genuinely good or about
    to regress, and the raw game log makes you work that out in your head.

    Steps:
        1. Take that player's rows and put the most recent first.
        2. Keep the last few games.
        3. Average the numbers worth averaging, ignoring any that are missing.

    Args:
        frame: The table from `player_weeks` above.
        canonical_id: Which player.
        games: How many recent games to summarise.

    Returns:
        dict: `games`, `points`, `expected_points`, `gap`, `snap_share`,
            `target_share` and `air_yards`. Values are NaN where there was
            nothing to average, and `games` is 0 for a player with no rows --
            which the caller should check before showing anything.
    """
    rows = frame[frame["canonical_id"] == canonical_id]
    rows = rows.sort_values(["season", "week"], ascending=False).head(games)

    if rows.empty:
        return {"games": 0}

    def mean(column):
        """Average one column, or NaN if it is absent or entirely blank."""
        if column not in rows.columns:
            return float("nan")
        values = pd.to_numeric(rows[column], errors="coerce")
        return float(values.mean()) if values.notna().any() else float("nan")

    points = mean("total_fantasy_points")
    expected = mean("total_fantasy_points_exp")

    return {
        "games": len(rows),
        "points": points,
        "expected_points": expected,
        "gap": points - expected if np.isfinite([points, expected]).all()
        else float("nan"),
        "snap_share": mean("snap_share"),
        "target_share": mean("target_share"),
        "air_yards": mean("avg_intended_air_yards"),
    }


def team_usage(frame, team, season=None, weeks=None) -> pd.DataFrame:
    """Show who gets the ball on one team, and how much of it.

    The heart of a team profile for Daily Fantasy purposes. Fantasy points come
    from touches, touches come from a role, and a role is visible weeks before
    the production is.

    Steps:
        1. Keep that team's rows for the season and weeks asked for.
        2. Add up each player's targets, carries, red-zone touches and points.
        3. Turn the counts into shares of what the whole team did over the same
           stretch -- see the note on why that is not an average of the weekly
           shares.
        4. Average the rate statistics, which are already per-game figures.
        5. Sort by target share, since that is what usually decides a week.

    Args:
        frame: The table from `player_weeks`.
        team: Which team, as an abbreviation such as `"SEA"`.
        season: Which season, or None for every season in the frame.
        weeks: A `(first, last)` pair, both included, or None for all of them.

    Returns:
        pd.DataFrame: One row per player, with `name`, `position`, `games`,
            `snap_share`, `targets`, `target_share`, `carries`, `carry_share`,
            `red_zone_touches`, `red_zone_share`, `air_yards`, `points_per_game`
            and `expected_points_per_game`. Empty with those columns if the team
            has no rows.

    Note:
        SHARES ARE BUILT FROM TOTALS, NOT AVERAGED FROM THE WEEKLY SHARES. A
        player who saw 40% of the targets in one game and missed the other seven
        did not command 40% of the offence; averaging his weekly shares would say
        he did. Dividing his total by the team's total over the same stretch
        answers the question actually being asked.
    """
    columns = ["name", "position", "games", "snap_share", "targets",
               "target_share", "carries", "carry_share", "red_zone_touches",
               "red_zone_share", "air_yards", "points_per_game",
               "expected_points_per_game"]

    rows = frame[frame["team"] == team]
    if season is not None:
        rows = rows[rows["season"] == season]
    if weeks is not None:
        first, last = weeks
        rows = rows[rows["week"].between(first, last)]

    if rows.empty:
        return pd.DataFrame(columns=columns)

    def total(column):
        """Sum one column per player, treating missing values as nothing."""
        if column not in rows.columns:
            return ("week", "size")     # a placeholder the caller overwrites
        return (column, "sum")

    grouped = rows.groupby(["canonical_id", "name", "position"],
                           as_index=False).agg(
        games=("week", "nunique"),
        targets=total("targets"),
        carries=total("carries"),
        red_zone_touches=total("red_zone_touches"),
        points=total("total_fantasy_points"),
        expected_points=total("total_fantasy_points_exp"),
        snap_share=("snap_share", "mean"),
        air_yards=("avg_intended_air_yards", "mean")
        if "avg_intended_air_yards" in rows.columns else ("week", "size"),
    )

    for count, share in (("targets", "target_share"),
                         ("carries", "carry_share"),
                         ("red_zone_touches", "red_zone_share")):
        team_total = grouped[count].sum()
        grouped[share] = (grouped[count] / team_total if team_total
                          else float("nan"))

    grouped["points_per_game"] = grouped["points"] / grouped["games"]
    grouped["expected_points_per_game"] = (grouped["expected_points"]
                                           / grouped["games"])

    return (grouped[columns]
            .sort_values("target_share", ascending=False)
            .reset_index(drop=True))


def slate(frame, season, week, positions=None, teams=None,
          minimum_snaps=0) -> pd.DataFrame:
    """Pick out one week's players, ready to be listed side by side.

    The Cheat Sheet's table. Everything on it already exists in the player-week
    table; this narrows that to a single week and adds the one number worth
    deriving.

    Steps:
        1. Keep the chosen season and week.
        2. Narrow to the positions and teams asked for, if any.
        3. Drop anyone below the snap floor, which is how a slate of a few
           hundred players becomes a slate of the ones worth considering.
        4. Work out the gap between what each player scored and what his
           opportunities were worth.
        5. Sort by points scored, since that is what people look at first.

    Args:
        frame: The table from `player_weeks`.
        season: Which season, as a year.
        week: Which week, as a number.
        positions: Which positions to keep, or None for all of them.
        teams: Which teams to keep, or None for all of them.
        minimum_snaps: Drop players with fewer offensive snaps than this.

    Returns:
        pd.DataFrame: One row per player, with everything the player-week table
            holds plus `points_gap`. Empty with its columns if nothing matched.

    Note:
        ONE WEEK, NOT A RANGE. A Daily Fantasy slate is a single week's games,
        and totalling several weeks would answer the season-long question the
        Basic Plots page already answers better.
    """
    rows = frame[(frame["season"] == season) & (frame["week"] == week)].copy()

    if positions is not None:
        rows = rows[rows["position"].isin(list(positions))]
    if teams is not None:
        rows = rows[rows["team"].isin(list(teams))]
    if minimum_snaps:
        rows = rows[rows["offense_snaps"].fillna(0) >= minimum_snaps]

    if rows.empty:
        return rows.assign(points_gap=[])

    actual = pd.to_numeric(rows.get("total_fantasy_points"), errors="coerce")
    expected = pd.to_numeric(rows.get("total_fantasy_points_exp"),
                             errors="coerce")
    rows["points_gap"] = actual - expected

    return rows.sort_values("total_fantasy_points", ascending=False,
                            na_position="last").reset_index(drop=True)
