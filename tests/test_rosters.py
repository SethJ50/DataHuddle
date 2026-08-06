"""Tests for reading a team's roster and lineup out of a picks matrix.

Pure array logic with no interface, and the shared foundation of the team
comparison and positional suggestion features. Three things it has to get right:

1. A roster is not a lineup. Six receivers only start twice.
2. FLEX is filled from the LEFTOVERS, after the dedicated slots.
3. A live matrix only covers the FUTURE, so a mid-draft roster is what a team
   holds ORed with what they are simulated to get.
"""

import numpy as np

from draft_model.config import UNDRAFTED
from draft_model.queries import (
    lineup_points, roster_from_picks, starting_lineup_mask,
)

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}


# ---------------------------------------------------------------------------
# roster_from_picks -- who did this team get?
# ---------------------------------------------------------------------------


def test_it_selects_the_players_who_went_at_this_team_s_picks():
    #                  p0  p1  p2  p3
    picks = np.array([[  1,  2,  3,  4],
                      [  4,  3,  2,  1]])
    got = roster_from_picks(picks, (1, 3))
    assert got.tolist() == [[True, False, True, False],
                            [False, True, False, True]]


def test_undrafted_players_are_never_on_a_roster():
    # UNDRAFTED (999) sits above any realistic draft, so it matches none of a
    # team's real pick numbers and the player lands on nobody's roster.
    picks = np.array([[1, UNDRAFTED, UNDRAFTED]])
    assert roster_from_picks(picks, (1, 5)).tolist() == [[True, False, False]]


def test_a_team_with_no_picks_left_gets_an_empty_roster():
    # Happens at the end of a draft. Should not raise.
    got = roster_from_picks(np.array([[1, 2, 3]]), ())
    assert got.shape == (1, 3)
    assert not got.any()


# ---------------------------------------------------------------------------
# starting_lineup_mask -- who actually starts?
# ---------------------------------------------------------------------------


def test_only_the_best_two_of_three_backs_start():
    # The heart of it: hoarding a position does not make a team better.
    positions = np.array(["RB", "RB", "RB", "QB"])
    projections = np.array([300.0, 250.0, 200.0, 400.0])
    roster = np.array([[True, True, True, True]])

    starters = starting_lineup_mask(roster, projections, positions,
                                    {"QB": 1, "RB": 2})

    assert starters.tolist() == [[True, True, False, True]]   # RB3 benched


def test_flex_takes_the_best_player_left_over():
    # RB1/RB2 and WR1/WR2 are spoken for, so FLEX is a choice between RB3 (260)
    # and WR3 (280). It must take the receiver.
    positions = np.array(["RB", "RB", "RB", "WR", "WR", "WR"])
    projections = np.array([300.0, 290.0, 260.0, 295.0, 285.0, 280.0])
    roster = np.ones((1, 6), dtype=bool)

    starters = starting_lineup_mask(roster, projections, positions,
                                    {"RB": 2, "WR": 2, "FLEX": 1})[0]

    assert starters.tolist() == [True, True, False, True, True, True]


def test_flex_never_double_counts_a_dedicated_starter():
    # If FLEX were filled without excluding the players already slotted, the
    # best back would fill both RB1 and FLEX and the team would score him twice.
    positions = np.array(["RB", "RB"])
    projections = np.array([300.0, 100.0])
    starters = starting_lineup_mask(np.ones((1, 2), bool), projections, positions,
                                    {"RB": 1, "FLEX": 1})[0]
    assert starters.tolist() == [True, True]      # two players, two slots


def test_a_quarterback_can_never_fill_a_flex_slot():
    positions = np.array(["QB", "QB"])
    projections = np.array([400.0, 380.0])
    starters = starting_lineup_mask(np.ones((1, 2), bool), projections, positions,
                                    {"QB": 1, "FLEX": 1})[0]
    assert starters.tolist() == [True, False]     # the FLEX goes unfilled


def test_a_missing_position_leaves_its_slot_empty_rather_than_borrowing():
    # A team with no tight end really does start one fewer player. That is what
    # makes the hole show up as weakness instead of being papered over.
    positions = np.array(["RB", "RB"])
    projections = np.array([300.0, 290.0])
    starters = starting_lineup_mask(np.ones((1, 2), bool), projections, positions,
                                    {"RB": 2, "TE": 1})[0]
    assert starters.sum() == 2


def test_fewer_players_than_slots_does_not_invent_starters():
    # The internal sort always returns as many columns as there are slots; the
    # extras are placeholders and must not be marked.
    positions = np.array(["RB", "WR"])
    projections = np.array([300.0, 280.0])
    roster = np.array([[True, False]])           # one back, nothing else
    starters = starting_lineup_mask(roster, projections, positions, {"RB": 3})[0]
    assert starters.tolist() == [True, False]


def test_players_not_on_the_roster_are_never_started():
    positions = np.array(["RB", "RB"])
    projections = np.array([500.0, 100.0])
    roster = np.array([[False, True]])           # the stud belongs to someone else
    starters = starting_lineup_mask(roster, projections, positions, {"RB": 1})[0]
    assert starters.tolist() == [False, True]


def test_an_unprojected_player_never_displaces_a_projected_one():
    # Kickers and defenses have no projection in this app. NaN sorts
    # unpredictably, so it is replaced with a very low score -- but the player
    # stays selectable when he is the only option at his position.
    positions = np.array(["K", "K", "RB"])
    projections = np.array([np.nan, np.nan, 300.0])
    starters = starting_lineup_mask(np.ones((1, 3), bool), projections, positions,
                                    {"K": 1, "RB": 1})[0]
    assert starters[2]                            # the back starts
    assert starters[:2].sum() == 1                # exactly one kicker starts


def test_every_simulation_is_slotted_independently():
    positions = np.array(["RB", "RB"])
    projections = np.array([300.0, 250.0])
    roster = np.array([[True, False],             # sim 0 got the better back
                       [False, True]])            # sim 1 got the worse one
    starters = starting_lineup_mask(roster, projections, positions, {"RB": 1})
    assert starters.tolist() == [[True, False], [False, True]]


# ---------------------------------------------------------------------------
# lineup_points -- what does that lineup score?
# ---------------------------------------------------------------------------


def test_it_totals_only_the_starters():
    positions = np.array(["RB", "RB"])
    projections = np.array([300.0, 250.0])
    total = lineup_points(np.array([[True, False]]), projections, positions)
    assert total.tolist() == [300.0]


def test_kickers_and_defenses_are_left_out_of_the_total():
    # They are unprojected and not something you draft on purpose early, so
    # counting them would add noise to a number meant to compare draft choices.
    positions = np.array(["RB", "K", "DST"])
    projections = np.array([300.0, 120.0, 110.0])
    starters = np.ones((1, 3), dtype=bool)

    assert lineup_points(starters, projections, positions).tolist() == [300.0]
    assert lineup_points(starters, projections, positions,
                         exclude=()).tolist() == [530.0]


def test_one_unprojected_starter_does_not_nan_the_whole_team():
    positions = np.array(["RB", "TE"])
    projections = np.array([300.0, np.nan])
    total = lineup_points(np.ones((1, 2), bool), projections, positions)
    assert total.tolist() == [300.0]


def test_it_returns_one_total_per_simulation():
    positions = np.array(["RB", "RB"])
    projections = np.array([300.0, 250.0])
    roster = np.array([[True, False], [False, True], [True, True]])
    assert lineup_points(roster, projections, positions).tolist() == [300., 250., 550.]
