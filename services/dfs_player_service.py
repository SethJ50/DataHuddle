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

    frame = frame.merge(_opportunity_counts(repo),
                        on=["canonical_id", "season", "week"], how="left")

    # Every row here is a player who APPEARED, so having no row in the counts
    # means he got none rather than that nobody counted. Zero is the truthful
    # answer and a blank would read as missing data.
    OPPORTUNITY_COUNTS = ["red_zone_carries", "red_zone_targets",
                          "red_zone_touches", "inside_5_carries",
                          "inside_5_targets", "goal_line_carries",
                          "end_zone_targets"]
    frame[OPPORTUNITY_COUNTS] = frame[OPPORTUNITY_COUNTS].fillna(0).astype(int)

    # Share of the team's total for each, using the same helper as carry_share.
    OPPORTUNITY_SHARES = {
        "red_zone_carries":  "red_zone_carry_share",
        "red_zone_targets":  "red_zone_target_share",
        "inside_5_carries":  "inside_5_carry_share",
        "inside_5_targets":  "inside_5_target_share",
        "goal_line_carries": "goal_line_carry_share",
        "end_zone_targets":  "end_zone_target_share",
    }
    for count, share in OPPORTUNITY_SHARES.items():
        frame[share] = _team_share(frame, count)

    # --- derived measures ---------------------------------------------------
    # Computed here rather than in the Cheat Sheet so the trailing-form averages
    # pick them up automatically -- `_average_everything` prefixes every numeric
    # column with `form_`, so each of these gets a per-game version for free.
    #
    # EACH IS GUARDED ON ITS INPUTS. The box-score selection above keeps whatever
    # columns the season actually has, so a source that went missing for a year
    # simply is not there -- and deriving from an absent column would take the
    # whole page down rather than costing it one column. Missing here means the
    # derived column is absent too, which every consumer already tolerates.
    if {"completions", "attempts"} <= set(frame.columns):
        frame["completion_pct"] = _safe_ratio(frame["completions"],
                                              frame["attempts"])

    # Adjusted opportunities: a target is worth about twice a carry, because it
    # can gain yards through the air AND after the catch. One number that lets a
    # pass-catching back and a between-the-tackles back be compared directly.
    if {"carries", "targets"} <= set(frame.columns):
        frame["adj_opportunities"] = (frame["carries"].fillna(0)
                                      + 2 * frame["targets"].fillna(0))

    # The source publishes target_share and air_yards_share but not this one.
    if "carries" in frame.columns:
        frame["carry_share"] = _team_share(frame, "carries")

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


def _count_by(plays, id_column, name):
    """Count a filtered set of plays per player per week.

    The shared body of `_opportunity_counts` below. Every count it produces is
    the same shape — some subset of the plays, credited to either the rusher or
    the receiver — so the only things that change are which plays and which
    column names the player.

    Steps:
        1. Drop the plays with nobody in that role. A run has no receiver, and
           `notna` is what keeps it out of a target count.
        2. Count the remaining plays per player per week.
        3. Rename the id column, so every piece can be merged on the same key.

    Args:
        plays: An already-filtered slice of the play-by-play table.
        id_column: Which role to credit: "rusher_player_id" or
            "receiver_player_id".
        name: What to call the resulting count column.

    Returns:
        pd.DataFrame: `canonical_id`, `season`, `week` and one count column.
            Only players who had at least one.
    """
    credited = plays[plays[id_column].notna()]
    return (credited.groupby([id_column, "season", "week"], as_index=False)
            .agg(**{name: ("play_id", "size")})
            .rename(columns={id_column: "canonical_id"}))


def _opportunity_counts(repo) -> pd.DataFrame:
    """Count the high-value touches nobody publishes a summary of.

    Touchdowns are most of what separates a good fantasy week from an ordinary
    one, and they come from a handful of plays near the goal line. No summary
    table carries these, so they are counted from the plays themselves — all six
    in one pass, because they read the same table.

    Steps:
        1. Slice the plays four ways: inside the twenty, inside the five, with a
           goal-to-go line to gain, and passes thrown as far as the goal line.
        2. Count each slice with `_count_by` above, crediting the rusher for
           carries and the receiver for targets.
        3. Merge the six counts together with an outer join, so a player who
           appears in only one still gets a row.
        4. Fill the gaps with zero and add `red_zone_touches`, which is the two
           red-zone counts back together.

    Args:
        repo: A `DfsReadRepo`, for the play-by-play table.

    Returns:
        pd.DataFrame: `canonical_id`, `season`, `week`, and the counts
            `red_zone_carries`, `red_zone_targets`, `red_zone_touches`,
            `inside_5_carries`, `inside_5_targets`, `goal_line_carries` and
            `end_zone_targets`. Only players with at least one of something.

    Note:
        A TARGET COUNTS, NOT ONLY A CATCH. Being thrown at on the two-yard line
        is the opportunity; whether it was caught is the outcome, and looking at
        usage separately from production is the whole point.

        THE FOUR SLICES OVERLAP, deliberately. A carry from the three is inside
        the five AND inside the twenty AND almost certainly goal-to-go, so it is
        counted in three columns. They answer different questions — "how much
        scoring work does he get" versus "does he get the ball on the doorstep" —
        and making them exclusive would break both.

        An END ZONE TARGET is a pass thrown at least as far as the goal line
        (`air_yards >= yardline_100`), which is not the same as a target caught
        in the end zone. Plays with no air yards recorded compare as False and
        drop out on their own.
    """
    plays = repo.pbp()

    red_zone = plays[plays["yardline_100"] <= 20]
    inside_5 = plays[plays["yardline_100"] <= 5]
    goal_line = plays[plays["goal_to_go"] == 1]
    end_zone = plays[plays["air_yards"] >= plays["yardline_100"]]

    pieces = [
        _count_by(red_zone, "rusher_player_id", "red_zone_carries"),
        _count_by(red_zone, "receiver_player_id", "red_zone_targets"),
        _count_by(inside_5, "rusher_player_id", "inside_5_carries"),
        _count_by(inside_5, "receiver_player_id", "inside_5_targets"),
        _count_by(goal_line, "rusher_player_id", "goal_line_carries"),
        _count_by(end_zone, "receiver_player_id", "end_zone_targets"),
    ]

    keys = ["canonical_id", "season", "week"]
    counts = pieces[0]
    for piece in pieces[1:]:
        counts = counts.merge(piece, on=keys, how="outer")

    values = [column for column in counts.columns if column not in keys]
    counts[values] = counts[values].fillna(0).astype(int)

    # Kept so the played-week Volume group keeps working -- it shows touches
    # rather than the split.
    counts["red_zone_touches"] = (counts["red_zone_carries"]
                                  + counts["red_zone_targets"])
    return counts

def _safe_ratio(numerator, denominator):
    """Divide two columns, giving a blank rather than infinity when dividing by zero.

    Rate statistics are undefined for a player who had no attempts at all, and
    that is a different thing from a rate of zero. A receiver has no completion
    percentage; he did not throw badly.

    Steps:
        1. Replace every zero denominator with NaN, which makes the division
           produce NaN instead of infinity.
        2. Divide.

    Args:
        numerator: The top of the ratio, as a column.
        denominator: The bottom of the ratio, as a column.

    Returns:
        pd.Series: The ratio, NaN wherever the denominator was zero or missing.
    """
    return numerator / denominator.where(denominator != 0)


def _team_share(frame, column):
    """Work out what share of his team's weekly total each player accounted for.

    The same shape as the `target_share` the source publishes, computed here for
    the counts it does not cover.

    Steps:
        1. Total the column across every player on the same team in the same
           week, and broadcast that total back onto each of their rows.
        2. Divide each player's own count by it with `_safe_ratio` above, so a
           team with none that week comes out blank rather than as a divide-by-
           zero.

    Args:
        frame: The player-week table being built.
        column: Which count to take a share of, such as "carries".

    Returns:
        pd.Series: The share, between 0 and 1, lined up with the frame's rows.
    """
    totals = frame.groupby(["team", "season", "week"])[column].transform("sum")
    return _safe_ratio(frame[column], totals)


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

def played_before(frame, season, week):
    """Keep the games played strictly before a given week, newest orderable.

    Everything on the Daily Fantasy player page looks BACKWARDS from a week you
    are building a lineup for, and that week is usually one nobody has played --
    so "his recent form" has to reach into the previous season rather than stop
    at a season boundary.

    Steps:
        1. Turn each row's season and week into one sortable number, so "before
           this week" and "most recent first" are each a single comparison
           rather than a pair of them.
        2. Keep the rows below the target.

    Args:
        frame: Player-weeks, any number of seasons.
        season: The season being built for.
        week: The week being built for.

    Returns:
        pd.DataFrame: The rows before that week, with an extra `_when` column
            holding the sortable number. Empty if nothing qualifies.

    Note:
        STRICTLY BEFORE. If the target week has already been played -- which it
        will have been for any week you look back at -- including it would put
        the result inside the form used to predict it.
    """
    rows = frame.copy()
    rows["_when"] = (rows["season"].astype(int) * 100
                     + rows["week"].astype(int))
    return rows[rows["_when"] < int(season) * 100 + int(week)]


def last_games(frame, games):
    """Keep each player's most recent few games, however they are spread.

    Every comparison on the page is "his last five against everybody else's last
    five", so each player's window is cut from HIS OWN games. A player who missed
    three weeks is still averaged over five games he actually played, rather than
    over whatever happens to sit in a fixed range of weeks.

    Steps:
        1. Order each player's rows newest first, using the `_when` column
           `played_before` above attaches.
        2. Keep the first few of each.

    Args:
        frame: The output of `played_before` above.
        games: How many games to keep per player.

    Returns:
        pd.DataFrame: The same columns, cut to at most `games` rows per player.
    """
    if frame.empty:
        return frame
    return (frame.sort_values(["canonical_id", "_when"], ascending=[True, False])
            .groupby("canonical_id", as_index=False, group_keys=False)
            .head(int(games)))


def upcoming_week(frame):
    """Guess which week a lineup would be built for, from the games played.

    Used as a fallback when no salary slate has been loaded to say so directly.

    Steps:
        1. Find the most recent season and week that have rows.
        2. Return the week after it, rolling into week 1 of the next season once
           a regular season is done.

    Args:
        frame: Player-weeks, any number of seasons.

    Returns:
        tuple: `(season, week)`. `(None, None)` when the frame is empty.
    """
    if frame.empty:
        return None, None

    newest = frame.loc[(frame["season"].astype(int) * 100
                        + frame["week"].astype(int)).idxmax()]
    season, week = int(newest["season"]), int(newest["week"])
    return (season, week + 1) if week < 18 else (season + 1, 1)


def weekly_percentiles(frame, canonical_id, fields, when, minimum_snaps=1):
    """Rank one player against his position, week by week, on several stats.

    Stats measured in different units cannot share an axis or a colour scale --
    fantasy points run to forty, EPA to about one, target share to a third.
    Turning each into a PERCENTILE among the players he is competing with makes
    them comparable, and answers a more useful question than the raw number
    does: not "how many targets" but "how good a week was that for a receiver".

    Steps:
        1. Make sure the frame carries the sortable season-and-week number, so
           weeks from different seasons cannot collide.
        2. Read his position off his most recent row.
        3. Gather everybody at his position who played in the weeks asked for,
           above a snap floor -- see the note.
        4. Rank every peer within each week, one stat at a time, flipping the
           handful where a smaller number is better.
        5. Keep his own placings.

    Args:
        frame: Player-weeks for EVERY player. Ranking needs the field he is
            being compared against.
        canonical_id: The player being ranked.
        fields: Which columns to rank, as column names.
        when: The season-and-week numbers to rank at, as produced by
            `played_before` above. Any iterable; usually one player's own rows.
        minimum_snaps: How many offensive snaps a peer needs before he counts.
            The player himself is always included whatever his snap count.

    Returns:
        pd.DataFrame: Long format, one row per week per stat, with `when` (the
            sortable number), `period` (a short label such as "25W18"), `stat`,
            `percentile` (0-100, HIGHER ALWAYS MEANING BETTER PLACED) and
            `value` (the raw number). Empty with those columns when nothing
            qualifies.

    Note:
        TAKES THE WEEKS RATHER THAN WORKING THEM OUT. One caller wants his last
        five games and another wants a whole season, and neither is a special
        case of the other -- so the choice belongs to them.

        THE SNAP FLOOR IS LOAD-BEARING. A position's weekly rows include every
        practice-squad player who took a single snap, and ranking against them
        would put a mediocre starter in the 95th percentile every week.

        RANKED WITHIN EACH WEEK SEPARATELY, so a week when everybody scored is
        not mistaken for a good week by this player.
    """
    from presentation.dfs_gamelog import direction_of

    blank = pd.DataFrame(columns=["when", "period", "stat", "percentile",
                                  "value"])

    when = list(when)
    if frame.empty or not fields or not when:
        return blank

    rows = frame if "_when" in frame.columns else frame.assign(
        _when=frame["season"].astype(int) * 100 + frame["week"].astype(int))

    his = rows[rows["canonical_id"] == canonical_id]
    if his.empty:
        return blank

    position = his.sort_values("_when")["position"].iloc[-1]

    # He is kept whatever his snap count -- excluding the subject from his own
    # comparison would drop the week entirely.
    peers = rows[(rows["position"] == position)
                 & (rows["_when"].isin(when))
                 & ((rows["offense_snaps"].fillna(0) >= minimum_snaps)
                    | (rows["canonical_id"] == canonical_id))]

    mine = peers["canonical_id"] == canonical_id

    pieces = []
    for field in fields:
        if field not in peers.columns:
            continue

        direction = direction_of(field)
        if direction is None:
            continue          # no better end, so no ranking to give

        values = pd.to_numeric(peers[field], errors="coerce")
        ranked = values.groupby(peers["_when"]).rank(pct=True) * 100

        # HIGHER ALWAYS MEANS BETTER PLACED once this returns, whichever way the
        # underlying stat runs. Without the flip a week with four interceptions
        # would read as a good one, in the plot and in the game log alike.
        if direction == "lower":
            ranked = 100 - ranked

        pieces.append(pd.DataFrame({
            "when": peers.loc[mine, "_when"],
            # Two digits of season, so a window crossing New Year cannot show
            # two columns both labelled "W1".
            "period": (peers.loc[mine, "season"].astype(int) % 100).astype(str)
                      + "W" + peers.loc[mine, "week"].astype(int).astype(str),
            "stat": field,
            "percentile": ranked[mine],
            "value": values[mine],
        }))

    return pd.concat(pieces, ignore_index=True) if pieces else blank


SNAPS_PER_GAME = 10
"""Snaps per game a peer must average before he counts in a comparison.

Multiplied by the window length, so a five-game window asks for fifty and a
ten-game window for a hundred. Scaling it means the floor never has to be
retuned when the window changes.

Ten a game is roughly a rotational player -- low enough to keep genuine
committee backs in, high enough to keep out the practice-squad rows that would
otherwise put every starter in the ninetieth percentile.
"""

def window_summary(frame, canonical_id, fields, season, week, games=5,
                   snaps_per_game=SNAPS_PER_GAME):
    """Average a player's recent games, and say where each average places him.

    The number and its context together. An average of 14 carries a game means
    nothing on its own; "14 carries, which is the 88th percentile among backs
    playing regularly" is a judgement you can act on.

    Steps:
        1. Keep the games played before the target week with `played_before`
           above, so the window reaches into last season when the week being
           built for is early in a new one.
        2. Narrow to his position, and cut EVERY player to his own last few
           games with `last_games` above -- see the note.
        3. Total each peer's snaps across his window and keep those above the
           floor. He is kept whatever his own snap count.
        4. Average every requested stat per player.
        5. Rank his average against theirs, one stat at a time, flipping the
           handful where a smaller number is better.
        6. Drop the stats he has no data for at all.

    Args:
        frame: Player-weeks for EVERY player and every loaded season.
        canonical_id: The player being summarised.
        fields: Which columns to average, as column names.
        season: The season being built for.
        week: The week being built for. Games from this week and later are
            excluded.
        games: How many recent games each player's window covers.
        snaps_per_game: Multiplied by the window length to get the snap floor.

    Returns:
        pd.DataFrame: One row per stat, with `stat`, `average` and `percentile`
            (0-100, higher always meaning better placed). The percentile is blank
            for a stat with no better end. Stats with no average at all are left
            out entirely.

    Note:
        EVERY PLAYER IS CUT TO HIS OWN LAST FEW GAMES, not to a fixed range of
        weeks. A player who missed three of the last five is then averaged over
        five games he actually played, which is what makes the comparison
        apples-to-apples -- and it is how the Cheat Sheet's trailing form already
        works.

        THE SNAP FLOOR DECIDES WHAT THE PERCENTILES MEAN. Ranking against
        everyone who took a snap puts a mediocre starter in the ninetieth
        percentile. Raise it to make the comparison harsher.

        THE PERCENTILE IS OF THE AVERAGE, not an average of weekly percentiles.
        Those are different numbers, and this is the one that matches the value
        shown beside it.
    """
    from presentation.dfs_gamelog import direction_of

    blank = pd.DataFrame(columns=["stat", "average", "percentile"])

    history = played_before(frame, season, week)
    his = history[history["canonical_id"] == canonical_id]
    if his.empty or not fields:
        return blank

    position = his.sort_values("_when")["position"].iloc[-1]
    window = last_games(history[history["position"] == position], games)

    snaps = window.groupby("canonical_id")["offense_snaps"].sum()
    eligible = set(snaps[snaps >= snaps_per_game * int(games)].index)
    eligible.add(canonical_id)          # never exclude the subject himself

    peers = window[window["canonical_id"].isin(eligible)]
    present = [field for field in fields if field in peers.columns]
    if not present:
        return blank

    averages = peers.groupby("canonical_id")[present].mean(numeric_only=True)

    rows = []
    for field in present:
        column = averages.get(field)
        direction = direction_of(field)

        percentile = float("nan")
        if column is not None and direction is not None:
            ranked = column.rank(pct=True) * 100
            placing = ranked.get(canonical_id, float("nan"))
            percentile = 100 - placing if direction == "lower" else placing

        rows.append({"stat": field,
                     "average": (column.get(canonical_id, float("nan"))
                                 if column is not None else float("nan")),
                     "percentile": percentile})

    # A stat with no average is one this player has NO DATA for, which is not
    # the same as a bad number and should not take up a row. It happens because
    # the tracking sources only cover part of each position: a quarterback is
    # absent from the Next Gen rushing table, and a running back rarely clears
    # the receiving thresholds.
    #
    # A BLANK PERCENTILE IS DIFFERENT and is kept -- that is a stat with a real
    # average and no better end (see UNRANKED in presentation/dfs_gamelog.py).
    summary = pd.DataFrame(rows)
    return summary[summary["average"].notna()].reset_index(drop=True)
