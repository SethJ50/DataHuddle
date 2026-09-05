"""Builds the read-only board summarising a draft plan, one row per pick.

The Draft Plan page's tabs let you work on ONE round at a time, which makes it
hard to see the shape of the whole plan. This is the opposite view: every pick
you own on the left, every position across the top, and each cell listing the
players saved for that round and position in priority order.

Split into two functions on purpose. `build_summary` does the data shaping and
knows nothing about how it looks; `summary_html` does the rendering and knows
nothing about where the plan came from. That means the layout can be swapped
without touching the logic, and the logic can be tested without a browser.
"""

import html

import pandas as pd

from presentation.colors import POSITION_TINTS

# The positions the Draft Plan page has a tab for. K and DST are deliberately
# absent: RosterService's player universe comes from UDK's rankings, which do
# not cover them, so there is nothing to plan with.
POSITIONS = ("QB", "RB", "WR", "TE")

# Where "he'll probably be there" turns into "coin flip" turns into "he's gone".
# Deliberately not symmetric: planning around a 55% player is a real gamble, so
# the safe band starts high, while anything under a quarter is effectively a
# wish rather than a plan.
SAFE_ABOVE = 0.70
GONE_BELOW = 0.25

# The table's own styling. Sent alongside the table in one st.markdown call.
# Every colour is either inherited or a grey with an alpha, so the SAME sheet
# reads correctly on Streamlit's light and dark surfaces with no theme check --
# the surface underneath sets the lightness.
_STYLE = """
<style>
.dh-plan-summary { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.dh-plan-summary th, .dh-plan-summary td {
    border: 1px solid rgba(128, 128, 128, 0.25);
    padding: 0.35rem 0.6rem;
    text-align: left;
    vertical-align: top;
    line-height: 1.5;
}
.dh-plan-summary th { font-weight: 600; }
.dh-plan-summary td.dh-pick {
    white-space: nowrap;
    font-variant-numeric: tabular-nums;  /* digits line up column-wise */
    opacity: 0.7;
    width: 1%;                           /* shrink to fit "12.09" */
}
.dh-plan-summary td.dh-empty { opacity: 0.3; }
/* The availability percentage trailing each name. Smaller and dimmed so the
   names stay the thing you read; the colour answers "can I wait?" before the
   digits do. Colours are rgba over the inherited surface, so one value reads
   correctly on both the light and the dark theme. */
.dh-plan-summary .dh-prob {
    font-size: 0.78em;
    font-variant-numeric: tabular-nums;
    margin-left: 0.35em;
    opacity: 0.85;
}
.dh-plan-summary .dh-prob-safe   { color: rgb(34, 160, 90); }
.dh-plan-summary .dh-prob-toss   { color: rgb(198, 138, 20); }
.dh-plan-summary .dh-prob-gone   { color: rgb(208, 66, 66); }
.dh-plan-summary tbody tr:hover { background: rgba(128, 128, 128, 0.08); }
</style>
"""


def round_of_label(label):
    """Read the round number out of a "ROUND.PICK" label such as "3.04".

    Plan entries are stored under the label that was current when they were
    saved, and that label's second half depends on your draft position. Reading
    the round out of it means an entry still lands on the right row after you
    move slots. Mirrors `round_of_pick` in services/draft_runner_service.py,
    which solves the same problem for the live console.

    Steps:
        1. Split the label on its dot and turn the first half into a whole
           number.
        2. Return None for anything that does not parse, so a malformed entry is
           skipped rather than taking the page down.

    Args:
        label: A round label such as "3.04". Anything else is tolerated.

    Returns:
        int | None: The round number, or None if the label could not be read.
    """
    try:
        return int(str(label).split(".")[0])
    except (ValueError, AttributeError, IndexError):
        return None


def build_summary(saved_plan, pick_labels, positions=POSITIONS,
                  availability=None):
    """Turn a saved draft plan into one row per pick and one column per position.

    The data half of the summary board. It fills in a row for EVERY pick you
    own, including the ones you have not planned yet, so the board always has
    the same number of rows and reads as a complete draft-day sheet.

    Steps:
        1. Regroup the saved plan by round NUMBER and position, rather than by
           the full round label — see `round_of_label` above for why.
        2. Walk the picks you own, in draft order.
        3. For each, build a row holding the pick's label plus one entry per
           position: the list of players saved there, or an empty list.
        4. Pair every player with his chance of surviving to THAT ROW'S pick,
           which is the pick you would actually spend on him.

    Args:
        saved_plan: What `DraftPlanService.get_plan` returns — a dictionary keyed
            by a `(round_label, position)` pair, with a list of player display
            names in priority order as the value. None and {} both mean "nothing
            planned yet" and are fine to pass.
        pick_labels: What `DraftPlanService.pick_labels` returns — one dictionary
            per round, each with at least `round` and `label`.
        positions: Which positions get a column, in the order they appear.
        availability: Optional `{overall_pick: {player_name: probability}}`,
            giving each player's chance of lasting to each of your picks. None
            means no simulation is loaded, and every player comes back with a
            probability of None rather than the column disappearing.

    Returns:
        pd.DataFrame: One row per pick you own, in draft order, with a "Pick"
            column holding the label ("3.04") and one column per position. Each
            position cell holds a LIST of `(display_name, probability)` pairs in
            priority order, empty where nothing is planned. Pairs rather than
            joined text so the rendering half decides how they stack, and the
            probability is None wherever the simulation has nothing to say.
    """
    # {(round_number, position): [player names]} — the same plan, but keyed so a
    # changed draft position cannot orphan an entry.
    by_round = {}
    for (label, position), players in (saved_plan or {}).items():
        round_number = round_of_label(label)
        if round_number is None:
            continue
        by_round.setdefault((round_number, position), []).extend(players)

    rows = []
    for pick in pick_labels:
        row = {"Pick": pick["label"]}
        # Every player in this row is measured against THIS pick -- the one you
        # would actually spend on him -- not against some league-wide average.
        at_this_pick = (availability or {}).get(pick["overall_pick"], {})
        for position in positions:
            row[position] = [
                (name, at_this_pick.get(name))
                for name in by_round.get((pick["round"], position), [])
            ]
        rows.append(row)

    return pd.DataFrame(rows, columns=["Pick", *positions])


def probability_html(probability):
    """Render one availability probability as the dimmed text trailing a name.

    Answers "will he still be there when my turn comes?" at a glance. The colour
    carries the judgment so the board can be skimmed down a column without
    reading any digits.

    Steps:
        1. Return an empty string when there is no probability, so a player the
           simulation has never heard of simply shows his name -- a "0%" there
           would claim he is certain to be gone, which is a different and much
           stronger statement than "unknown".
        2. Pick a colour class: safe above SAFE_ABOVE, gone below GONE_BELOW,
           and a coin flip in between.
        3. Render it as a whole-number percentage in a small dimmed span.

    Args:
        probability: A number between 0 and 1, or None/NaN when the simulation
            has nothing to say about this player.

    Returns:
        str: A `<span>` ready to sit after a name, or "" for no probability.
    """
    if probability is None:
        return ""
    try:
        value = float(probability)
    except (TypeError, ValueError):
        return ""
    if value != value:          # NaN, which is not equal to itself
        return ""

    if value >= SAFE_ABOVE:
        tone = "dh-prob-safe"
    elif value < GONE_BELOW:
        tone = "dh-prob-gone"
    else:
        tone = "dh-prob-toss"

    return f'<span class="dh-prob {tone}">{value:.0%}</span>'


def summary_html(frame, positions=POSITIONS):
    """Render the summary as an HTML table, one player per line inside each cell.

    Streamlit's own `st.dataframe` gives every row the SAME height, so a board
    with one round holding four players and twelve holding none would be mostly
    empty space. A plain HTML table sizes each row to its own contents, which is
    what makes a fifteen-round board readable without scrolling.

    Steps:
        1. Build the header, tinting each position's cell with that position's
           colour from presentation/colors.py, so the summary speaks the same
           colour language as the rest of the app.
        2. Build one row per pick: the label in its own narrow cell, then each
           position's players joined by line breaks, each followed by its
           availability percentage from `probability_html` above.
        3. Show a dash rather than nothing for an empty cell, so a planned-but-
           empty position is visibly empty instead of looking unrendered.
        4. Join everything together with the stylesheet in front.

    Args:
        frame: `build_summary`'s output.
        positions: Which columns to render, in order. Must match what the frame
            was built with.

    Returns:
        str: HTML for `st.markdown(..., unsafe_allow_html=True)`.

    Note:
        Every value goes through `html.escape` before it reaches the output.
        Player names come from a data source rather than from you, and an
        unescaped "&" or "<" would break the table silently.
    """
    header_cells = ['<th class="dh-pick">Pick</th>']
    for position in positions:
        tint = POSITION_TINTS.get(position, "transparent")
        header_cells.append(
            f'<th style="background:{tint}">{html.escape(position)}</th>'
        )

    body_rows = []
    for record in frame.to_dict("records"):
        cells = [f'<td class="dh-pick">{html.escape(str(record["Pick"]))}</td>']
        for position in positions:
            players = record.get(position) or []
            if players:
                # <br> rather than a comma: priority order reads down the cell,
                # which is the order the Move arrows on the tabs below set.
                # Each entry is either a bare name or a (name, probability)
                # pair, so a caller with no simulation loaded can pass the plan
                # through unchanged.
                lines = []
                for entry in players:
                    name, probability = (entry if isinstance(entry, tuple)
                                         else (entry, None))
                    lines.append(html.escape(str(name)) + probability_html(probability))
                stacked = "<br>".join(lines)
                cells.append(f"<td>{stacked}</td>")
            else:
                cells.append('<td class="dh-empty">—</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        _STYLE
        + '<table class="dh-plan-summary">'
        + "<thead><tr>" + "".join(header_cells) + "</tr></thead>"
        + "<tbody>" + "".join(body_rows) + "</tbody>"
        + "</table>"
    )
