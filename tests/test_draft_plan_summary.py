"""Tests for the Draft Plan summary board.

The property most worth locking in is that each player's availability is
measured against HIS OWN ROW'S pick. Reading a round-3 probability off the
round-1 pick would still render a perfectly plausible board -- every number
present, every colour sensible -- and would quietly tell you to wait on players
who will be long gone.
"""

import pandas as pd
import pytest

from presentation.draft_plan_summary import (
    GONE_BELOW, SAFE_ABOVE, build_summary, probability_html, summary_html,
)


PICKS = [
    {"round": 1, "label": "1.04", "overall_pick": 4},
    {"round": 2, "label": "2.09", "overall_pick": 21},
]


# --------------------------------------------------------------------------
# build_summary
# --------------------------------------------------------------------------

def test_each_player_is_measured_against_his_own_rows_pick():
    # The same player planned in two rounds must carry a DIFFERENT number in
    # each -- his chance of lasting to pick 4 is not his chance of lasting to 21.
    plan = {("1.04", "RB"): ["Gibbs"], ("2.09", "RB"): ["Gibbs"]}
    availability = {4: {"Gibbs": 0.10}, 21: {"Gibbs": 0.90}}

    frame = build_summary(plan, PICKS, availability=availability)
    assert frame.loc[0, "RB"] == [("Gibbs", 0.10)]
    assert frame.loc[1, "RB"] == [("Gibbs", 0.90)]


def test_priority_order_is_preserved():
    plan = {("1.04", "WR"): ["First", "Second", "Third"]}
    availability = {4: {"First": 0.5, "Second": 0.6, "Third": 0.7}}
    names = [name for name, _ in build_summary(plan, PICKS, availability=availability).loc[0, "WR"]]
    assert names == ["First", "Second", "Third"]


def test_a_player_the_simulation_never_saw_gets_no_probability():
    # None, not 0.0. "Unknown" and "certain to be gone" are very different
    # claims, and the second one would be a lie here.
    plan = {("1.04", "TE"): ["Nobody"]}
    frame = build_summary(plan, PICKS, availability={4: {}})
    assert frame.loc[0, "TE"] == [("Nobody", None)]


def test_no_simulation_still_builds_the_whole_board():
    # The page shows this summary before it knows whether a simulation exists.
    plan = {("1.04", "RB"): ["Gibbs"]}
    frame = build_summary(plan, PICKS)
    assert frame.loc[0, "RB"] == [("Gibbs", None)]
    assert list(frame["Pick"]) == ["1.04", "2.09"]


def test_unplanned_picks_still_get_a_row():
    frame = build_summary({}, PICKS)
    assert len(frame) == 2
    assert all(frame.loc[0, position] == [] for position in ("QB", "RB", "WR", "TE"))


# --------------------------------------------------------------------------
# probability_html
# --------------------------------------------------------------------------

def test_probability_colours_split_at_the_documented_thresholds():
    assert "dh-prob-safe" in probability_html(SAFE_ABOVE)
    assert "dh-prob-toss" in probability_html(SAFE_ABOVE - 0.01)
    assert "dh-prob-toss" in probability_html(GONE_BELOW)
    assert "dh-prob-gone" in probability_html(GONE_BELOW - 0.01)


def test_probability_renders_as_a_whole_percentage():
    assert ">91%<" in probability_html(0.9149)
    assert ">100%<" in probability_html(1.0)
    assert ">0%<" in probability_html(0.0)


def test_missing_probability_renders_nothing_at_all():
    assert probability_html(None) == ""
    assert probability_html(float("nan")) == ""
    assert probability_html("not a number") == ""


# --------------------------------------------------------------------------
# summary_html
# --------------------------------------------------------------------------

def test_html_puts_the_percentage_after_the_name():
    plan = {("1.04", "RB"): ["Gibbs"]}
    html = summary_html(build_summary(plan, PICKS, availability={4: {"Gibbs": 0.91}}))
    assert "Gibbs<span" in html
    assert ">91%<" in html


def test_html_escapes_player_names():
    # Names come from a data source, and an unescaped "&" would break the table.
    plan = {("1.04", "RB"): ["A & B <script>"]}
    html = summary_html(build_summary(plan, PICKS))
    assert "<script>" not in html
    assert "&amp;" in html


def test_html_accepts_bare_names_as_well_as_pairs():
    # Lets a caller with no availability pass a plain plan straight through.
    frame = pd.DataFrame([{"Pick": "1.04", "QB": [], "RB": ["Gibbs"],
                           "WR": [], "TE": []}])
    html = summary_html(frame)
    assert "Gibbs" in html
    assert "dh-prob " not in html.split("</style>")[1]   # no span in the body
