"""Which projected stats to show for each position, and what to call them.

The Fantasy Footballers publish the same ten stat columns for every player, but
only some of them mean anything at a given position. This file decides which
ones a position shows and in what groups, so a page can ask for "a quarterback's
stat line" without holding an opinion about what that contains.

WHY A POSITION'S MISSING GROUPS ARE LEFT OUT RATHER THAN SHOWN AS ZERO. The
projections adapter deliberately FILLS a quarterback's receiving columns with 0,
because a quarterback really does catch no passes — the zeros are true, not
missing. That makes them indistinguishable from a real projection of zero on
screen, so the honest thing is not to show the group at all.

Deliberately free of Streamlit, like every other module in presentation/.
"""

# Short heading for each stat column. Kept short on purpose: the group heading
# above a row already says whether "Yds" means passing, rushing or receiving, so
# repeating it in every label would be noise.
STAT_LABELS = {
    "passing_yards": "Yds",
    "passing_tds": "TD",
    "interceptions": "INT",
    "rushing_attempts": "Att",
    "rushing_yards": "Yds",
    "rushing_tds": "TD",
    "receptions": "Rec",
    "receiving_yards": "Yds",
    "receiving_tds": "TD",
    "fumbles_lost": "FL",
}

# Every column this module knows how to label. Used by a caller to ask the
# projections service for exactly the columns it might display.
ALL_STAT_COLUMNS = tuple(STAT_LABELS)

# Reusable group definitions, so RB's rushing block and QB's rushing block cannot
# drift apart.
_RUSHING = ("rushing_attempts", "rushing_yards", "rushing_tds")
_RECEIVING = ("receptions", "receiving_yards", "receiving_tds")

# Which groups each position shows, in the order they should appear. The most
# important group for that position comes first.
#
# Passing has no volume stat because the adapter does not carry attempts or
# completions -- yards and touchdowns are all there is.
POSITION_STAT_GROUPS = {
    "QB": (("Passing", ("passing_yards", "passing_tds")), ("Rushing", _RUSHING)),
    "RB": (("Rushing", _RUSHING), ("Receiving", _RECEIVING)),
    "WR": (("Receiving", _RECEIVING),),
    "TE": (("Receiving", _RECEIVING),),
}


def stat_groups(position):
    """List the stat groups one position's profile should show.

    Lets a page render a stat line without knowing that quarterbacks show passing
    and running backs do not.

    Steps:
        1. Look the position up in POSITION_STAT_GROUPS above.
        2. Return an empty tuple for anything unrecognised — a kicker, a defense,
           or a missing position — so the caller renders nothing rather than
           raising.

    Args:
        position: A position name such as "RB". None is accepted and returns
            nothing, which is what a page shows before a player is resolved.

    Returns:
        tuple: Pairs of `(heading, columns)`, where `heading` is text such as
            "Rushing" and `columns` is a tuple of projection column names in
            display order. Empty for a position with nothing to show.
    """
    return POSITION_STAT_GROUPS.get(position, ())


def stat_label(column):
    """Get the short heading for one projected stat column.

    Steps:
        1. Look the column up in STAT_LABELS above, falling back to the column
           name itself so a newly added stat renders as something readable rather
           than disappearing.

    Args:
        column: A projection column name, such as "receiving_yards".

    Returns:
        str: The label to show, such as "Yds".
    """
    return STAT_LABELS.get(column, column)
