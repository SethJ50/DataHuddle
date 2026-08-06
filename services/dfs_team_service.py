"""Turns play-by-play into a picture of how each offence behaves.

Not how good a team is -- how it PLAYS. How often it throws when the score is
not forcing it to, how quickly it snaps the ball, how many plays it runs, how
often it reaches the red zone. Those are the things that decide how many chances
a team's players get, and chances are what fantasy points are made of.

Everything here reduces roughly 50,000 plays a season down to 32 rows.
"""

import pandas as pd

# ---------------------------------------------------------------------------
# What counts as a normal play, in a normal situation
# ---------------------------------------------------------------------------

OFFENSIVE_PLAY_TYPES = ("run", "pass")
"""The only two play types that say anything about play-calling.

Kicks and punts are not choices in the same sense, and nflreadpy already files
kneel-downs and spikes under their own play types, so this one test excludes
those too.
"""

NEUTRAL_WIN_PROBABILITY = (0.20, 0.80)
"""How close the game has to be for play-calling to mean anything.

Outside this band a team is either running out the clock or throwing because it
has to. Both tell you about the scoreboard rather than about the offence, and
including them makes a good team look run-heavy and a bad one look pass-happy.
"""

NEUTRAL_LAST_QUARTER = 3
"""The last quarter counted as neutral.

Fourth-quarter play-calling is driven by the score even when the win probability
still looks close, because there is no longer time to recover from being wrong.
"""

MAX_SECONDS_BETWEEN_PLAYS = 60
"""Longest believable gap between two snaps on the same drive.

Anything longer is a timeout, an injury, a review or a television break rather
than the offence taking its time, and a handful of five-minute gaps would drag a
team's average pace badly.
"""

RED_ZONE_YARDS = 20
"""How close to the goal line the red zone starts."""


def neutral_plays(plays):
    """Narrow play-by-play to ordinary plays in situations that are still close.

    The definition of "neutral script", in one place, so every number built on
    it agrees and so it can be changed once.

    Steps:
        1. Keep only runs and passes -- see `OFFENSIVE_PLAY_TYPES` above.
        2. Keep only plays where the game is still close, using the win
           probability band above.
        3. Drop the fourth quarter and overtime.
        4. Drop two-point conversions, which are neither a normal down nor a
           normal distance and would otherwise count as pass attempts.

    Args:
        plays: A play-by-play frame from `DfsReadRepo.pbp`.

    Returns:
        pd.DataFrame: The subset that counts. Roughly half the runs and passes in
            a season survive.

    Note:
        THERE IS NO AGREED DEFINITION of neutral script, so this is a choice
        rather than a fact, and a site quoting different numbers is probably not
        wrong -- it has drawn the line somewhere else. That is exactly why the
        page states these rules where they can be read.
    """
    low, high = NEUTRAL_WIN_PROBABILITY

    keep = (
        plays["play_type"].isin(OFFENSIVE_PLAY_TYPES)
        & plays["wp"].between(low, high)
        & (plays["qtr"] <= NEUTRAL_LAST_QUARTER)
        & (plays["two_point_attempt"].fillna(0) == 0)
    )
    return plays.loc[keep]


def offensive_tendencies(repo, season, weeks=None) -> pd.DataFrame:
    """Describe how each team's offence plays, one row per team.

    The table behind the Team Profile's offence tab and the pass-rate plot.

    Steps:
        1. Read play-by-play from the repository and keep the season and weeks
           asked for.
        2. Count each team's games and offensive plays, and work out how often
           they reach the red zone, using `_volume` below.
        3. Narrow to neutral situations with `neutral_plays` above, and from
           those work out pass rate, pass rate over expected and pace, using
           `_neutral_measures` below.
        4. Put the two halves together, keeping every team that appeared at all.

    Args:
        repo: A `DfsReadRepo`, for the play-by-play table.
        season: Which season, as a year such as 2025.
        weeks: A `(first, last)` pair, both included. None means the whole
            season.

    Returns:
        pd.DataFrame: One row per team, sorted by pass rate over expected, most
            pass-happy first. Columns:

            - `team`, `games`
            - `plays_per_game` -- offensive plays run, any situation
            - `red_zone_trips_per_game` -- drives reaching inside the 20
            - `neutral_plays` -- how many plays the next three are built from
            - `pass_rate` -- share of neutral plays that were passes, 0 to 1
            - `proe` -- pass rate over expected, in percentage points
            - `seconds_per_play` -- pace, on neutral plays

            Empty with those columns present if the season has no data.

    Note:
        THE THREE NEUTRAL MEASURES ARE NaN, NOT ZERO, for a team without enough
        qualifying plays. A team whose games were all blowouts genuinely has no
        neutral pass rate, and calling that zero would put them bottom of the
        league for being in one-sided games.

        `plays_per_game` and `red_zone_trips_per_game` count EVERY situation, not
        just neutral ones. They measure how much football a team plays, and a
        team trailing all year really does run more plays.
    """
    columns = ["team", "games", "plays_per_game", "red_zone_trips_per_game",
               "neutral_plays", "pass_rate", "proe", "seconds_per_play"]

    plays = repo.pbp()
    plays = plays[plays["season"] == season]
    if weeks is not None:
        first, last = weeks
        plays = plays[plays["week"].between(first, last)]

    plays = plays[plays["posteam"].notna()]
    if plays.empty:
        return pd.DataFrame(columns=columns)

    volume = _volume(plays)
    measures = _neutral_measures(plays)

    # A left join from volume, so a team that played but never had a neutral
    # snap keeps its row with blanks rather than disappearing.
    table = volume.merge(measures, on="team", how="left")
    return (table[columns]
            .sort_values("proe", ascending=False, na_position="last")
            .reset_index(drop=True))


def _volume(plays) -> pd.DataFrame:
    """Count how much football each team plays, in every situation.

    Steps:
        1. Count each team's distinct games, so the per-game figures divide by
           the right number.
        2. Count their runs and passes.
        3. Find how near the goal line each drive got, and count the drives that
           reached the red zone.
        4. Turn both counts into per-game rates.

    Args:
        plays: Play-by-play for one season, already narrowed to rows that have
            an offensive team.

    Returns:
        pd.DataFrame: `team`, `games`, `plays_per_game` and
            `red_zone_trips_per_game`.
    """
    offensive = plays[plays["play_type"].isin(OFFENSIVE_PLAY_TYPES)]

    counts = offensive.groupby("posteam", as_index=False).agg(
        games=("game_id", "nunique"),
        plays=("play_id", "size"),
    )

    # One row per drive holding how close it got, then count the ones that got
    # inside the twenty. `drive` numbers restart each game, so the game has to be
    # part of the grouping.
    drives = (plays.dropna(subset=["drive"])
              .groupby(["game_id", "posteam", "drive"], as_index=False)
              .agg(closest=("yardline_100", "min")))
    trips = (drives.assign(red_zone=drives["closest"] <= RED_ZONE_YARDS)
             .groupby("posteam", as_index=False)
             .agg(red_zone_trips=("red_zone", "sum")))

    counts = counts.merge(trips, on="posteam", how="left")
    counts["plays_per_game"] = counts["plays"] / counts["games"]
    counts["red_zone_trips_per_game"] = (counts["red_zone_trips"]
                                         / counts["games"])

    return counts.rename(columns={"posteam": "team"})[
        ["team", "games", "plays_per_game", "red_zone_trips_per_game"]]


def _neutral_measures(plays) -> pd.DataFrame:
    """Work out pass rate, pass rate over expected and pace, in neutral spots.

    Steps:
        1. Narrow to neutral situations with `neutral_plays` above.
        2. Work out how long each play took, using `_seconds_between_plays`
           below, before the narrowing throws away the plays either side.
        3. Average the pass indicator to get pass rate, and the model's own
           expected pass rate alongside it.
        4. Subtract one from the other for pass rate over expected -- see the
           note.
        5. Average the play lengths for pace.

    Args:
        plays: Play-by-play for one season.

    Returns:
        pd.DataFrame: `team`, `neutral_plays`, `pass_rate`, `proe` and
            `seconds_per_play`. A team with no neutral plays is absent, and the
            caller's left join turns that into blanks.

    Note:
        PASS RATE OVER EXPECTED IS THE BETTER TENDENCY MEASURE. Raw pass rate
        confuses a team that likes throwing with one that is always behind on
        third and long. The expected figure is what a typical team would do on
        the same down, distance and field position, so the difference is what
        this offence does that others in the same spot would not.

        Reported in PERCENTAGE POINTS, matching how it is quoted everywhere
        else: +5 means they throw on five more plays in a hundred than expected.
    """
    timed = plays.copy()
    timed["seconds"] = _seconds_between_plays(timed)

    neutral = neutral_plays(timed)
    if neutral.empty:
        return pd.DataFrame(columns=["team", "neutral_plays", "pass_rate",
                                     "proe", "seconds_per_play"])

    measures = neutral.groupby("posteam", as_index=False).agg(
        neutral_plays=("play_id", "size"),
        pass_rate=("pass", "mean"),
        expected_pass_rate=("xpass", "mean"),
        seconds_per_play=("seconds", "mean"),
    )
    measures["proe"] = (measures["pass_rate"]
                        - measures["expected_pass_rate"]) * 100

    return measures.rename(columns={"posteam": "team"})[
        ["team", "neutral_plays", "pass_rate", "proe", "seconds_per_play"]]


def _seconds_between_plays(plays):
    """Work out how long each play took, from the game clock.

    Pace is how quickly an offence gets to the next snap, and no column records
    that directly -- it has to come from the difference between one play's clock
    reading and the next one's.

    Steps:
        1. Put the plays in the order they happened.
        2. Within each drive, subtract the next play's clock reading from this
           one's. The clock counts down, so this comes out positive.
        3. Throw away anything not believable as a play -- see the note.

    Args:
        plays: Play-by-play for one season, with `game_id`, `play_id`, `drive`
            and `game_seconds_remaining`.

    Returns:
        pd.Series: Seconds per play, lined up with the rows it was given. NaN for
            the last play of a drive, which has no next play to measure against,
            and for anything unbelievable.

    Note:
        GROUPED BY DRIVE so the clock is never read across a change of
        possession -- the gap between a punt and the next offence's first snap is
        not either team's pace.

        Gaps of zero or less come from plays sharing a clock reading, such as a
        penalty replayed from the same spot. Long gaps are timeouts, injuries,
        reviews and television breaks. Both are dropped rather than averaged in,
        because a handful of five-minute gaps would ruin a team's figure.
    """
    order = plays.sort_index()[["game_id", "play_id", "drive",
                               "game_seconds_remaining"]]
    order = order.sort_values(["game_id", "play_id"])

    # `diff(-1)` compares each play with the NEXT one. The clock counts down, so
    # this play's reading minus the next play's is the time the play took.
    gaps = order.groupby(["game_id", "drive"], dropna=True)[
        "game_seconds_remaining"].diff(-1)

    believable = (gaps > 0) & (gaps <= MAX_SECONDS_BETWEEN_PLAYS)
    return gaps.where(believable).reindex(plays.index)


def neutral_script_description() -> str:
    """Describe the neutral-script rules in a sentence, for the page to show.

    Built from the constants above rather than typed out again, so the caption
    can never drift away from what the filter actually does -- which would be
    worse than no caption at all.

    Returns:
        str: Something like "win probability 20-80%, quarters 1-3, two-point
            conversions excluded".
    """
    low, high = NEUTRAL_WIN_PROBABILITY
    return (f"win probability {low:.0%}–{high:.0%}, "
            f"quarters 1–{NEUTRAL_LAST_QUARTER}, "
            "runs and passes only, two-point conversions excluded")


# ---------------------------------------------------------------------------
# The other side of the ball: what a defence gives up
# ---------------------------------------------------------------------------

PLAY_KINDS = {
    "rush": {
        "play_type": "run",
        "points": "rush_fantasy_points",
        "label": "rush",
    },
    "pass": {
        "play_type": "pass",
        "points": "rec_fantasy_points",
        "label": "pass attempt",
    },
}
"""The two ways to attack a defence, and where each one's numbers come from.

`points` names the column of fantasy points the OFFENCE earned that way. For
passes that is RECEIVING points rather than the quarterback's passing points,
because the question these charts answer is which defences let pass-catchers
score -- and a pass catcher is what you are choosing between.
"""


def defensive_allowances(repo, season, weeks=None, positions=None,
                         play_kind="rush", scoring=None) -> pd.DataFrame:
    """Measure what each defence gives up, in efficiency and in fantasy points.

    Two numbers per defence, and they do not always agree. Expected points added
    says how well a defence plays; fantasy points allowed says how much it pays
    out. A defence can be sound and still bleed fantasy points -- if it is on the
    field constantly, or gives up the short catches that score in points-per-
    reception scoring -- and it is the disagreements that are worth finding.

    Steps:
        1. Read play-by-play and keep the season and weeks asked for.
        2. Count the plays of this kind each defence faced, and average the
           expected points added on them.
        3. Read the expected-points table, restate it in the chosen scoring with
           `rescore` from services/dfs_scoring.py, and keep the positions asked
           for.
        4. Work out who each offence was playing, using `_opponents` below, and
           add up the fantasy points each defence allowed.
        5. Divide those points by the plays faced, and put the two halves
           together.

    Args:
        repo: A `DfsReadRepo`.
        season: Which season, as a year.
        weeks: A `(first, last)` pair, both included. None means the whole
            season.
        positions: Which positions' points to count, such as `["RB"]`. None
            counts everyone.
        play_kind: `"rush"` or `"pass"` -- a key of `PLAY_KINDS` above.
        scoring: Which scoring to report in, a `DfsScoring` member. None uses the
            default, which is FanDuel.

    Returns:
        pd.DataFrame: One row per defence, sorted by fantasy points allowed per
            play, most generous first. Columns `team`, `games`, `plays_faced`,
            `epa_per_play`, `points_allowed` and `points_per_play`. Empty with
            those columns if the season has no data.

    Raises:
        KeyError: If `play_kind` is not one of `PLAY_KINDS`.

    Note:
        THE RATE IS PER PLAY FACED, NOT PER PLAY THOSE PLAYERS TOUCHED. Filtering
        to running backs and dividing by every rush the defence faced answers
        "how many back-points does a rush against this defence tend to be
        worth", which is the question you have when picking one. Dividing by
        carries by backs only would answer a different question -- how efficient
        those backs were -- and would hide a defence that simply gets run at.

        EPA IS NOT FILTERED BY POSITION. It measures the defence against every
        play of that kind, because that is what defensive quality means. Only the
        fantasy side narrows to the players you are choosing between.
    """
    if play_kind not in PLAY_KINDS:
        raise KeyError(f"unknown play kind {play_kind!r}; "
                       f"expected one of {list(PLAY_KINDS)}")

    from services.dfs_scoring import DfsScoring, rescore

    kind = PLAY_KINDS[play_kind]
    columns = ["team", "games", "plays_faced", "epa_per_play",
               "points_allowed", "points_per_play"]

    plays = _in_range(repo.pbp(), season, weeks)
    plays = plays[plays["defteam"].notna()]
    if plays.empty:
        return pd.DataFrame(columns=columns)

    faced = plays[plays["play_type"] == kind["play_type"]]
    defence = faced.groupby("defteam", as_index=False).agg(
        games=("game_id", "nunique"),
        plays_faced=("play_id", "size"),
        epa_per_play=("epa", "mean"),
    ).rename(columns={"defteam": "team"})

    scored = rescore(_in_range(repo.ff_opportunity(), season, weeks),
                     scoring or DfsScoring.FANDUEL)
    if positions is not None:
        scored = scored[scored["position"].isin(list(positions))]

    allowed = scored.merge(_opponents(plays), on=["game_id", "posteam"],
                           how="inner")
    allowed = allowed.groupby("defteam", as_index=False).agg(
        points_allowed=(kind["points"], "sum"),
    ).rename(columns={"defteam": "team"})

    table = defence.merge(allowed, on="team", how="left")
    table["points_allowed"] = table["points_allowed"].fillna(0.0)
    table["points_per_play"] = table["points_allowed"] / table["plays_faced"]

    return (table[columns]
            .sort_values("points_per_play", ascending=False)
            .reset_index(drop=True))


def _in_range(frame, season, weeks):
    """Keep one season, and optionally one stretch of weeks, from any table.

    Steps:
        1. Keep the rows for that season.
        2. If a week range was given, keep the rows inside it, both ends
           included.

    Args:
        frame: Anything with `season` and `week` columns.
        season: Which season, as a year.
        weeks: A `(first, last)` pair, or None for the whole season.

    Returns:
        pd.DataFrame: The narrowed rows.
    """
    narrowed = frame[frame["season"] == season]
    if weeks is not None:
        first, last = weeks
        narrowed = narrowed[narrowed["week"].between(first, last)]
    return narrowed


def _opponents(plays) -> pd.DataFrame:
    """Work out who each offence was playing, one row per team per game.

    The bridge between the two data sources. Fantasy points are recorded against
    the team who SCORED them, and this attaches the team who gave them up.

    Steps:
        1. Take the offence and defence from every play.
        2. Reduce to one row per game per offence.

    Args:
        plays: Play-by-play with `game_id`, `posteam` and `defteam`.

    Returns:
        pd.DataFrame: `game_id`, `posteam` and `defteam`, two rows per game.

    Note:
        Built from play-by-play rather than the schedule because it comes out
        already keyed the way the join needs. Verified against real data: exactly
        two rows per game, no offence ever facing two defences, and every
        expected-points row finding its opponent -- so this can never multiply
        rows on the join.
    """
    return (plays[["game_id", "posteam", "defteam"]]
            .dropna()
            .drop_duplicates())
