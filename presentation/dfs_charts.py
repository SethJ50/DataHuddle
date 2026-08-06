"""Charts the Daily Fantasy pages draw, built with Altair.

Separate from presentation/charts.py, which serves the draft pages, because the
two sets share nothing but their palette. They do share that deliberately: a
running back is the same colour everywhere in this app, and a chart that picked
its own hues would teach a second, contradictory colour language.

ONE DATA SOURCE PER CHART, ALWAYS. See `EVERY_LAYER_SHARES_THE_DATA` below --
this is the rule that makes reference lines work inside Streamlit, and breaking
it fails silently.
"""

import altair as alt

from presentation.charts import is_dark_theme
from presentation.colors import POSITION_COLORS, POSITION_COLORS_DARK

EVERY_LAYER_SHARES_THE_DATA = """
Streamlit sends exactly ONE dataset to the browser per chart, whatever the chart
thinks it has.

A layered Altair chart can normally give each layer its own data, and the obvious
way to draw a reference line is a two-point table holding its ends. Altair builds
that correctly and it renders correctly on its own. Streamlit then throws it
away: its conversion keeps only the primary dataframe, and the line's layer is
left pointing at a dataset name that no longer exists anywhere in the spec.

Nothing errors. The layer simply draws nothing, and no amount of restyling brings
it back -- which is a long way to go to find out.

So every layer here reads the SAME dataframe. A y = x line needs no data of its
own anyway: encode the y channel against the x FIELD and the line traces the
diagonal exactly.
"""

POINT_SIZE = 90
"""Area of a scatter point. Large enough to read as a mark rather than a speck."""

BREAK_EVEN_COLOR = "#d62728"
BREAK_EVEN_COLOR_DARK = "#ff5f56"
"""Red, for the break-even line, in the light and dark themes.

A strong colour is safe here BECAUSE THE LINE IS NOT A SERIES. Position identity
is carried by the points, and no position is red -- they are blue, yellow,
magenta and green -- so the line cannot be mistaken for one. The dashes separate
it a second time for anyone who cannot rely on hue.

The dark step is lighter and less saturated, since the light-theme red goes muddy
against a dark surface.
"""

BREAK_EVEN_WIDTH = 3
LABEL_COLOR = "#898781"
"""Ink for the corner notes. A text colour rather than a series colour, so a
label can never be mistaken for a position's marks."""


def _palette(dark=None):
    """Pick the colour set matching the theme in use.

    Steps:
        1. Use the theme that was asked for, or detect the live one with
           `is_dark_theme` from presentation/charts.py.
        2. Return the matching position colours.

    Args:
        dark: Force a palette instead of detecting one. Mainly for tests.

    Returns:
        dict: Position name to colour.

    Note:
        The dark colours are not lightened copies of the light ones -- they were
        chosen and checked against a dark background separately. Using the wrong
        set puts light-surface colours on a dark chart, the one case they were
        never validated for.
    """
    use_dark = dark if dark is not None else is_dark_theme()
    return POSITION_COLORS_DARK if use_dark else POSITION_COLORS


def actual_vs_expected_chart(frame, split="Total", dark=None):
    """Plot what players scored against what their opportunities were worth.

    A scatter with a red dashed diagonal through it. The diagonal is break-even:
    a player sitting on it scored exactly what his chances were worth, above it
    he beat them, below it he did not, and the distance from it is how much.

    Steps:
        1. Choose the palette and the red for the current theme.
        2. Work out one range covering both axes, so the diagonal really is at 45
           degrees -- see the note.
        3. Draw one point per player, coloured by position, with a tooltip
           carrying every number.
        4. Draw the y = x line over the top by encoding the y channel against the
           EXPECTED field, so the line needs no data of its own.
        5. Note which half of the chart means what, in the two corners.

    Args:
        frame: The table from `actual_vs_expected`, with `name`, `position`,
            `team`, `games`, `actual_per_game`, `expected_per_game` and
            `gap_per_game`. Must not be empty -- callers check first.
        split: Which part of the game is being measured, used in the axis titles
            so a Receiving chart cannot be mistaken for a Total one.
        dark: Force a palette instead of detecting one. Mainly for tests.

    Returns:
        alt.LayerChart: Ready for `st.altair_chart(..., width="stretch",
            theme=None)`. Pass `theme=None` or Streamlit restyles it.

    Note:
        BOTH AXES SHARE ONE RANGE. The whole chart is read against a 45-degree
        line, and independently scaled axes would put that line somewhere other
        than break-even, making every judgement drawn from the picture wrong.

        The axes do NOT start at zero. Position is the encoding here, not length,
        so the origin carries no meaning; including it would push every point into
        one corner and hide the differences the chart exists to show.

        THE LINE CARRIES NO DATA OF ITS OWN, and must not. See
        `EVERY_LAYER_SHARES_THE_DATA` at the top of this module.
    """
    palette = _palette(dark)
    use_dark = dark if dark is not None else is_dark_theme()
    break_even_color = BREAK_EVEN_COLOR_DARK if use_dark else BREAK_EVEN_COLOR

    positions = sorted(frame["position"].dropna().unique())

    lowest = float(min(frame["expected_per_game"].min(),
                       frame["actual_per_game"].min()))
    highest = float(max(frame["expected_per_game"].max(),
                        frame["actual_per_game"].max()))
    padding = max((highest - lowest) * 0.05, 0.5)
    floor, ceiling = lowest - padding, highest + padding
    limits = alt.Scale(domain=[floor, ceiling], zero=False)

    base = alt.Chart(frame)

    points = base.mark_circle(size=POINT_SIZE, opacity=0.8).encode(
        x=alt.X("expected_per_game:Q",
                title=f"Expected {split.lower()} points per game", scale=limits),
        y=alt.Y("actual_per_game:Q",
                title=f"Actual {split.lower()} points per game", scale=limits),
        color=alt.Color(
            "position:N", title="Position",
            scale=alt.Scale(domain=positions,
                            range=[palette.get(p, LABEL_COLOR) for p in positions]),
            legend=alt.Legend(orient="top", direction="horizontal"),
        ),
        tooltip=[
            alt.Tooltip("name:N", title="Player"),
            alt.Tooltip("position:N", title="Position"),
            alt.Tooltip("team:N", title="Team"),
            alt.Tooltip("games:Q", title="Games"),
            alt.Tooltip("actual_per_game:Q", title="Actual / game", format=".1f"),
            alt.Tooltip("expected_per_game:Q", title="Expected / game", format=".1f"),
            alt.Tooltip("gap_per_game:Q", title="Gap / game", format="+.1f"),
            alt.Tooltip("actual:Q", title="Actual total", format=".0f"),
        ],
    )

    # y = x, drawn from the same rows as the points. Encoding the y channel
    # against the EXPECTED field is what makes it the diagonal: every player
    # contributes the point (expected, expected), which all lie on the line.
    break_even = base.mark_line(
        color=break_even_color, strokeDash=[8, 5], strokeWidth=BREAK_EVEN_WIDTH,
        opacity=1.0,
    ).encode(
        x=alt.X("expected_per_game:Q", scale=limits),
        y=alt.Y("expected_per_game:Q", scale=limits),
    )

    return (points + break_even + _corner(base, limits, floor, ceiling)) \
        .properties(height=470)


def _corner(base, limits, floor, ceiling):
    """Write which half of the chart is the good news, in its two corners.

    The line tells you there is a division; it does not tell you which side to
    want to be on, and that is the whole question the plot is asked.

    Steps:
        1. Squash the data down to a single row, so one note is drawn rather than
           one per player. The count is thrown away -- it is only there to give
           the aggregation something to do.
        2. Place each note at a fixed spot with `alt.datum`, which supplies a
           constant instead of reading a column.

    Args:
        base: The chart carrying the shared data. See
            `EVERY_LAYER_SHARES_THE_DATA` for why these notes cannot bring their
            own.
        limits: The shared axis range.
        floor: The low end of both axes.
        ceiling: The high end of both axes.

    Returns:
        alt.LayerChart: The two notes, ready to layer under the caller's chart.
    """
    span = ceiling - floor

    def note(x, y, text, align):
        """One italic note, drawn once however many rows the data has."""
        return (
            base.transform_aggregate(rows="count()")     # collapse to one row
            .mark_text(fontSize=11, fontStyle="italic", color=LABEL_COLOR,
                       align=align, baseline="middle")
            .encode(x=alt.datum(x), y=alt.datum(y), text=alt.datum(text))
        )

    return (
        note(floor + span * 0.02, ceiling - span * 0.03,
             "above the line — beating expectation", "left")
        + note(ceiling - span * 0.02, floor + span * 0.03,
               "below — not yet cashing in", "right")
    )


BAR_COLOR = "#5a6b7a"
BAR_COLOR_DARK = "#8fa3b5"
"""The one hue the team bars use, per theme.

A single series needs a single colour, and this low-chroma slate is deliberately
not one of the position hues -- a chart about teams should not borrow the colour
language that means "running back" everywhere else.
"""


def team_tendency_chart(frame, measure="proe", dark=None):
    """Rank every team's offence on one measure, as horizontal bars.

    Thirty-two numbers in a column are a lookup table; the same thirty-two as
    sorted bars are a ranking you can read in a glance, which is the only reason
    to draw them.

    Steps:
        1. Pick the colour for the theme in use.
        2. Sort the teams so the largest bar is at the top.
        3. Draw one bar per team, anchored at zero.
        4. Attach a tooltip with the rest of that team's numbers, so the chart
           can stay a ranking without becoming a table.

    Args:
        frame: The table from `offensive_tendencies`, with `team` and the chosen
            measure. Must not be empty -- callers check first.
        measure: Which column to rank on. `"proe"` for pass rate over expected,
            `"pass_rate"`, `"seconds_per_play"` or `"plays_per_game"`.
        dark: Force a palette instead of detecting one. Mainly for tests.

    Returns:
        alt.Chart: Ready for `st.altair_chart(..., width="stretch", theme=None)`.

    Note:
        PASS RATE OVER EXPECTED IS ALREADY SIGNED, so the bars run both ways from
        zero and DIRECTION carries the meaning -- right is pass-happy, left is
        run-heavy. That leaves nothing for colour to say, which is why every bar
        is the same one. A two-colour version would encode the sign twice and
        make the chart look like it has two series when it has one.

        The axis is never truncated. A bar's LENGTH is the encoding, so a clipped
        axis would make a small difference look like a large one.
    """
    use_dark = dark if dark is not None else is_dark_theme()
    color = BAR_COLOR_DARK if use_dark else BAR_COLOR

    titles = {
        "proe": "Pass rate over expected (percentage points)",
        "pass_rate": "Pass rate in neutral situations",
        "seconds_per_play": "Seconds per play (lower is faster)",
        "plays_per_game": "Offensive plays per game",
    }
    order = list(frame.sort_values(measure, ascending=False, na_position="last")["team"])

    return (
        alt.Chart(frame)
        .mark_bar(size=13, cornerRadiusEnd=3, color=color)
        .encode(
            x=alt.X(f"{measure}:Q", title=titles.get(measure, measure),
                    scale=alt.Scale(zero=True)),
            y=alt.Y("team:N", title=None, sort=order,
                    axis=alt.Axis(labelFontSize=10)),
            tooltip=[
                alt.Tooltip("team:N", title="Team"),
                alt.Tooltip("pass_rate:Q", title="Neutral pass rate", format=".1%"),
                alt.Tooltip("proe:Q", title="Pass rate over expected", format="+.1f"),
                alt.Tooltip("seconds_per_play:Q", title="Seconds per play", format=".1f"),
                alt.Tooltip("plays_per_game:Q", title="Plays per game", format=".1f"),
                alt.Tooltip("red_zone_trips_per_game:Q", title="Red-zone trips per game",
                            format=".2f"),
                alt.Tooltip("neutral_plays:Q", title="Neutral plays measured"),
            ],
        )
        .properties(height=max(18 * len(frame), 200))
    )


def defensive_allowance_chart(frame, play_kind="rush", dark=None):
    """Plot how well each defence plays against how much it pays out.

    Two different questions on two axes. Expected points added says whether a
    defence is any good; fantasy points allowed says whether it is worth
    attacking. They agree less than you would think, and the teams where they
    disagree are the ones worth finding -- a defence that plays well but gives up
    the short catches that score is still a defence to target.

    Steps:
        1. Pick the colours for the theme in use.
        2. Draw each team as its abbreviation rather than a dot, so the chart
           needs no legend and no hovering to be read.
        3. Draw a crosshair at the league average on both measures, splitting the
           chart into four quadrants.
        4. Attach a tooltip with the underlying counts.

    Args:
        frame: The table from `defensive_allowances`, with `team`,
            `epa_per_play`, `points_per_play` and `plays_faced`. Must not be
            empty -- callers check first.
        play_kind: `"rush"` or `"pass"`, used in the axis titles.
        dark: Force a palette instead of detecting one. Mainly for tests.

    Returns:
        alt.LayerChart: Ready for `st.altair_chart(..., width="stretch",
            theme=None)`.

    Note:
        TEAMS ARE DRAWN AS TEXT, NOT AS POINTS. With thirty-two of them a scatter
        of identical dots says nothing without hovering every one, and the
        abbreviations are short enough to be the marks themselves.

        THE CROSSHAIR IS AT THE AVERAGE, NOT AT ZERO. Zero expected points added
        is roughly league-average by construction, but zero fantasy points
        allowed is not a thing that happens -- so a zero crosshair would put
        every team in one quadrant and say nothing.

        Both reference lines are built from the SAME data as the points. See
        `EVERY_LAYER_SHARES_THE_DATA` at the top of this module.
    """
    use_dark = dark if dark is not None else is_dark_theme()
    ink = BAR_COLOR_DARK if use_dark else BAR_COLOR

    noun = "rush" if play_kind == "rush" else "pass attempt"
    base = alt.Chart(frame)

    labels = base.mark_text(fontSize=11, fontWeight="bold", color=ink).encode(
        x=alt.X("epa_per_play:Q",
                title=f"Expected points added allowed per {noun} "
                      f"(right is worse defence)",
                scale=alt.Scale(zero=False, nice=True)),
        y=alt.Y("points_per_play:Q",
                title=f"Fantasy points allowed per {noun}",
                scale=alt.Scale(zero=False, nice=True)),
        text=alt.Text("team:N"),
        tooltip=[
            alt.Tooltip("team:N", title="Defence"),
            alt.Tooltip("epa_per_play:Q", title=f"EPA allowed per {noun}",
                        format="+.3f"),
            alt.Tooltip("points_per_play:Q", title=f"Fantasy points per {noun}",
                        format=".3f"),
            alt.Tooltip("points_allowed:Q", title="Fantasy points allowed",
                        format=".0f"),
            alt.Tooltip("plays_faced:Q", title=f"{noun.capitalize()}s faced"),
            alt.Tooltip("games:Q", title="Games"),
        ],
    )

    # `mean()` over the shared data rather than a number worked out in Python,
    # so these lines cannot drift from the points they are drawn against.
    vertical = base.mark_rule(color=LABEL_COLOR, strokeDash=[4, 4],
                              opacity=0.7).encode(x="mean(epa_per_play):Q")
    horizontal = base.mark_rule(color=LABEL_COLOR, strokeDash=[4, 4],
                                opacity=0.7).encode(y="mean(points_per_play):Q")

    return (vertical + horizontal + labels).properties(height=470)
