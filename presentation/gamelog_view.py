"""What a player's week-by-week game log shows, and how each column is labelled.

The season-long counterpart to presentation/dfs_gamelog.py, and deliberately
narrower: that file splits a week into five tables because Daily Fantasy asks
five different questions of it. Here there is one question — what did he actually
do — so there is one table.

POSITION-AWARE, because a quarterback's log and a receiver's have almost nothing
in common. Showing every column to everyone would bury the interesting numbers in
a field of zeros.

Defines its own `Column` rather than importing the DFS one. The two halves of the
app are kept apart on purpose — different seasons, different repositories — and a
shared four-field record is not worth coupling them over.
"""

from typing import NamedTuple

import pandas as pd

import scoring

# Fallback width in pixels for a game-log column that does not name its own.
# Stat columns hold at most three or four digits, so the HEADING is what sets the
# minimum -- "PaYd" and "ReTD" are the widest things in these columns, not 118.
DEFAULT_COLUMN_WIDTH = 50


class Column(NamedTuple):
    """One column of the game log, and how to show it.

    Attributes:
        field: The column's name in the game-log table.
        label: The short heading to show. Logs are read across, so these stay
            abbreviated.
        format: A printf-style format for a number column, or None for text.
        help: The longer explanation shown on hovering the heading.
        width: Column width in pixels. Streamlit takes an int here, or one of
            "small"/"medium"/"large", and silently ignores anything else.
    """

    field: str
    label: str
    format: str = "%.1f"
    help: str = ""
    width: int = DEFAULT_COLUMN_WIDTH

# Which game this was. Season is deliberately absent: the page filters to one
# season with a selector, so a Season column would repeat the same value down
# every row.
CONTEXT = [
    Column("week", "Wk", "%d", width=45),
    Column("opponent_team", "Opp", None),
]

PASSING = [
    Column("completions", "Cmp", "%d"),
    Column("attempts", "Att", "%d"),
    Column("passing_yards", "PaYd", "%d"),
    Column("passing_tds", "PaTD", "%d"),
    Column("passing_interceptions", "Int", "%d"),
]

RUSHING = [
    Column("carries", "Car", "%d"),
    Column("rushing_yards", "RuYd", "%d"),
    Column("rushing_tds", "RuTD", "%d"),
]

RECEIVING = [
    # Targets come FIRST and before receptions on purpose: it is the opportunity
    # number, and it moves before the production does.
    Column("targets", "Tgt", "%d",
           "Passes thrown his way, caught or not. The opportunity behind the "
           "catches beside it."),
    Column("receptions", "Rec", "%d"),
    Column("receiving_yards", "ReYd", "%d"),
    Column("receiving_tds", "ReTD", "%d"),
]

POINTS = [
    Column("fantasy_points", "FP", "%.1f",
           "Fantasy points actually scored that week, in this draft's scoring "
           "format — the same rules the projections on the left are scored by."),
]

# How the game log's stat columns map onto the keys scoring.py expects. Needed
# because the two vocabularies genuinely differ: the scorer wants one
# `interceptions` and one `fumbles_lost`, while the game log splits fumbles three
# ways by how they were lost and prefixes interceptions with `passing_`.
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


def columns_for(position):
    """Choose which columns a player's game log should show, in order.

    Steps:
        1. Start with the context columns everybody gets.
        2. Add the halves of the game this position actually plays: passing then
           rushing for a quarterback, rushing then receiving for a running back,
           receiving then rushing for everyone else.
        3. Finish with fantasy points, so the row reads left to right as "what he
           did, and what it was worth".

    Args:
        position: The player's position, such as "WR". Anything unrecognised is
            treated as a pass-catcher, which is the commonest case.

    Returns:
        list: The `Column` records to show, in display order.
    """
    if position == "QB":
        stats = PASSING + RUSHING
    elif position == "RB":
        stats = RUSHING + RECEIVING
    else:
        stats = RECEIVING + RUSHING

    return CONTEXT + stats + POINTS


def add_fantasy_points(frame, fmt):
    """Score each of a player's games with the app's own scoring rules.

    nflreadpy publishes its own fantasy point columns, but they use ITS
    definition of scoring rather than your league's. Scoring the stat line here
    means the FP column is in the same currency as the projections shown beside
    it — which is the only way a game log is worth comparing against them.

    Steps:
        1. Copy the frame so the cached stats table is not modified.
        2. Build each key scoring.py expects by adding up the game log columns
           that feed it, treating a missing column as zero rather than raising —
           a season's data can be missing a column the others have.
        3. Hand the whole set to `scoring.fantasy_points` in scoring.py, which
           works on entire columns at once.

    Args:
        frame: One player's game rows, from `PlayerDirectory.get_gamelog`.
        fmt: The ScoringFormat to score under.

    Returns:
        pd.DataFrame: The input's columns plus `fantasy_points`, one value per
            game.

    Note:
        Fumbles are summed across the log's three separate columns — lost on a
        sack, on a rush, and after a catch — because scoring.py knows only "a
        fumble you lost", and all three cost the same.
    """
    frame = frame.copy()

    stats = {}
    for key, sources in SCORING_SOURCES.items():
        total = pd.Series(0.0, index=frame.index)
        for source in sources:
            if source in frame.columns:
                total = total + frame[source].fillna(0)
        stats[key] = total

    frame["fantasy_points"] = scoring.fantasy_points(stats, fmt)
    return frame


def shape(frame, columns):
    """Cut a player's games down to the chosen columns, most recent first.

    Steps:
        1. Keep only the columns the data actually has, so a column missing from
           a season is skipped rather than raising.
        2. Put the most recent week at the top, which is the one being decided
           about.

    Args:
        frame: One player's rows for ONE season, already scored by
            `add_fantasy_points` above.
        columns: The `Column` records from `columns_for` above.

    Returns:
        tuple: `(table, present)` — the rows to draw, and the `Column` records
            that survived, so the caller can build matching column settings.
    """
    present = [column for column in columns if column.field in frame.columns]
    table = frame[[column.field for column in present]].copy()
    return table.sort_values("week", ascending=False), present
