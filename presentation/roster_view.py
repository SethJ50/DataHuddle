"""Lays one team's drafted players out as a starting lineup down to the bench.

A list of drafted players tells you very little. The same list arranged into
QB / RB / RB / WR / WR / TE / FLEX tells you immediately what a team still needs,
which is the question you are actually asking when you look at someone's roster
mid-draft.

Pure pandas -- no Streamlit -- so the slotting can be tested on its own.
"""

import pandas as pd

from draft_model.queries import FLEX_POSITIONS

FILL_ORDER = ("QB", "RB", "WR", "TE", "K", "DST")
DISPLAY_ORDER = ("QB", "RB", "WR", "TE", "FLEX", "K", "DST")

def slot_labels(position, count):
    """Name the slots a position gets, numbering them only when there are several.

    "RB1" and "RB2" are useful; a lone "QB1" is just noise, since there is no QB2
    to tell it apart from.

    Steps:
        1. Return the bare position name when there is exactly one slot.
        2. Otherwise number them from 1.

    Args:
        position: The position, such as "RB".
        count: How many slots the lineup starts at that position. Zero gives an
            empty list, which is how a lineup with no kicker slot works.

    Returns:
        list: The slot labels in order, for example `["RB1", "RB2"]`.
    """

    if count == 1:
        return [position]
    return [f"{position}{n}" for n in range(1, count + 1)]

def slot_roster(players, starting_slots):
    """Arrange one team's players into their starting lineup and bench.

    Greedy and projection-driven: the best player at each position starts, and
    whoever is left after every dedicated slot is filled competes for the flex.
    That is not provably optimal, but it matches how people actually read a
    roster and is easy to explain when it surprises you.

    Steps:
        1. Sort the team's players by projection, best first, with anyone
           lacking a projection last so a blank never outranks a real number.
        2. Fill every DEDICATED slot in FILL_ORDER, taking the best unused player
           at that exact position.
        3. Fill the FLEX slots from the best unused player at any of
           FLEX_POSITIONS -- deliberately after step 2, so the flex gets a
           leftover rather than a starter.
        4. Re-emit the filled slots in DISPLAY_ORDER, which reads naturally even
           though it differs from the order they were filled in.
        5. Append everyone still unused as bench slots.

    Args:
        players: One team's drafted players, one row each, with `name`,
            `position` and `projection` columns. May be empty.
        starting_slots: The league's lineup, mapping a position to how many are
            started, for example `{"QB": 1, "RB": 2, "FLEX": 1, ...}`.

    Returns:
        list: One `(slot_label, player)` pair per slot, starters first then
            bench. `player` is a dictionary of that row, or **None** for a slot
            nobody fills yet -- which is the point of the panel. An unfilled RB2
            in round 9 is the thing you want to notice.

    Note:
        A player with no projection sinks to the bottom and so tends to land on
        the bench. Kickers and defenses have no projection in this app, which is
        why they get dedicated slots of their own rather than competing on
        points.
    """
    remaining = list(
        players.sort_values("projection", ascending=False, na_position="last")
        .to_dict("records")
    )

    def take(matching):
        """Remove and return the best remaining player at any of `matching`."""
        for i, player in enumerate(remaining):
            if player["position"] in matching:
                return remaining.pop(i)
        return None

    by_label = {}
    for position in FILL_ORDER:
        for label in slot_labels(position, int(starting_slots.get(position, 0) or 0)):
            by_label[label] = take({position})

    for label in slot_labels("FLEX", int(starting_slots.get("FLEX", 0) or 0)):
        by_label[label] = take(set(FLEX_POSITIONS))

    lineup = []
    for position in DISPLAY_ORDER:
        for label in slot_labels(position, int(starting_slots.get(position, 0) or 0)):
            lineup.append((label, by_label[label]))

    for i, player in enumerate(remaining, start=1):
        lineup.append((f"BN{i}", player))

    return lineup

def roster_frame(lineup):
    """Turn a slotted lineup into a small table ready to display.

    Steps:
        1. Walk the `(slot, player)` pairs, writing an em dash where a slot is
           still empty so the row still renders.

    Args:
        lineup: The output of `slot_roster` above.

    Returns:
        pd.DataFrame: Columns `Slot`, `Player`, `Pos` and `Proj`, one row per
            slot including the empty ones.
    """
    return pd.DataFrame([
        {
            "Slot": label,
            "Player": player["name"] if player else "—",
            "Pos": player["position"] if player else "",
            "Proj": player["projection"] if player else None,
        }
        for label, player in lineup
    ])