"""Splits a player's week-by-week numbers into the questions people ask of them.

One table with every column would be forty wide and answer nothing quickly. These
are five narrower tables, each holding the columns that go together:

- FANTASY, the box score -- what he actually did
- USAGE, how much of the offence runs through him
- EFFICIENCY, what he does with what he gets
- EXPECTED, whether the production matches the opportunity
- ADVANCED, the tracking and charting layer

Each one is also POSITION-AWARE: a quarterback's usage is dropbacks and attempts,
a receiver's is targets and target share, and showing either of them the other's
columns would be showing them a column of blanks.
"""

from typing import NamedTuple


class Column(NamedTuple):
    """One column of a game log, and how to show it.

    Attributes:
        field: The column's name in the player-week table.
        label: The short heading to show. Game logs are read across, so these
            stay abbreviated.
        format: A printf-style format, or None to leave the value alone.
        help: The longer explanation, shown on hovering the heading. This is
            where a statistic nobody could guess from four letters gets
            explained.
        scale: What to multiply the value by before showing it. 100 turns a
            fraction into a percentage. MUST BE SET EXPLICITLY rather than
            guessed from the format -- see the note.

    Note:
        THE SOURCES DISAGREE ABOUT PERCENTAGES. `snap_share` and `target_share`
        arrive as fractions between 0 and 1, while `aggressiveness` and
        `percent_attempts_gte_eight_defenders` arrive already multiplied out.
        Scaling everything that displays with a percent sign would turn a
        perfectly ordinary 40.6% into 4062%.
    """

    field: str
    label: str
    format: str = "%.1f"
    help: str = ""
    scale: float = 1.0


# The columns every log starts with: which game this was.
CONTEXT = [
    Column("week", "Wk", "%d"),
    Column("opponent", "Opp", None),
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
    Column("targets", "Tgt", "%d"),
    Column("receptions", "Rec", "%d"),
    Column("receiving_yards", "ReYd", "%d"),
    Column("receiving_tds", "ReTD", "%d"),
]

POINTS = [
    Column("total_fantasy_points", "FP", "%.1f",
           "Fantasy points actually scored, in the scoring chosen above."),
]

USAGE_SHARED = [
    Column("offense_snaps", "Snaps", "%d"),
    Column("snap_share", "Snap%", "%.0f%%",
           "Share of the offence's snaps he was on the field for. Usually moves "
           "before the production does.", scale=100),
    Column("red_zone_touches", "RZ", "%d",
           "Carries plus targets inside the twenty. A target counts whether or "
           "not it was caught -- the opportunity is the point."),
]

USAGE_RECEIVING = [
    Column("targets", "Tgt", "%d"),
    Column("target_share", "Tgt%", "%.0f%%",
           "Share of his team's targets.", scale=100),
    Column("air_yards_share", "AY%", "%.0f%%",
           "Share of his team's air yards -- how much of the downfield passing "
           "game is aimed at him.", scale=100),
    Column("wopr", "WOPR", "%.2f",
           "Weighted opportunity rating: target share and air-yards share "
           "combined into one number. Higher means a bigger role."),
]

EFFICIENCY_RECEIVING = [
    Column("receiving_yards", "ReYd", "%d"),
    Column("receiving_yards_after_catch", "YAC", "%d"),
    Column("receiving_epa", "EPA", "%.2f",
           "Expected points added by his catches -- yards weighted by how much "
           "they mattered to the drive."),
    Column("racr", "RACR", "%.2f",
           "Receiving yards per air yard. Above 1 means he gains more than the "
           "throw travelled, which is a yards-after-catch player."),
]

EFFICIENCY_RUSHING = [
    Column("rushing_yards", "RuYd", "%d"),
    Column("rushing_epa", "EPA", "%.2f",
           "Expected points added by his carries."),
]

EFFICIENCY_PASSING = [
    Column("passing_yards", "PaYd", "%d"),
    Column("passing_epa", "EPA", "%.2f", "Expected points added by his throws."),
    Column("passing_cpoe", "CPOE", "%.1f",
           "Completion percentage over expected: how often he completes passes "
           "compared with a typical quarterback attempting the same throws."),
    Column("pacr", "PACR", "%.2f",
           "Passing yards per air yard thrown."),
]

EXPECTED = [
    Column("total_fantasy_points", "FP", "%.1f"),
    Column("total_fantasy_points_exp", "xFP", "%.1f",
           "What his opportunities were worth on average, whatever happened to "
           "them."),
    Column("rush_fantasy_points_exp", "xRush", "%.1f"),
    Column("rec_fantasy_points_exp", "xRec", "%.1f"),
    Column("pass_fantasy_points_exp", "xPass", "%.1f"),
]

ADVANCED_RECEIVING = [
    Column("avg_separation", "Sep", "%.2f",
           "Yards of separation from the nearest defender at the catch point."),
    Column("avg_cushion", "Cush", "%.2f",
           "Yards the defender gave him at the snap."),
    Column("avg_intended_air_yards", "aDOT", "%.1f",
           "Average depth of target: how far downfield he is thrown to."),
    Column("avg_yac", "YAC", "%.1f"),
    Column("avg_expected_yac", "xYAC", "%.1f",
           "Yards after catch a typical receiver would have gained from the "
           "same catches. Compare it with YAC beside it."),
    Column("receiving_drop_pct", "Drop%", "%.1f%%", scale=100),
]

ADVANCED_RUSHING = [
    Column("efficiency", "Eff", "%.2f",
           "How directly he runs. Lower means less east-west movement."),
    Column("percent_attempts_gte_eight_defenders", "Stack%", "%.1f%%",
           "Share of his carries against eight or more defenders in the box."),
    Column("rush_yards_over_expected_per_att", "RYOE", "%.2f",
           "Rushing yards over expected per carry, given the blocking and the "
           "defenders in front of him."),
    Column("rushing_yards_before_contact_avg", "YBC", "%.2f",
           "Yards before contact per carry -- mostly a measure of his line."),
    Column("rushing_yards_after_contact_avg", "YAC", "%.2f",
           "Yards after contact per carry -- mostly a measure of him."),
]

ADVANCED_PASSING = [
    Column("avg_time_to_throw", "TT", "%.2f", "Seconds from snap to release."),
    Column("avg_completed_air_yards", "CAY", "%.1f",
           "Average air yards on completed passes."),
    Column("aggressiveness", "Agg%", "%.1f%%",
           "Share of throws made into tight coverage."),
]


def tabs_for(position):
    """Choose which game-log tables to show, and what goes in each.

    Steps:
        1. Start from the tables everybody gets.
        2. Add the passing columns for a quarterback and the receiving ones for
           everybody else, since those are the halves of the game each actually
           plays.
        3. Give running backs both rushing and receiving, because a back who
           catches passes is a different player from one who does not, and the
           log should show it.

    Args:
        position: The player's position, such as `"WR"`. Anything unrecognised
            is treated as a pass-catcher, which is the commonest case.

    Returns:
        dict: Tab name to the list of `Column` records to show, in order. The
            tab names are what the page draws as headings.
    """
    quarterback = position == "QB"
    back = position == "RB"

    if quarterback:
        fantasy = PASSING + RUSHING
        efficiency = EFFICIENCY_PASSING + EFFICIENCY_RUSHING
        usage = []
        advanced = ADVANCED_PASSING
    elif back:
        fantasy = RUSHING + RECEIVING
        efficiency = EFFICIENCY_RUSHING + EFFICIENCY_RECEIVING
        usage = USAGE_RECEIVING
        advanced = ADVANCED_RUSHING + ADVANCED_RECEIVING
    else:
        fantasy = RECEIVING + RUSHING
        efficiency = EFFICIENCY_RECEIVING
        usage = USAGE_RECEIVING
        advanced = ADVANCED_RECEIVING

    return {
        "Fantasy": CONTEXT + fantasy + POINTS,
        "Usage": CONTEXT + USAGE_SHARED + usage,
        "Efficiency": CONTEXT + efficiency,
        "Expected": CONTEXT + EXPECTED,
        "Advanced": CONTEXT + advanced,
    }


def shape(frame, columns):
    """Cut a player's rows down to one tab's columns, most recent game first.

    Steps:
        1. Keep the columns that are actually present -- a source can be missing
           for a season, and its columns go with it.
        2. Put the most recent game at the top, which is the one being decided
           about.
        3. Apply each column's own scale, which is how a fraction becomes a
           percentage. Only the columns that ARE fractions carry one -- see the
           note on `Column` above.

    Args:
        frame: One player's rows from `player_weeks`.
        columns: The `Column` records for this tab.

    Returns:
        tuple: `(table, present)` -- the rows to draw, and the `Column` records
            that survived, so the caller can build matching column settings.
    """
    present = [column for column in columns if column.field in frame.columns]
    table = frame[[column.field for column in present]].copy()
    table = table.sort_values(["week"], ascending=False)

    for column in present:
        if column.scale != 1.0:
            table[column.field] = table[column.field] * column.scale

    return table, present


def ordinal(rank):
    """Write a placing the way a person says it: 1st, 2nd, 3rd, 21st.

    Steps:
        1. Return a dash for a missing rank, so an unrankable row reads as
           unmeasured rather than as first.
        2. Use "th" for everything in the teens, which is the exception that
           catches naive versions out -- 11th, 12th and 13th, not 11st.
        3. Otherwise pick the suffix from the last digit.

    Args:
        rank: The placing, as a number. May be missing.

    Returns:
        str: Something like "3rd", or "—" when there is no rank.
    """
    if rank is None or rank != rank:
        return "—"

    number = int(rank)
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"
