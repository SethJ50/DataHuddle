"""Turns the league-wide strength table into the two views the panel shows.

`services.draft_runner_service.team_strength_table` does the arithmetic once and
returns every team scored on every category. This module reshapes that one table
two ways: "where do I rank on everything" and "who is best at this one thing".

Kept apart from the page so the reshaping and the ranking can be tested without
starting Streamlit, which is the same split the rest of `presentation/` uses.
"""

import numpy as np
import pandas as pd

from presentation.colors import hex_to_rgba, rank_tint
from services.draft_runner_service import LOWER_IS_BETTER

MY_ROW_TINT = hex_to_rgba("#5a6b7a", alpha=0.16)
"""The wash marking your own row in the category view. The same slate the rank
column uses, so the panel spends one colour on "this is about you" rather than
introducing a second."""


def category_label(group, category) -> str:
    """Write one category's name the way a person would say it.

    Steps:
        1. Return the points headline as-is, since "Starting Lineup total" reads
           worse than "Lineup total".
        2. Put the group after the word for a whole-lineup risk or upside row,
           so it reads "Lineup risk" rather than "Risk Lineup".
        3. Otherwise join the two in the order they are written.

    Args:
        group: The group name, such as "Starting", "Bench" or "Risk".
        category: The category within it, such as "RB" or "Lineup".

    Returns:
        str: A label such as "Starting RB", "Lineup total" or "Lineup risk".
    """
    if category == "Lineup total":
        return category
    if category == "Lineup":
        return f"Lineup {group.lower()}"
    return f"{group} {category}"


def ranks_for(row, lower_is_better=False) -> pd.Series:
    """Place every team on one category, best first.

    Steps:
        1. Flip the sign when a small number is the good one, so one ranking rule
           covers both directions.
        2. Rank the teams, giving tied teams the same place.

    Args:
        row: One category's values, indexed by team slot.
        lower_is_better: True for a category where small is good, such as a
            replacement gap.

    Returns:
        pd.Series: The rank per team, 1 being best. Teams with no value keep NaN
            rather than being ranked last -- "we could not measure this" and "you
            are worst at this" are different things.
    """
    values = row if lower_is_better else -row
    return values.rank(method="min", na_option="keep")


def my_team_frame(table, my_slot) -> pd.DataFrame:
    """Show where you stand on every category at once.

    The scannable view: the point is to spot "1st at WR, 11th at TE" without
    reading any of the numbers closely.

    Steps:
        1. Walk the categories in the order the strength table put them.
        2. Rank the teams on each with `ranks_for` above, flipping the direction
           for the groups listed in `LOWER_IS_BETTER`.
        3. Record your value, your rank, and the best and worst in the league, so
           a rank comes with a sense of how much is actually between the teams.

    Args:
        table: The frame from `team_strength_table`, categories down the side and
            teams across the top.
        my_slot: Your draft position, which is one of the column names.

    Returns:
        pd.DataFrame: Columns `Category`, `Value`, `Rank`, `Best` and `Worst`,
            one row per category, in the table's own order.

    Note:
        `Best` and `Worst` are there to stop a rank being read as a verdict.
        Being 11th of 12 matters if the leader is 200 points ahead and does not
        if the whole league is within 20, and the rank alone cannot tell those
        apart.
    """
    rows = []
    for (group, category), values in table.iterrows():
        lower_is_better = group in LOWER_IS_BETTER
        rank = ranks_for(values, lower_is_better)[my_slot]
        rows.append({
            "Category": category_label(group, category),
            "Value": values[my_slot],
            "Rank": rank,
            "Best": values.min() if lower_is_better else values.max(),
            "Worst": values.max() if lower_is_better else values.min(),
        })
    return pd.DataFrame(rows)


def category_frame(table, group, category) -> pd.DataFrame:
    """List every team on one category, best first.

    The other half of the panel: having spotted that you are 11th at tight end,
    this is where you see who the ten teams ahead of you are and by how much.

    Steps:
        1. Pull that category's row out of the table.
        2. Sort it, smallest first for the groups where small is good.
        3. Label the teams and number the places with `ranks_for` above.

    Args:
        table: The frame from `team_strength_table`.
        group: The category's group, such as "Bench".
        category: The category within it, such as "RB".

    Returns:
        pd.DataFrame: Columns `Rank`, `Team` and `Value`, ranked best first, with
            a `slot` column carrying the team number so the caller can find your
            row without parsing the label back out.
    """
    values = table.loc[(group, category)]
    lower_is_better = group in LOWER_IS_BETTER

    ranked = values.sort_values(ascending=lower_is_better, na_position="last")
    ranks = ranks_for(values, lower_is_better)

    return pd.DataFrame({
        "Rank": [ranks[slot] for slot in ranked.index],
        "Team": [f"Team {slot}" for slot in ranked.index],
        "Value": ranked.to_numpy(),
        "slot": list(ranked.index),
    })


def shade_ranks(out_of, column="Rank"):
    """Build a styling function that shades a rank column light to dark.

    Steps:
        1. Define an inner function that pandas calls with the whole table.
        2. Shade each rank cell with `rank_tint` from presentation/colors.py.

    Args:
        out_of: How many teams are being ranked, so a rank can be turned into a
            share of the league.
        column: Which column holds the rank.

    Returns:
        A function suitable for `frame.style.apply(fn, axis=None)`.
    """
    def _style(frame):
        styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
        position = frame.columns.get_loc(column)
        for row, rank in enumerate(frame[column]):
            tint = rank_tint(rank, out_of)
            if tint:
                styles.iloc[row, position] = f"background-color: {tint}"
        return styles

    return _style


def highlight_my_team(slots, my_slot):
    """Build a styling function that washes your own row in the category view.

    Steps:
        1. Define an inner function that pandas calls with the whole table.
        2. Tint every cell of the row whose team slot is yours.

    Args:
        slots: The team slot for each row, in row order -- the `slot` column
            `category_frame` above returns.
        my_slot: Your draft position.

    Returns:
        A function suitable for `frame.style.apply(fn, axis=None)`.

    Note:
        Your row moves as the ranking changes, which is exactly why it needs
        marking: in a twelve-row table sorted by value there is no fixed place to
        look for yourself.
    """
    def _style(frame):
        styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for row, slot in enumerate(slots):
            if slot == my_slot:
                styles.iloc[row] = f"background-color: {MY_ROW_TINT}"
        return styles

    return _style


def category_options(table) -> list:
    """List the categories the panel can show, ready for a dropdown.

    Steps:
        1. Walk the table's row labels, which are already in reading order.
        2. Drop any category that could not be measured for a single team, since
           choosing it would only ever show an empty table.

    Args:
        table: The frame from `team_strength_table`.

    Returns:
        list: (group, category) pairs. Empty if nothing could be measured, which
            happens before anybody has a spare player on their bench.
    """
    return [key for key, values in table.iterrows()
            if np.isfinite(values.to_numpy(dtype=float)).any()]
