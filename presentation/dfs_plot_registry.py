"""The list of plots the Basic Plots page offers, and what each one needs.

The page itself draws whatever it finds here. Adding a plot means adding an entry
to `PLOTS` below -- naming which filters it wants, which function builds its data
and which draws it -- and the page picks it up with no changes at all.

Written this way from the start because the plan calls for more plots over time,
and the alternative grows into a chain of `if plot == "..."` branches where the
filters, the data and the chart for one plot end up in three different places.
"""

from typing import Callable, NamedTuple

from presentation.dfs_charts import (
    actual_vs_expected_chart, defensive_allowance_chart, team_tendency_chart,
)
from services.dfs_opportunity_service import SPLITS, actual_vs_expected
from services.dfs_team_service import (
    defensive_allowances, neutral_script_description, offensive_tendencies,
)

# The filters a plot can ask for. The page knows how to draw each of these, and
# an entry below lists only the ones it actually uses -- so a plot about team
# tendencies never shows a position filter nobody can act on.
SEASON = "season"
WEEKS = "weeks"
POSITION_FILTER = "positions"
SPLIT = "split"
MINIMUM_GAMES = "minimum_games"
MEASURE = "measure"
LABEL_COUNT = "label_count"

# How a plot wants to be drawn. Matplotlib gives a static picture with full
# control over every mark; Altair gives an interactive one. Both are legitimate,
# so a plot says which it is rather than the page assuming.
MATPLOTLIB = "matplotlib"
ALTAIR = "altair"


class PlotSpec(NamedTuple):
    """Everything the page needs to know about one plot.

    A record rather than a loose tuple because the page reads these by name, and
    `spec.filters` says what it is where a fourth tuple slot would not.

    Attributes:
        key: A short stable name, used for widget keys so switching plots does
            not carry one plot's filter settings onto another.
        label: What the dropdown shows.
        question: One line saying what the plot answers, shown under the
            dropdown. Not a description of the chart -- a description of the
            decision it helps with.
        filters: Which filter controls to show, from the names above, in the
            order they should appear.
        build: Called with the repository and the chosen filter values, returning
            the table to draw.
        chart: Called with that table to produce the drawing.
        renderer: `MATPLOTLIB` or `ALTAIR`, saying how the page should show what
            `chart` returns.
        uses_scoring: Whether this plot's numbers depend on the scoring system.
            False for plots about play-calling, which have no fantasy points in
            them at all -- the page then leaves the scoring choice out rather
            than passing an argument the builder would reject.
        chart_filters: Filters that belong to the CHART ALONE. These are passed
            to `chart` and withheld from `build`, for choices that change the
            drawing without changing the data.
        shared_filters: Filters that BOTH need. `split` is the example: the
            builder uses it to pick which columns to read, and the chart uses it
            to title the axes.
        reading: One line on how to read the result, shown under the chart. The
            place to put the caveat that stops somebody misreading it.
        default_positions: Which positions to preselect, when the plot has a
            position filter.
    """

    key: str
    label: str
    question: str
    filters: tuple
    build: Callable
    chart: Callable
    renderer: str = MATPLOTLIB
    uses_scoring: bool = True
    chart_filters: tuple = ()
    shared_filters: tuple = ()
    reading: str = ""
    default_positions: tuple = ()



def _rush_defence(repo, **kwargs):
    """Build the rushing half of the defensive table. See `defensive_allowances`."""
    return defensive_allowances(repo, play_kind="rush", **kwargs)


def _pass_defence(repo, **kwargs):
    """Build the passing half of the defensive table. See `defensive_allowances`."""
    return defensive_allowances(repo, play_kind="pass", **kwargs)


def _pass_defence_chart(frame, **kwargs):
    """Draw the passing defence chart. See `defensive_allowance_chart`."""
    return defensive_allowance_chart(frame, play_kind="pass", **kwargs)


PLOTS = (
    PlotSpec(
        key="actual_vs_expected",
        label="Actual points vs expected (xFP)",
        question="Who is scoring more than their opportunities are worth, and "
                 "who is being carried by their role?",
        filters=(SEASON, WEEKS, POSITION_FILTER, SPLIT, MINIMUM_GAMES),
        build=actual_vs_expected,
        chart=actual_vs_expected_chart,
        renderer=ALTAIR,
        shared_filters=(SPLIT,),
        reading="The red dashed line is break-even. Above it a player beat what his "
                "chances were worth; below it he did not. Distance from the line "
                "is how much. Neither side is automatically good news -- a player "
                "far above the line has been finishing well, which may be skill "
                "or may be touchdown luck that is about to stop, and one far "
                "below has the opportunities without the results yet. Hover any "
                "point for the player and his numbers.",
        default_positions=("RB", "WR", "TE"),
    ),
    PlotSpec(
        key="team_tendencies",
        label="Neutral-script pass rate by team",
        question="Which offences throw when the score is not forcing them to — "
                 "and how fast do they play?",
        filters=(SEASON, WEEKS, MEASURE),
        build=offensive_tendencies,
        chart=team_tendency_chart,
        renderer=ALTAIR,
        uses_scoring=False,
        chart_filters=(MEASURE,),
        reading="Bars run both ways from zero, so direction is the answer: right "
                "throws more than a typical team would in the same spots, left "
                "runs more. Measured over " + neutral_script_description()
                + ". There is no agreed definition of neutral script, so a site "
                  "quoting different numbers has probably drawn the line "
                  "somewhere else rather than got it wrong.",
    ),
    PlotSpec(
        key="rush_defence",
        label="Rushing: fantasy points vs EPA allowed by defence",
        question="Which defences can be run on — and which are worth attacking "
                 "even though they play well?",
        filters=(SEASON, WEEKS, POSITION_FILTER),
        build=_rush_defence,
        chart=defensive_allowance_chart,
        renderer=ALTAIR,
        chart_filters=(),
        reading="Right means the defence gives up ground; up means it gives up "
                "fantasy points. The dashed crosshair is the league average. "
                "Top-right defences are bad and generous, and are the easy "
                "targets. TOP-LEFT IS THE INTERESTING CORNER: sound defences "
                "that still pay out, usually because they are on the field a "
                "lot or concede the short stuff that scores.",
        default_positions=("RB",),
    ),
    PlotSpec(
        key="pass_defence",
        label="Passing: fantasy points vs EPA allowed by defence",
        question="Which defences let pass-catchers score, and which shut them "
                 "down?",
        filters=(SEASON, WEEKS, POSITION_FILTER),
        build=_pass_defence,
        chart=_pass_defence_chart,
        renderer=ALTAIR,
        chart_filters=(),
        reading="Right means the defence gives up yardage through the air; up "
                "means it gives up fantasy points to the players you are "
                "choosing between. The dashed crosshair is the league average, "
                "and the top-left corner holds the defences worth attacking "
                "despite playing well.",
        default_positions=("WR",),
    ),
)
"""Every plot the page offers, in the order the dropdown lists them."""


PLOTS_BY_LABEL = {plot.label: plot for plot in PLOTS}
"""The same plots keyed by what the dropdown shows, so the page can look up
whatever was picked."""


MEASURE_OPTIONS = {
    "Pass rate over expected": "proe",
    "Neutral pass rate": "pass_rate",
    "Pace (seconds per play)": "seconds_per_play",
    "Plays per game": "plays_per_game",
}
"""What the team-tendency bars can be ranked on, as shown to the reader.

Pass rate over expected leads because it is the one that separates a team that
LIKES throwing from one that is simply always behind.
"""


SPLIT_OPTIONS = tuple(SPLITS)
"""The choices the split filter offers, taken from the service so the two cannot
disagree about what a split is called."""
