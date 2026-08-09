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
import pandas as pd

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
        width: How wide to draw the column: a pixel count, or one of "small",
            "medium" and "large". Streamlit silently ignores anything else, so
            "80px" does nothing.

            NONE MEANS "NOT SPECIFIED", not "no width" -- each page substitutes
            its own default for it. A number here is an OVERRIDE of that default,
            so only the handful of columns that genuinely differ need one.
    """

    field: str
    label: str
    format: str = "%.1f"
    help: str = ""
    scale: float = 1.0
    width: int | str | None = None

# The columns every log starts with: which game this was.
CONTEXT = [
    Column("week", "Wk", "%d", width=48),
    Column("opponent", "Opp", None, width=58),
]

# ---------------------------------------------------------------------------
# Fantasy -- what he actually did
# ---------------------------------------------------------------------------
FANTASY_PASSING = [
    Column("attempts", "Att", "%d"),
    Column("completions", "Cmp", "%d"),
    Column("passing_yards", "PaYd", "%d"),
    Column("passing_tds", "PaTD", "%d"),
    Column("passing_interceptions", "Int", "%d"),
]

# "Car", not "Att". A quarterback's Fantasy tab holds both groups, and two
# columns sharing a heading would share one column_config entry -- see the note
# on `shape` below.
FANTASY_RUSHING = [
    Column("carries", "Car", "%d"),
    Column("rushing_yards", "RuYd", "%d"),
    Column("rushing_tds", "RuTD", "%d"),
]

FANTASY_RECEIVING = [
    Column("targets", "Tgt", "%d"),
    Column("receptions", "Rec", "%d"),
    Column("receiving_yards", "ReYd", "%d"),
    Column("receiving_tds", "ReTD", "%d"),
]

FANTASY_ALL = [
    Column("total_fantasy_points", "FP", "%.1f",
           "Fantasy points scored, in the scoring chosen above."),
    Column("total_fantasy_points_exp", "xFP", "%.1f",
           "What his opportunities were worth on average, whatever happened "
           "to them."),
]

# ---------------------------------------------------------------------------
# Opportunities -- how much of the offence ran through him
# ---------------------------------------------------------------------------
OPPS_RUSHING = [
    Column("carries", "Car", "%d"),
    Column("carry_share", "Car%", "%.0f%%", "Share of his team's carries.",
           scale=100),
    Column("adj_opportunities", "AdjOpp", "%.1f",
           "Carries plus twice targets. A target is worth about two carries, so "
           "this compares a pass-catching back with a between-the-tackles back "
           "on one number."),
    Column("red_zone_carries", "RZC", "%d", "Carries inside the twenty."),
    Column("red_zone_carry_share", "RZC%", "%.0f%%",
           "Share of his team's carries inside the twenty.", scale=100),
    Column("inside_5_carries", "In5", "%d", "Carries from inside the five."),
    Column("inside_5_carry_share", "In5%", "%.0f%%",
           "Share of his team's carries from inside the five.", scale=100),
    Column("goal_line_carries", "GL", "%d",
           "Carries with a goal-to-go line to gain."),
    Column("goal_line_carry_share", "GL%", "%.0f%%",
           "Share of his team's goal-to-go carries.", scale=100),
]

OPPS_RECEIVING = [
    Column("targets", "Tgt", "%d"),
    Column("target_share", "Tgt%", "%.0f%%", "Share of his team's targets.",
           scale=100),
    Column("red_zone_targets", "RZT", "%d", "Targets inside the twenty."),
    Column("red_zone_target_share", "RZT%", "%.0f%%",
           "Share of his team's targets inside the twenty.", scale=100),
    Column("inside_5_targets", "In5T", "%d", "Targets from inside the five."),
    Column("inside_5_target_share", "In5T%", "%.0f%%",
           "Share of his team's targets from inside the five.", scale=100),
    Column("end_zone_targets", "EZT", "%d",
           "Targets thrown as far as the goal line -- a shot at a touchdown, "
           "caught or not."),
    Column("end_zone_target_share", "EZT%", "%.0f%%",
           "Share of his team's end-zone targets.", scale=100),
    Column("receiving_air_yards", "AirYd", "%d",
           "How much downfield volume was aimed at him."),
    Column("air_yards_share", "AY%", "%.0f%%",
           "Share of his team's air yards, from play-by-play.", scale=100),
    Column("percent_share_of_intended_air_yards", "iAY%", "%.1f%%",
           "Next Gen Stats' own share of intended air yards. Nearly the same "
           "thing as AY% from a different source."),
    Column("receiving_yards_after_catch", "YAC", "%d"),
    Column("wopr", "WOPR", "%.2f",
           "Target share and air-yards share combined. Higher means a bigger "
           "role in the passing game."),
]

OPPS_ALL = [
    Column("snap_share", "Snap%", "%.0f%%",
           "Share of the offence's snaps he was on the field for. Usually moves "
           "before the production does.", scale=100),
]

# ---------------------------------------------------------------------------
# Advanced -- what he did with what he got
# ---------------------------------------------------------------------------
# Each EPA is prefixed. A quarterback's Advanced tab holds passing AND rushing,
# a back's holds rushing AND receiving, so a bare "EPA" would collide.
ADVANCED_PASSING = [
    Column("passing_epa", "PaEPA", "%.2f", "Expected points added by his throws."),
    Column("completion_pct", "Cmp%", "%.1f%%", "Completion percentage.", scale=100),
    Column("passing_cpoe", "CPOE", "%.1f",
           "Completion percentage over expected: how often he completes passes "
           "compared with a typical quarterback attempting the same throws."),
    Column("avg_time_to_throw", "TT", "%.2f", "Seconds from snap to release."),
    Column("pacr", "PACR", "%.2f", "Passing yards per air yard thrown."),
    Column("aggressiveness", "Agg%", "%.1f%%",
           "Share of throws made into tight coverage."),
]

ADVANCED_RUSHING = [
    Column("rushing_epa", "RuEPA", "%.2f", "Expected points added by his carries."),
    Column("efficiency", "Eff", "%.2f",
           "How directly he runs. Lower means less east-west movement."),
    Column("rushing_yards_before_contact_avg", "YBC", "%.2f",
           "Yards before contact per carry -- mostly a measure of his line."),
    Column("rushing_yards_after_contact_avg", "YACon", "%.2f",
           "Yards after contact per carry -- mostly a measure of him."),
    Column("rush_yards_over_expected_per_att", "RYOE", "%.2f",
           "Rushing yards over expected per carry, given the blocking and the box."),
    Column("percent_attempts_gte_eight_defenders", "Stack%", "%.1f%%",
           "Share of his carries against eight or more defenders in the box."),
]

ADVANCED_RECEIVING = [
    Column("receiving_epa", "ReEPA", "%.2f", "Expected points added by his catches."),
    Column("avg_cushion", "Cush", "%.2f",
           "Yards the defender gave him at the snap."),
    Column("avg_separation", "Sep", "%.2f",
           "Yards of separation from the nearest defender at the catch point."),
    Column("avg_intended_air_yards", "aDOT", "%.1f",
           "Average depth of target: how far downfield he is thrown to."),
    Column("avg_yac", "aYAC", "%.1f", "Average yards after catch per reception."),
    Column("avg_expected_yac", "xYAC", "%.1f",
           "Yards after catch a typical receiver would gain from the same "
           "catches. Compare with aYAC beside it."),
    Column("receiving_drop_pct", "Drop%", "%.1f%%", scale=100),
    Column("wopr", "WOPR", "%.2f",
           "Target share and air-yards share combined."),
]

LOWER_IS_BETTER = frozenset({
    "passing_interceptions",
    "efficiency",                            # east-west movement; less is more direct
    "percent_attempts_gte_eight_defenders",   # stacked boxes
    "receiving_drop_pct",
})
"""Stats where a SMALLER number is the better one.

Four exceptions in a catalogue where everything else rewards more. Getting one
wrong paints a bad week as a good one and still looks entirely plausible, which
is why they are listed rather than inferred from the name.
"""

UNRANKED = frozenset()
"""Stats to show as a number with no ranking and no colour.

EMPTY ON PURPOSE, and it was not always. Time to throw, aggressiveness and
average cushion sat here because each cuts both ways in isolation -- a quick
release is good until it means he is not looking downfield; a defender playing
off is easy yards and also a sign of no respect. Leaving them unranked was
defensible and read as broken, which is worse: a blank cell looks like a
calculation that fell over, whatever the tooltip says.

They are now ranked higher-is-better with everything else, which is defensible
for all three FROM A FANTASY POINT OF VIEW even though it is a convention rather
than a fact:

  * a longer time to throw goes with deeper throws, so with more upside
  * more throws into tight coverage means more downfield volume
  * more cushion means easier completions underneath

Add one back here the day you decide its ranking is misleading. Nothing else has
to change -- `direction_of` below returns None for anything listed, and both the
summary column and the Cheat Sheet already draw that as a number with no colour.
"""

def direction_of(field):
    """Say which end of a stat counts as good, if either does.

    Steps:
        1. Return nothing for a stat that cuts both ways, so it is left
           uncoloured rather than given a direction nobody can defend.
        2. Return "lower" for the handful where a smaller number wins.
        3. Otherwise "higher", which is true of everything else here.

    Args:
        field: A column name from the game-log catalogue.

    Returns:
        str | None: "higher", "lower", or None for no ranking at all.

    Note:
        Defaults to "higher" rather than being opt-in, which is the OPPOSITE of
        the Cheat Sheet's SCALE_DIRECTIONS. The two catalogues differ: this one
        is a curated set of performance measures, every one of which has a better
        end, while the Cheat Sheet also carries salary, games played and injury
        flags, which do not.
    """
    if field in UNRANKED:
        return None
    return "lower" if field in LOWER_IS_BETTER else "higher"

def tabs_for(position):
    """Choose which tables to show, and which sub-groups go in each.

    Three questions, asked in order: what he did, how much of the offence ran
    through him, and what he did with it. Each is one tab, and inside a tab the
    sub-groups are the halves of the game he actually plays.

    Steps:
        1. Work out which halves of the game apply. A quarterback throws and
           runs; a back runs and catches; everybody else catches.
        2. Build each tab from those halves, in the same order every time, and
           finish with the group that applies to everyone.
        3. Leave out a sub-group with nothing in it, so a receiver's Advanced tab
           does not carry an empty Passing banner.

    Args:
        position: The player's position, such as `"WR"`. Anything unrecognised
            is treated as a pass-catcher, which is the commonest case.

    Returns:
        dict: Tab name to a list of `(sub_category, columns)` pairs, in display
            order. The sub-category is what `shape` below draws as a banner over
            its columns; the tab names are what the page draws as headings.
    """
    passes = position == "QB"
    runs = position in ("QB", "RB")
    catches = position != "QB"

    def groups(passing, rushing, receiving, everyone=()):
        """Keep the sub-groups this position actually has, in a fixed order."""
        pairs = []
        if passes and passing:
            pairs.append(("Passing", list(passing)))
        if runs and rushing:
            pairs.append(("Rushing", list(rushing)))
        if catches and receiving:
            pairs.append(("Receiving", list(receiving)))
        if everyone:
            pairs.append(("All", list(everyone)))
        return pairs

    return {
        "Fantasy": groups(FANTASY_PASSING, FANTASY_RUSHING, FANTASY_RECEIVING,
                          FANTASY_ALL),
        "Opportunities": groups((), OPPS_RUSHING, OPPS_RECEIVING, OPPS_ALL),
        "Advanced": groups(ADVANCED_PASSING, ADVANCED_RUSHING,
                           ADVANCED_RECEIVING),
    }

def shape(frame, groups):
    """Cut a player's rows down to one tab's columns, most recent game first.

    Steps:
        1. Flatten the tab's sub-groups into one ordered list, keeping which
           group each column came from.
        2. Drop the columns the data does not actually have -- a source can be
           missing for a season, and its columns go with it.
        3. Put the most recent week at the top, which is the one being decided
           about.
        4. Apply each column's own scale, which is how a fraction becomes a
           percentage. Only the columns that ARE fractions carry one.
        5. Give the table two-level headings, so each sub-group is drawn as a
           banner over its columns. The context columns get an empty group,
           which renders as no banner.

    Args:
        frame: One player's rows from `player_weeks`.
        groups: The `(sub_category, columns)` pairs for this tab, from
            `tabs_for` above.

    Returns:
        tuple: `(table, columns)` -- the rows to draw, with two-level headings,
            and the `Column` records that survived in the same order, so the
            caller can build matching column settings.

    Note:
        HEADINGS MUST BE UNIQUE WITHIN A TAB. Streamlit names a two-level column
        after its LEAF, and column_config is keyed on that name -- so two columns
        sharing a heading would share one config entry, and the second one's
        format and tooltip would silently be the first one's. That is why rushing
        attempts are "Car" and each EPA carries its own prefix.
    """
    pairs = [("", column) for column in CONTEXT]
    pairs += [(name, column) for name, columns in groups for column in columns]

    present = [(name, column) for name, column in pairs
               if column.field in frame.columns]
    columns = [column for _, column in present]

    # Sorted by the SEASON-AND-WEEK number when it is there. Sorting by week
    # alone puts last season's week 18 above this season's week 1, which is
    # exactly backwards -- and the window crosses that boundary whenever the
    # target week is early in a new year.
    order = "_when" if "_when" in frame.columns else "week"
    ordered = frame.sort_values(order, ascending=False)

    table = ordered[[column.field for column in columns]].copy()

    for column in columns:
        if column.scale != 1.0:
            table[column.field] = table[column.field] * column.scale

    table.columns = pd.MultiIndex.from_tuples(
        [(name, column.label) for name, column in present]
    )
    return table, columns


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

def selectable_stats(position):
    """List every stat this position's game logs carry, for a picker.

    The trend plot lets you choose what to draw, and the honest answer to "what
    can I choose" is "whatever the logs already show him". Taking it from
    `tabs_for` above means the two can never drift apart.

    Steps:
        1. Walk every tab and every sub-group for this position.
        2. Collect each column's field and heading, keeping the FIRST heading
           seen for a field -- several appear in more than one tab, and they are
           the same statistic each time.

    Args:
        position: The player's position, such as "WR".

    Returns:
        dict: Maps a column name to its short heading, in catalogue order. Ready
            for a multiselect's options and its `format_func`.
    """
    labels = {}
    for groups in tabs_for(position).values():
        for _, columns in groups:
            for column in columns:
                labels.setdefault(column.field, column.label)
    return labels


def percentile_frame(table, when_by_row, ranks, columns):
    """Line weekly percentiles up with the cells of a game log.

    The log is one row per game and one column per statistic; the rankings arrive
    as a long list of week-and-statistic pairs. This puts the second into the
    shape of the first, so a styler can colour each cell by how good that week
    was rather than by how it compares with the other rows on screen.

    Steps:
        1. Spread the long rankings into a grid, one row per week and one column
           per statistic.
        2. Build an empty grid matching the table exactly, headings and all.
        3. Fill each column by looking its statistic up and reordering it to
           match the table's rows.

    Args:
        table: `shape`'s table, with two-level headings.
        when_by_row: The season-and-week number for each of the table's rows, in
            the table's own order.
        ranks: `weekly_percentiles`' long output, carrying `when`, `stat` and
            `percentile`.
        columns: `shape`'s `Column` records, in the table's column order.

    Returns:
        pd.DataFrame: The same shape and headings as `table`, holding a
            percentile from 0 to 100 per cell. NaN wherever a stat was not
            ranked, or the week could not be matched -- which a styler draws as
            no colour.
    """
    empty = pd.DataFrame(float("nan"), index=table.index, columns=table.columns)
    if ranks.empty:
        return empty

    grid = ranks.pivot(index="when", columns="stat", values="percentile")

    for heading, column in zip(table.columns, columns):
        if column.field in grid.columns:
            empty[heading] = grid[column.field].reindex(when_by_row).to_numpy()
    return empty
