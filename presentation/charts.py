"""Charts the draft pages draw, built with Altair.

Altair rather than `st.bar_chart` because the colours have to be OURS. Position
colour is a language this app teaches on the draft board, and a chart that
re-picked its own hues would teach a second, contradictory one. `st.bar_chart`
can be forced to use specific colours only by smuggling hex strings into a data
column, which then leak into the legend.

Altair is not an extra dependency: Streamlit requires it (>=4, <7), so it is
present wherever Streamlit is.
"""

import altair as alt

from presentation.colors import POSITION_COLORS, POSITION_COLORS_DARK

# Bar geometry, from the data-viz mark specs: cap the thickness rather than
# filling the band, and round only the data end so the baseline stays square and
# every bar visibly grows from the same line.
BAR_THICKNESS = 18          # <= 24px
BAR_CORNER_RADIUS = 4


def is_dark_theme():
    """Work out whether the app is currently showing its dark theme.

    The palette has separate steps for each surface -- the dark ones are not
    lightened copies, they were selected and validated against the dark
    background. Picking the wrong set would put light-surface colours on a dark
    chart, which is exactly the case they were never checked for.

    Steps:
        1. Read the active theme from Streamlit's request context.
        2. Treat anything unavailable or unrecognised as light, since that is the
           default and the safer guess.

    Returns:
        bool: True when the dark theme is active.
    """
    import streamlit as st

    theme = getattr(st.context, "theme", None)
    base = getattr(theme, "type", None) or getattr(theme, "base", None)
    return str(base).lower() == "dark"


def cost_of_waiting_chart(costs, dark=None):
    """Draw the cost of waiting as horizontal bars, one per position.

    Replaces a row of numbers. Reading five figures and ranking them is work;
    seeing which bar is longest is not, and that ranking is the whole decision
    the panel exists to support.

    Steps:
        1. Choose the palette matching the active theme.
        2. Build a horizontal bar mark, thickness capped and only the data end
           rounded, so every bar grows from one square baseline.
        3. Put cost on the x axis and position on the y, sorted longest first.
        4. Map position to colour explicitly, so a running back is the same
           colour here as on the draft board.
        5. Attach a tooltip carrying the number, so the exact value is one hover
           away without printing a figure on every bar.

    Args:
        costs: The frame from `positional_costs_for_team`, with `position`,
            `cost` and `best_available_vorp` columns. Must not be empty --
            callers check first and show a caption instead.
        dark: Force a palette instead of detecting one. Mainly for tests.

    Returns:
        alt.Chart: Ready for `st.altair_chart(..., use_container_width=True)`.

    Note:
        NO LEGEND, deliberately. Every bar is labelled with its position on the y
        axis, so identity never rests on colour alone -- which is the condition
        this palette was validated under. A legend box would restate the axis and
        cost space.

        The x axis starts at zero and is never truncated. A bar's LENGTH is the
        encoding, so a clipped axis would make a small cost look like a large
        one.
    """
    palette = POSITION_COLORS_DARK if (
        dark if dark is not None else is_dark_theme()) else POSITION_COLORS

    positions = list(costs["position"])
    order = list(costs.sort_values("cost", ascending=False)["position"])

    return (
        alt.Chart(costs)
        .mark_bar(
            size=BAR_THICKNESS,
            cornerRadiusEnd=BAR_CORNER_RADIUS,   # data end only; baseline square
        )
        .encode(
            x=alt.X("cost:Q",
                    title="Expected points lost",
                    scale=alt.Scale(zero=True),   # never truncate a length encoding
                    axis=alt.Axis(grid=True, tickCount=4)),
            y=alt.Y("position:N",
                    title=None,
                    sort=order,                   # most urgent at the top
                    axis=alt.Axis(labelFontSize=13)),
            color=alt.Color(
                "position:N",
                scale=alt.Scale(domain=positions,
                                range=[palette.get(p, "#898781") for p in positions]),
                legend=None,                      # the y axis already names each bar
            ),
            tooltip=[
                alt.Tooltip("position:N", title="Position"),
                alt.Tooltip("cost:Q", title="Cost of waiting", format=".0f"),
                alt.Tooltip("best_available_vorp:Q", title="Best available VORP",
                            format=".0f"),
            ],
        )
        .properties(height=max(28 * len(costs), 90))
    )
