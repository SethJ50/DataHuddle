"""One position colour palette, shared by every view that colours by position.

Defined once so a running back is the same colour on the draft board and in the
cost-of-waiting chart. Two views that each picked their own colours would teach
you two different colour languages for the same thing.

WHY THESE FOUR HUES
-------------------
They were not chosen by eye. The set was validated with the data-viz palette
checker in BOTH light and dark themes, over ALL pairs -- the right test here,
because any position can sit next to any other on a draft board:

    light   worst all-pairs colour-blind separation  dE 13.0   (>= 8 target)
            worst normal-vision separation           dE 19.6   (>= 15 floor)
    dark    worst all-pairs colour-blind separation  dE  6.9   (floor band)
            worst normal-vision separation           dE 19.3

Two conditions came with that pass, and this app satisfies both:

  * Dark's 6.9 sits in the "floor band", legal only with SECONDARY ENCODING.
  * Light flags yellow and magenta below 3:1 contrast, which obliges "relief"
    -- visible labels or a table view.

Every board cell contains the player and his position as text, and every bar is
labelled on its axis. Colour is therefore reinforcement and never the only thing
carrying identity, which is exactly what both conditions ask for.

WHY ONLY FOUR
-------------
Six distinct hues cannot clear the all-pairs floors in both themes -- checked
exhaustively, no combination of six does. Rather than ship two colours nobody can
tell apart, K and DST fold into one neutral. That is also honest about the
domain: they have no projections, no value over replacement, and are left out of
the cost-of-waiting chart entirely. They really are "everything else".
"""

from draft_model.config import POSITIONS

# Solid hues, for marks that sit ON the surface -- bars, dots, legend swatches.
# Assigned in POSITIONS order and never cycled: a fifth hue is not invented, it
# folds into the neutral below.
POSITION_COLORS = {
    "QB":  "#2a78d6",   # blue
    "RB":  "#eda100",   # yellow
    "WR":  "#e87ba4",   # magenta
    "TE":  "#008300",   # green
    "K":   "#898781",   # neutral -- folded, see the module docstring
    "DST": "#898781",
}

# The same four stepped for a dark surface. NOT an automatic lightening: these
# are the palette's own dark steps, validated as a set against the dark surface.
POSITION_COLORS_DARK = {
    "QB":  "#3987e5",
    "RB":  "#c98500",
    "WR":  "#d55181",
    "TE":  "#008300",
    "K":   "#898781",
    "DST": "#898781",
}

# How opaque a cell tint is. Low enough that the text on top stays readable and
# the app's own light/dark surface still shows through -- which is what lets ONE
# set of tints work in both themes without a media query, something a pandas
# Styler cannot express anyway.
TINT_ALPHA = 0.20


def hex_to_rgba(color, alpha=TINT_ALPHA):
    """Turn a solid hex colour into a translucent CSS colour.

    Table cells are tinted rather than filled: the text has to stay readable, and
    a translucent wash lets the surface underneath set the lightness. That is why
    the same value works in both themes.

    Steps:
        1. Drop the leading "#".
        2. Read the three pairs of hex digits as red, green and blue.
        3. Format them as a CSS `rgba(...)` with the given alpha.

    Args:
        color: A solid colour as "#rrggbb".
        alpha: How opaque, from 0 (invisible) to 1 (solid).

    Returns:
        str: A CSS colour such as "rgba(42, 120, 214, 0.2)".
    """
    raw = color.lstrip("#")
    red, green, blue = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


# Ready-made cell tints, one per position. Built from the LIGHT hues on purpose:
# at this alpha the surface dominates, so a single set reads correctly in both
# themes and there is nothing to switch.
POSITION_TINTS = {
    position: hex_to_rgba(POSITION_COLORS[position]) for position in POSITIONS
}


def position_color(position, dark=False):
    """Look up the solid colour for one position.

    Steps:
        1. Choose the light or dark set.
        2. Look the position up, falling back to the neutral for anything
           unrecognised rather than raising -- a missing colour should not take a
           page down.

    Args:
        position: A position name such as "RB".
        dark: True for the dark-surface step.

    Returns:
        str: A solid colour as "#rrggbb".
    """
    palette = POSITION_COLORS_DARK if dark else POSITION_COLORS
    return palette.get(position, "#898781")


def position_legend_html():
    """Build a small colour key showing which tint means which position.

    Colour is only readable as a code once you know the code. Streamlit's own
    coloured-text markup (`:blue[...]`) offers a fixed handful of named colours
    that do NOT include this palette's yellow or magenta, so a legend built from
    it would show swatches that disagree with the cells. Emitting the real tints
    as HTML keeps the key and the board honest with each other.

    Steps:
        1. For each position in order, build a small inline swatch carrying that
           position's actual tint, followed by its name.
        2. Fold the folded positions into one entry -- K and DST share a colour,
           so listing them twice would imply they differ.
        3. Join them into a single line of HTML.

    Returns:
        str: HTML for `st.markdown(..., unsafe_allow_html=True)`.
    """
    swatch = ('<span style="display:inline-block;width:0.75rem;height:0.75rem;'
              'background:{tint};border-radius:2px;margin-right:0.3rem;'
              'vertical-align:-1px"></span>{label}')

    parts = [swatch.format(tint=POSITION_TINTS[p], label=p)
             for p in ("QB", "RB", "WR", "TE")]
    parts.append(swatch.format(tint=POSITION_TINTS["K"], label="K / DST"))

    return ('<div style="font-size:0.8rem;opacity:0.8">'
            + "&nbsp;&nbsp;".join(parts) + "</div>")


RANK_HUE = "#5a6b7a"
"""The one hue the strengths panel shades a rank with. A low-chroma slate, kept
deliberately apart from every position colour so a dark cell in the rank column
can never be misread as "this row is about quarterbacks"."""


def rank_tint(rank, out_of, hue=RANK_HUE):
    """Shade a rank from pale (worst) to solid (best), in one hue.

    A rank of 3 out of 12 is good news, and a number alone makes you work that
    out. Shading turns the column into something you read at a glance, which is
    the whole point of the "where am I strong" view.

    Steps:
        1. Return no colour at all for a missing rank, so an unrankable row stays
           visibly blank instead of pretending to be last.
        2. Turn the rank into a 0-to-1 strength, where 1st is strongest.
        3. Scale the alpha over that strength and build the colour with
           `hex_to_rgba` above.

    Args:
        rank: Where this team placed, 1 being best. NaN or None for no rank.
        out_of: How many teams were ranked.
        hue: The colour to shade. One hue only -- see the note.

    Returns:
        str: A CSS colour, or an empty string for no shading.

    Note:
        ONE HUE, VARYING IN STRENGTH. This is a magnitude scale, and a magnitude
        scale gets a single hue running light to dark -- two hues would read as
        two categories. It stays translucent for the same reason the position
        tints do: the surface underneath sets the lightness, so one set of values
        works in both the light and dark themes, and a pandas Styler emits plain
        inline CSS with nowhere to put a media query anyway.

        The rank is always printed in the cell as well, so this never carries the
        meaning on its own.
    """
    if rank is None or not float(rank) == float(rank) or out_of < 2:
        return ""
    strength = (out_of - float(rank)) / (out_of - 1)
    return hex_to_rgba(hue, alpha=0.08 + 0.34 * strength)
