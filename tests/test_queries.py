"""Tests for the query layer.

Two things carry most of the weight:
  - prob_any_available must use max, not min. The reverse is a natural-looking
    mistake that returns 0% for any tier containing an early-round player while
    looking entirely reasonable.
  - VORP must let NaN propagate. Kickers and defenses have no projections, and
    substituting zero would make them look replacement-level rather than unknown.
"""

import numpy as np
import pytest

from draft_model.config import UNDRAFTED
from draft_model.queries import (
    availability_matrix, compute_vorp, cost_of_waiting, pick_percentiles,
    positional_cost_of_waiting, prob_all_available, prob_any_available,
    prob_available_at_pick, replacement_value, sim_draft_order,
    simulated_pick_distribution,
)


# --------------------------------------------------------------------------
# availability
# --------------------------------------------------------------------------

def test_prob_available_counts_undrafted_as_available():
    # The sentinel is larger than any real pick, so this works automatically --
    # but it's exactly the kind of thing that breaks silently if it changes.
    picks = np.array([[5, UNDRAFTED], [20, UNDRAFTED]], dtype=np.int16)
    assert prob_available_at_pick(picks, 0, 10) == pytest.approx(0.5)
    assert prob_available_at_pick(picks, 1, 999) == pytest.approx(1.0)


def test_availability_matrix_matches_per_player_calls():
    rng = np.random.default_rng(0)
    picks = rng.integers(1, 60, size=(200, 12)).astype(np.int16)
    my_picks = [5, 20, 35]

    grid = availability_matrix(picks, my_picks)
    assert grid.shape == (12, 3)
    for column, pick in enumerate(my_picks):
        for player in range(12):
            assert grid[player, column] == pytest.approx(
                prob_available_at_pick(picks, player, pick)
            )


# --------------------------------------------------------------------------
# THE min/max trap
# --------------------------------------------------------------------------

def test_prob_any_available_uses_max_not_min():
    # Two players. In every simulation the first goes at pick 1 and the second
    # survives to pick 50. At pick 10, "at least one available" is clearly 100%.
    # The min form would say 0%, because the EARLIEST of them went at pick 1.
    picks = np.array([[1, 50], [1, 50], [1, 50]], dtype=np.int16)

    assert prob_any_available(picks, [0, 1], 10) == pytest.approx(1.0)
    assert prob_all_available(picks, [0, 1], 10) == pytest.approx(0.0)


def test_prob_any_available_is_at_least_the_best_individual():
    # A structural property: the group can never be less likely to survive than
    # its most durable member.
    rng = np.random.default_rng(3)
    picks = rng.integers(1, 80, size=(500, 6)).astype(np.int16)
    group = [0, 2, 4]

    joint = prob_any_available(picks, group, 40)
    best_alone = max(prob_available_at_pick(picks, i, 40) for i in group)
    assert joint >= best_alone - 1e-9


def test_simulated_pick_distribution_drops_undrafted():
    picks = np.array([[10], [UNDRAFTED], [30]], dtype=np.int16)
    assert list(simulated_pick_distribution(picks, 0)) == [10, 30]


def test_pick_percentiles_excludes_undrafted():
    # Player 0 goes at 10 or 30; player 1 is never taken. Averaging the sentinel
    # in would drag the percentiles toward 999 and look like a very late pick.
    picks = np.array([[10, UNDRAFTED], [30, UNDRAFTED]], dtype=np.int16)
    bounds = pick_percentiles(picks, (0.0, 100.0))

    assert bounds.shape == (2, 2)
    assert bounds[0].tolist() == [10.0, 30.0]
    assert np.isnan(bounds[1]).all()


def test_pick_percentiles_high_is_never_later_than_low():
    # High = earliest, Low = latest. If these ever swapped, the table would read
    # backwards without anything raising.
    rng = np.random.default_rng(3)
    picks = rng.integers(1, 180, size=(500, 30)).astype(np.int16)
    bounds = pick_percentiles(picks, (5.0, 95.0))
    assert (bounds[:, 0] <= bounds[:, 1]).all()


# --------------------------------------------------------------------------
# replaying one simulation
# --------------------------------------------------------------------------

def test_sim_draft_order_inverts_the_matrix():
    # Row 0: player 0 went 3rd, player 1 went 1st, player 2 undrafted, player 3 2nd.
    picks = np.array([[3, 1, UNDRAFTED, 2]], dtype=np.int16)
    pick_numbers, columns = sim_draft_order(picks, 0)

    assert pick_numbers.tolist() == [1, 2, 3]
    assert columns.tolist() == [1, 3, 0]


def test_sim_draft_order_reads_the_requested_simulation():
    picks = np.array([[1, 2], [2, 1]], dtype=np.int16)
    assert sim_draft_order(picks, 0)[1].tolist() == [0, 1]
    assert sim_draft_order(picks, 1)[1].tolist() == [1, 0]


def test_sim_draft_order_covers_every_pick_exactly_once():
    # A real draft assigns picks 1..N to N distinct players. A board built from
    # a result that double-booked a pick would silently drop somebody.
    rng = np.random.default_rng(7)
    n_players, n_picks = 40, 24
    row = np.full(n_players, UNDRAFTED, dtype=np.int16)
    row[rng.permutation(n_players)[:n_picks]] = rng.permutation(n_picks) + 1

    pick_numbers, columns = sim_draft_order(row[None, :], 0)

    assert pick_numbers.tolist() == list(range(1, n_picks + 1))
    assert len(set(columns.tolist())) == n_picks


# --------------------------------------------------------------------------
# replacement level and VORP
# --------------------------------------------------------------------------

def test_replacement_level_uses_the_last_startable_player():
    # 2 teams starting 1 QB each -> replacement QB is the 2nd best QB.
    projections = np.array([300.0, 280.0, 260.0, 240.0])
    positions = np.array(["QB", "QB", "QB", "QB"])

    replacement = replacement_value(projections, positions, {"QB": 1}, num_teams=2)
    assert replacement["QB"] == pytest.approx(280.0)


def test_flex_allocation_falls_out_of_projections():
    # 1 team, 1 RB + 1 WR + 1 FLEX = 3 startable flex-eligible players.
    # By projection those are RB1 (200), WR1 (190), RB2 (180) -- so the flex is
    # filled by a RB, and RB replacement is 180 while WR replacement is 190.
    # No 45/45/10 constant anywhere; the split is decided by the numbers.
    projections = np.array([200.0, 180.0, 190.0, 100.0])
    positions = np.array(["RB", "RB", "WR", "WR"])

    replacement = replacement_value(
        projections, positions, {"RB": 1, "WR": 1, "FLEX": 1}, num_teams=1
    )
    assert replacement["RB"] == pytest.approx(180.0)
    assert replacement["WR"] == pytest.approx(190.0)


def test_replacement_skips_positions_with_no_projections():
    # K and DST have no projections in this app. A fabricated baseline would be
    # worse than none, so they're simply absent from the result.
    projections = np.array([300.0, 280.0, np.nan, np.nan])
    positions = np.array(["QB", "QB", "K", "K"])

    replacement = replacement_value(projections, positions,
                                    {"QB": 1, "K": 1}, num_teams=2)
    assert "QB" in replacement
    assert "K" not in replacement


def test_vorp_is_comparable_across_positions():
    projections = np.array([300.0, 250.0])
    positions = np.array(["QB", "RB"])
    replacement = {"QB": 280.0, "RB": 150.0}

    vorp = compute_vorp(projections, positions, replacement)
    # The QB scores more points but the RB is worth far more over replacement --
    # which is the entire point of the transformation.
    assert vorp[0] == pytest.approx(20.0)
    assert vorp[1] == pytest.approx(100.0)
    assert vorp[1] > vorp[0]


def test_vorp_is_nan_without_a_projection():
    projections = np.array([300.0, np.nan])
    positions = np.array(["QB", "K"])
    vorp = compute_vorp(projections, positions, {"QB": 280.0})
    assert vorp[0] == pytest.approx(20.0)
    assert np.isnan(vorp[1])


# --------------------------------------------------------------------------
# cost of waiting
# --------------------------------------------------------------------------

def test_cost_of_waiting_is_zero_when_an_equal_player_survives():
    # He's certain to be gone, but an equally good back at his position lasts
    # past your next pick. High probability, no consequence -- exactly the case
    # a raw availability percentage misreads.
    picks = np.array([[1, 30], [1, 30]], dtype=np.int16)
    vorp = np.array([50.0, 50.0])
    positions = np.array(["RB", "RB"])

    assert cost_of_waiting(picks, 0, 20, vorp, positions) == pytest.approx(0.0)


def test_cost_of_waiting_prices_the_cliff():
    # Certain to be gone; the back who survives is 40 points worse.
    picks = np.array([[1, 30], [1, 30]], dtype=np.int16)
    vorp = np.array([50.0, 10.0])
    positions = np.array(["RB", "RB"])

    assert cost_of_waiting(picks, 0, 20, vorp, positions) == pytest.approx(40.0)


def test_cost_of_waiting_scales_with_probability():
    # Available in half the simulations -> half the cost.
    picks = np.array([[1, 30], [30, 30]], dtype=np.int16)
    vorp = np.array([50.0, 10.0])
    positions = np.array(["RB", "RB"])

    assert cost_of_waiting(picks, 0, 20, vorp, positions) == pytest.approx(20.0)


def test_cost_of_waiting_uses_the_simulated_fallback_not_the_current_board():
    # REGRESSION TEST. The fallback must be "who actually survives", not "the
    # best other player nominally available". Here both backs are certain to be
    # gone, so passing on the first leaves you a replacement-level player and
    # costs his full VORP -- even though a nominally-better back exists.
    picks = np.array([[1, 2], [1, 2]], dtype=np.int16)
    vorp = np.array([50.0, 90.0])
    positions = np.array(["RB", "RB"])

    # The old "best other available" rule gave 0.0 here, because 90 > 50.
    assert cost_of_waiting(picks, 0, 20, vorp, positions) == pytest.approx(50.0)


def test_cost_of_waiting_is_zero_without_a_projection():
    picks = np.array([[1, 2], [1, 2]], dtype=np.int16)
    vorp = np.array([np.nan, 10.0])
    positions = np.array(["K", "K"])
    assert cost_of_waiting(picks, 0, 20, vorp, positions) == pytest.approx(0.0)


def test_positional_cost_sees_a_deep_tier_as_cheap():
    # Four interchangeable backs. Each individually is gone by pick 20, but one
    # of the four always survives, so waiting costs nothing. The per-player
    # metric would show four separate alarming numbers here.
    picks = np.array([
        [1, 2, 3, 25],
        [1, 2, 25, 3],
        [1, 25, 2, 3],
    ], dtype=np.int16)
    vorp = np.array([50.0, 50.0, 50.0, 50.0])
    positions = np.array(["RB"] * 4)

    assert positional_cost_of_waiting(picks, "RB", 1, 20, vorp, positions) == pytest.approx(0.0)


def test_positional_cost_prices_a_top_heavy_tier():
    # One elite back (100) and one ordinary one (10). The elite back is always
    # gone by pick 20; the fallback is 90 points worse.
    picks = np.array([[1, 25], [1, 25]], dtype=np.int16)
    vorp = np.array([100.0, 10.0])
    positions = np.array(["RB", "RB"])

    assert positional_cost_of_waiting(picks, "RB", 1, 20, vorp, positions) == pytest.approx(90.0)


def test_positional_cost_is_zero_when_the_best_survives():
    picks = np.array([[25, 30], [25, 30]], dtype=np.int16)
    vorp = np.array([100.0, 10.0])
    positions = np.array(["RB", "RB"])

    assert positional_cost_of_waiting(picks, "RB", 1, 20, vorp, positions) == pytest.approx(0.0)


def test_positional_cost_handles_a_position_with_no_projections():
    picks = np.array([[1, 2], [1, 2]], dtype=np.int16)
    vorp = np.array([np.nan, np.nan])
    positions = np.array(["DST", "DST"])
    assert positional_cost_of_waiting(picks, "DST", 1, 20, vorp, positions) == pytest.approx(0.0)
