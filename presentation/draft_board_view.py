"""Draws a draft as a grid: rounds down the side, teams across the top.

Shared by the Draft Runner and the Sim Viewer, because two functions drawing
"the draft board" would drift, and the day they disagree you would not know
which one was right.

The shared piece takes `BoardEntry` records, so a caller can feed it either a
live pick log or a row of a simulated picks matrix without either knowing about
the other.
"""

from typing import NamedTuple

import pandas as pd

from presentation.colors import POSITION_TINTS


class BoardEntry(NamedTuple):
    """One pick, in the shape the grid builders below want.

    A record rather than a plain tuple because the position rides along for
    colouring, and reading `entry.position` at the call site says what it is
    where a fourth tuple slot would not.

    Attributes:
        pick: The overall pick number, 1-indexed.
        team: The drafting team's slot, 1-indexed. `snake_order` returns
            0-indexed ids, so callers deriving this from it must add one.
        label: What to show in the cell, such as "12. Bijan Robinson (RB)".
        position: The player's position, used to colour the cell. Empty for a
            pick whose position is unknown.
    """

    pick: int
    team: int
    label: str
    position: str = ""


def build_board_grid(entries, config, my_slot=None):
    """Arrange picks into a rounds-by-teams grid.

    Steps:
        1. Start an empty grid, one row per round and one column per team.
        2. For each pick, work out its round by integer-dividing the pick number.
           Subtracting 1 first makes the arithmetic count from zero, which is
           what the row index needs.
        3. Skip anything outside the board rather than raising -- a longer
           simulation than the board has rounds is a real possibility.
        4. Write the label into that team's column, converting the 1-indexed team
           slot to a 0-indexed column.
        5. Label the columns, marking your own.

    Args:
        entries: A sequence of `BoardEntry` records. Pass a LIST, not a
            generator, if you also intend to call `build_position_grid` below --
            a generator is consumed by the first call and the second would
            silently build an empty grid.
        config: The league, for its size and round count.
        my_slot: Your draft position, so your column can be marked. Optional.

    Returns:
        pd.DataFrame: One row per round labelled `R1`, `R2`, …, one column per
            team. Cells not yet picked are empty strings.

    Note:
        THE SNAKE IS IN THE DATA, NOT THE LAYOUT. Columns are fixed team slots,
        so because the draft order reverses every round, even rounds read
        right-to-left. That is exactly how a real draft board looks, and it makes
        the snake visible rather than hiding it behind a re-sorted row.
    """
    return _grid_of(entries, config, lambda entry: entry.label, my_slot=my_slot)


def build_position_grid(entries, config):
    """Arrange the same picks into a matching grid of POSITIONS.

    Exists so the board can be coloured. It has the same shape and the same
    column names as `build_board_grid` above, so a styling function can line the
    two up cell for cell.

    Steps:
        1. Build a grid the same way, writing each pick's position instead of its
           label.

    Args:
        entries: The same sequence of `BoardEntry` records the label grid was
            built from. See that function's note about generators.
        config: The league, for its size and round count.

    Returns:
        pd.DataFrame: Same shape and columns as the label grid, holding a
            position per filled cell and an EMPTY STRING elsewhere -- never None,
            which a styling function would choke on.

    Note:
        The position is carried through as data rather than parsed back out of
        "12. Bijan Robinson (RB)". Reading a display string to recover the value
        that built it works right up until a name contains a bracket.
    """
    # Column names are irrelevant here -- only the shape and order matter, since
    # the caller indexes this grid positionally against the label grid.
    return _grid_of(entries, config, lambda entry: entry.position or "")


def _grid_of(entries, config, value_for, my_slot=None):
    """Lay picks out on a rounds-by-teams grid, taking one value from each.

    The shared body of the two builders above, which differ only in what they
    write into a cell.

    Steps:
        1. Start a grid of empty strings, one row per round, one column per team.
        2. For each pick, work out its round by integer-dividing the pick number.
           Subtracting 1 first makes the arithmetic count from zero, which is
           what the row index needs.
        3. Skip anything outside the board rather than raising -- a longer
           simulation than the board has rounds is a real possibility.
        4. Write the value into that team's column, converting the 1-indexed team
           slot to a 0-indexed column.

    Args:
        entries: A sequence of `BoardEntry` records.
        config: The league, for its size and round count.
        value_for: Called with each entry to get the cell's value.
        my_slot: Your draft position, marked in the column headers. Optional.

    Returns:
        pd.DataFrame: One row per round, one column per team.
    """
    grid = [["" for _ in range(config.num_teams)] for _ in range(config.num_rounds)]

    for entry in entries:
        round_index = (entry.pick - 1) // config.num_teams
        if not 0 <= round_index < config.num_rounds:
            continue
        if not 1 <= entry.team <= config.num_teams:
            continue
        grid[round_index][entry.team - 1] = value_for(entry)

    headers = [
        f"Team {slot}" + (" (you)" if slot == my_slot else "")
        for slot in range(1, config.num_teams + 1)
    ]
    return pd.DataFrame(
        grid, columns=headers,
        index=[f"R{r}" for r in range(1, config.num_rounds + 1)],
    )


def entries_from_pick_log(picks, label_by_id, position_by_id=None):
    """Turn a live pick log into the triples `build_board_grid` above expects.

    Steps:
        1. Walk the log in order.
        2. Look each player's label up by model-table id first, falling back to
           canonical id, then to an em dash for somebody outside the pool.
        3. Mark keepers, since that slot was spent rather than chosen.
        4. Prefix the pick number, which is how a draft board is read.

    Args:
        picks: The pick log from `DraftState.picks`.
        label_by_id: Maps EITHER kind of player id to a display label such as
            "Bijan Robinson (RB)". Both keys are looked up because a keeper is
            recorded by canonical id alone, while everything else carries a
            model-table id.
        position_by_id: Maps either kind of id to the player's position, used to
            colour the cell. Omit it and cells simply go uncoloured.

    Yields:
        BoardEntry: One per pick, ready for the grid builders above.

    Note:
        The model-table id is tried FIRST because it is the one every player has.
        Team defenses have no canonical id, so a canonical-only lookup would show
        every defense as an em dash.
    """
    position_by_id = position_by_id or {}

    for entry in picks:
        player_id = entry.get("player_id")
        canonical_id = entry.get("canonical_id")

        label = label_by_id.get(player_id) or label_by_id.get(canonical_id)
        position = (position_by_id.get(player_id)
                    or position_by_id.get(canonical_id)
                    # A pick recorded without naming the player may still say
                    # WHAT was taken, which is enough to colour the cell.
                    or entry.get("position") or "")

        if label is None:
            # "Unknown (K)" tells you a kicker went here, which is most of what
            # the board is for.
            label = f"Unknown ({position})" if position else "—"
        if entry.get("source") == "keeper":
            label += " (K)"

        yield BoardEntry(entry["pick"], entry["team"],
                         f"{entry['pick']}. {label}", position)


def tint_by_position(position_grid):
    """Build a styling function that colours each board cell by position.

    Turns the board from a wall of text into something you can read at a glance:
    a positional run shows up as a block of one colour, which is the whole reason
    to look at a draft board rather than a list.

    Steps:
        1. Define an inner function that pandas calls with the label grid.
        2. For each cell, read the SAME cell of the position grid -- the two are
           built to the same shape, so they line up positionally.
        3. Look that position's tint up, leaving unfilled cells unstyled.

    Args:
        position_grid: The frame from `build_position_grid` above, the same shape
            as the label grid being styled.

    Returns:
        A function suitable for `labels.style.apply(fn, axis=None)`.

    Note:
        Position OWNS the cell colour -- your own column is not tinted on top.
        The two would fight: your column's highlight would sit over every cell in
        it, and a blue "this is you" wash is indistinguishable from a blue "this
        is a quarterback". The column header already carries "(you)", which
        marks it without spending the colour channel twice.

        The tints are translucent, so the surface underneath sets the lightness
        and one set of colours reads correctly in both light and dark themes. A
        pandas Styler emits plain inline CSS with no way to express a media
        query, so a theme-switching colour is not an option here anyway.
    """
    def _style(frame):
        """Return a same-shaped frame of CSS, one string per cell."""
        styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for row in range(len(frame)):
            for column in range(len(frame.columns)):
                tint = POSITION_TINTS.get(position_grid.iat[row, column])
                if tint:
                    styles.iat[row, column] = f"background-color: {tint}"
        return styles

    return _style


def equal_column_widths(grid, width=150):
    """Give every team column the same fixed width.

    Left to itself Streamlit sizes each column to its contents, so a round full
    of long names sits wider than one full of short ones and the grid reads as
    ragged. A draft board is a grid; it should look like one.

    Steps:
        1. Build a column config entry per column, all with the same width.

    Args:
        grid: The label grid, for its column names.
        width: Width in pixels for every column.

    Returns:
        dict: Ready to pass as `st.dataframe(..., column_config=...)`.
    """
    import streamlit as st
    return {name: st.column_config.Column(name, width=width)
            for name in grid.columns}


def cliff_frame(cliffs):
    """Turn the cliff finder's output into a small table ready to display.

    Steps:
        1. Build one row per position, in the order given -- the finder has
           already sorted them most urgent first.
        2. Keep the columns short: a countdown, the size of the drop, and how
           that drop compares to the position's usual step.

    Args:
        cliffs: The list of dictionaries from
            `draft_model.queries.positional_cliffs`.

    Returns:
        pd.DataFrame: Columns `Pos`, `Left`, `Drop` and `Steep`, most urgent
            first.
    """
    return pd.DataFrame([
        {
            "Pos": cliff["position"],
            "Left": cliff["players_before"],
            "Drop": cliff["drop"],
            "Steep": cliff["steepness"],
        }
        for cliff in cliffs
    ])


def tint_positions_column(cliffs):
    """Build a styling function that colours the cliff table's position cells.

    Ties the table to the draft board: a running back is the same colour in both
    places, so the eye can move between them without relearning the code.

    Steps:
        1. Define an inner function that pandas calls with the whole table.
        2. Tint only the `Pos` column, leaving the numbers plain -- colouring
           everything would drown the one thing the colour is for.

    Args:
        cliffs: The same list `cliff_frame` above was built from, read in order
            so the colours line up with the rows.

    Returns:
        A function suitable for `frame.style.apply(fn, axis=None)`.
    """
    def _style(frame):
        styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for row, cliff in enumerate(cliffs):
            tint = POSITION_TINTS.get(cliff["position"])
            if tint:
                styles.iloc[row, frame.columns.get_loc("Pos")] = (
                    f"background-color: {tint}")
        return styles

    return _style
