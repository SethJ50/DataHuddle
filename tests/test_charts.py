"""Tests for the cost-of-waiting chart.

A chart cannot be checked by assertion the way a number can, but its SPEC can.
These pin the things that would quietly mislead: a truncated axis that makes a
small cost look large, a colour that disagrees with the draft board, or a sort
order that puts the least urgent position at the top.
"""

import json

import pandas as pd
import pytest

from presentation.charts import cost_of_waiting_chart
from presentation.colors import POSITION_COLORS, POSITION_COLORS_DARK


@pytest.fixture
def costs():
    """A cost frame as positional_costs_for_team returns it, urgent first."""
    return pd.DataFrame({
        "position": ["RB", "WR", "TE", "QB"],
        "best_available_vorp": [48.4, 50.6, 43.9, 79.8],
        "cost": [25.1, 23.3, 20.3, 18.6],
    })


def spec_of(costs, **kwargs):
    return json.loads(cost_of_waiting_chart(costs, **kwargs).to_json())


def test_the_axis_starts_at_zero():
    # Length IS the encoding here. A truncated axis would make a 2-point cost
    # look like a 20-point one, which is worse than showing no chart at all.
    frame = pd.DataFrame({"position": ["RB", "WR"], "cost": [30.0, 28.0],
                          "best_available_vorp": [10.0, 9.0]})
    assert spec_of(frame)["encoding"]["x"]["scale"]["zero"] is True


def test_the_most_urgent_position_is_at_the_top(costs):
    order = spec_of(costs)["encoding"]["y"]["sort"]
    assert order[0] == "RB"                       # highest cost in the fixture
    assert order == ["RB", "WR", "TE", "QB"]


def test_an_unsorted_frame_is_still_drawn_most_urgent_first():
    # The chart sorts for itself rather than trusting the caller to have done it.
    frame = pd.DataFrame({"position": ["QB", "RB"], "cost": [5.0, 40.0],
                          "best_available_vorp": [1.0, 2.0]})
    assert spec_of(frame)["encoding"]["y"]["sort"][0] == "RB"


def test_colours_match_the_draft_board_exactly(costs):
    # The whole reason for a shared palette: two views must not teach two
    # different colour languages for the same thing.
    scale = spec_of(costs)["encoding"]["color"]["scale"]
    for position, colour in zip(scale["domain"], scale["range"]):
        assert colour == POSITION_COLORS[position]


def test_the_dark_theme_uses_the_validated_dark_steps(costs):
    # NOT lightened copies -- the dark steps were selected and checked against
    # the dark surface, so using the light ones there is untested territory.
    scale = spec_of(costs, dark=True)["encoding"]["color"]["scale"]
    for position, colour in zip(scale["domain"], scale["range"]):
        assert colour == POSITION_COLORS_DARK[position]


def test_there_is_no_legend(costs):
    # Every bar is labelled with its position on the y axis, so identity never
    # rests on colour alone. A legend would restate the axis.
    assert spec_of(costs)["encoding"]["color"].get("legend") is None


def test_the_number_is_available_on_hover(costs):
    titles = [t["title"] for t in spec_of(costs)["encoding"]["tooltip"]]
    assert "Cost of waiting" in titles
    assert "Position" in titles


def test_the_bars_follow_the_mark_specs(costs):
    mark = spec_of(costs)["mark"]
    assert mark["size"] <= 24                     # cap it; leave the band air
    assert mark["cornerRadiusEnd"] == 4           # data end only, baseline square


def test_a_folded_position_still_gets_its_neutral():
    # K and DST share the neutral. They are normally excluded from this chart
    # (no VORP), but the palette must not fall over if one appears.
    frame = pd.DataFrame({"position": ["K"], "cost": [3.0],
                          "best_available_vorp": [1.0]})
    scale = spec_of(frame)["encoding"]["color"]["scale"]
    assert scale["range"] == [POSITION_COLORS["K"]]


def test_the_chart_grows_with_the_number_of_positions(costs):
    tall = spec_of(costs)["height"]
    short = spec_of(costs.head(2))["height"]
    assert tall > short
