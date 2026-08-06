"""Tests for the positional cliff finder.

It answers "how many players are left at this position before value falls away".
Two things make or break it: the search WINDOW, without which the biggest drop in
a position is usually between two players nobody will draft, and the STEEPNESS
measure, without which every position looks like it has a cliff.

It reads projections rather than the simulation on purpose. Every
simulation-based number in this app follows ADP, so a real drop in value the
market has not priced is invisible to all of them -- this is the one thing that
sees it.
"""

import numpy as np
import pytest

from draft_model.queries import positional_cliffs


def test_it_finds_the_drop_and_counts_what_is_left():
    # Three good backs, then a shelf.
    positions = ["RB"] * 6
    projections = [300.0, 295.0, 290.0, 240.0, 236.0, 233.0]

    cliff = positional_cliffs(positions, projections)[0]

    assert cliff["position"] == "RB"
    assert cliff["players_before"] == 3        # take one of these, or drop 50
    assert cliff["drop"] == pytest.approx(50.0)
    assert cliff["before"] == pytest.approx(290.0)
    assert cliff["after"] == pytest.approx(240.0)


def test_a_flat_position_reports_no_real_cliff():
    # Evenly spaced players have a "biggest drop", but it is just the normal
    # step. Steepness near 1 is what tells the reader to ignore it.
    cliff = positional_cliffs(["WR"] * 8, list(np.linspace(300, 250, 8)))[0]
    assert cliff["steepness"] == pytest.approx(1.0, abs=0.01)


def test_a_real_shelf_is_clearly_steeper_than_the_normal_step():
    cliff = positional_cliffs(["TE"] * 6, [260, 258, 256, 200, 198, 196])[0]
    assert cliff["drop"] == pytest.approx(56.0)
    assert cliff["steepness"] > 3


def test_the_window_ignores_drops_nobody_will_draft_through():
    # A 230-point cliff at player 21. Real, and completely irrelevant -- no one
    # is deciding between the 20th and 21st running back.
    projections = list(np.linspace(300, 280, 20)) + [50.0] * 10

    near = positional_cliffs(["RB"] * 30, projections, window=8)[0]
    far = positional_cliffs(["RB"] * 30, projections, window=25)[0]

    assert near["players_before"] < 8
    assert near["steepness"] == pytest.approx(1.0, abs=0.01)   # nothing to see
    assert far["players_before"] == 20                          # found once in range
    assert far["drop"] == pytest.approx(230.0)


def test_positions_are_ordered_most_urgent_first():
    # Fewest players left comes first; a bigger drop breaks a tie.
    positions = ["QB"] * 4 + ["RB"] * 4 + ["WR"] * 4
    projections = ([400, 340, 338, 336]          # QB: 1 left, 60-pt drop
                   + [300, 298, 296, 240]        # RB: 3 left, 56-pt drop
                   + [280, 220, 218, 216])       # WR: 1 left, 60-pt drop
    order = [c["position"] for c in positional_cliffs(positions, projections)]
    assert order[0] in ("QB", "WR")              # both have 1 left
    assert order[-1] == "RB"                     # 3 left, least urgent


def test_players_without_a_projection_are_skipped():
    # This is what keeps K and DST out without naming them: they have no
    # projection in this app at all.
    positions = ["K", "K", "K", "RB", "RB", "RB"]
    projections = [np.nan, np.nan, np.nan, 300.0, 295.0, 200.0]

    found = positional_cliffs(positions, projections)
    assert [c["position"] for c in found] == ["RB"]


def test_a_position_with_one_player_left_is_skipped():
    # One player has no gap to measure. Reporting a drop of zero would read as
    # "no cliff" when the truth is "no data".
    found = positional_cliffs(["TE", "RB", "RB"], [200.0, 300.0, 250.0])
    assert [c["position"] for c in found] == ["RB"]


def test_an_empty_pool_returns_nothing_rather_than_raising():
    assert positional_cliffs([], []) == []
    assert positional_cliffs(["RB"], [np.nan]) == []


def test_identical_projections_do_not_divide_by_zero():
    # Every gap is zero, so the typical gap is zero too. Steepness must fall back
    # to 1 rather than producing infinity or NaN.
    cliff = positional_cliffs(["RB"] * 5, [200.0] * 5)[0]
    assert cliff["drop"] == 0.0
    assert np.isfinite(cliff["steepness"])


def test_it_returns_plain_python_not_a_dataframe():
    # draft_model stays free of pandas; the caller builds a frame if it wants one.
    found = positional_cliffs(["RB", "RB"], [300.0, 250.0])
    assert isinstance(found, list)
    assert isinstance(found[0], dict)


def test_drafting_the_top_players_moves_the_cliff():
    # The whole point of recomputing it every pick.
    positions = ["TE"] * 6
    full = [262.0, 259.0, 221.0, 212.0, 194.0, 186.0]
    before = positional_cliffs(positions, full)[0]

    after = positional_cliffs(positions[2:], full[2:])[0]

    assert before["players_before"] == 2
    assert before["drop"] == pytest.approx(38.0)
    assert after["drop"] < before["drop"]        # the shelf has been taken
