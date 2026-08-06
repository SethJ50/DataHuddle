"""Tests for the adjusted risk and upside metrics.

Raw upside is close to a copy of the projection -- at running back the two agree
93% of the time -- so showing it would be a second, blurrier `Proj` column. The
adjustment removes that overlap and keeps only what is new. The aggregation rules
then differ on purpose:

- UPSIDE takes the best one or two, because "do I have a ceiling?" is a question
  about the best player in a group. An average would let a bust cancel a boom.
- RISK is weighted by projection, because "how exposed am I?" is a question about
  points. A shaky RB1 matters; a shaky WR3 does not.

Getting either backwards produces a number that looks reasonable and means the
wrong thing, which is why they are pinned here.
"""

import numpy as np
import pandas as pd
import pytest

from draft_model.queries import adjust_within_position
from services.draft_runner_service import (
    BEST_N_UPSIDE, DraftState, LOWER_IS_BETTER, _top_upside, _weighted_risk,
    align_ratings, resimulate, team_strength_table,
)
from tests.test_draft_runner_live import draft_through, make_board


# ---------------------------------------------------------------------------
# The adjustment
# ---------------------------------------------------------------------------


def test_it_removes_a_rating_that_only_restates_the_projection():
    # An upside that is exactly the projection carries no information of its own,
    # so after adjustment there should be nothing left.
    projections = np.array([300., 250., 200., 150., 100., 50.])
    positions = np.array(["RB"] * 6)

    adjusted = adjust_within_position(projections / 10, projections, positions)

    assert np.allclose(adjusted, 0.0, atol=1e-9)


def test_it_keeps_the_player_who_beats_his_projection():
    # Five players on a straight line and one above it. Only the outlier should
    # come out with a non-zero score, and it should be positive.
    projections = np.array([300., 250., 200., 150., 100., 50.])
    upside = np.array([9.0, 8.0, 7.0, 6.0, 5.0, 9.0])   # last one is the outlier

    adjusted = adjust_within_position(upside, projections, np.array(["WR"] * 6))

    assert adjusted[-1] == max(adjusted)
    assert adjusted[-1] > 0


def test_each_position_is_judged_on_its_own_scale():
    # The vendor grades each position separately, so a raw 8 means different
    # things at different positions. Centring per position is what makes a tight
    # end and a receiver at +1.5 comparable.
    positions = np.array(["QB"] * 4 + ["TE"] * 4)
    upside = np.array([9., 9., 9., 9., 3., 3., 3., 3.])
    projections = np.array([400., 380., 360., 340., 260., 240., 220., 200.])

    adjusted = adjust_within_position(upside, projections, positions)

    assert np.allclose(adjusted, 0.0)      # both flat once judged in position


def test_an_unrated_player_stays_unrated():
    # Kickers and defenses have no rating at all. They must come out NaN rather
    # than scoring zero, which would read as "perfectly average".
    positions = np.array(["RB", "RB", "RB", "K"])
    upside = np.array([8.0, 6.0, 4.0, np.nan])
    adjusted = adjust_within_position(upside, np.array([300., 200., 100., 90.]),
                                      positions)
    assert np.isnan(adjusted[3])
    assert np.isfinite(adjusted[:3]).all()


def test_a_position_with_too_few_players_is_centred_not_fitted():
    # Three points make an unreliable slope. Falling back to subtracting the
    # average keeps the metric usable and comparable rather than dropping it.
    upside = np.array([9.0, 6.0, 3.0])
    adjusted = adjust_within_position(upside, np.array([300., 200., 100.]),
                                      np.array(["TE"] * 3), minimum_players=6)
    assert np.allclose(adjusted, upside - upside.mean())


def test_identical_projections_do_not_divide_by_zero():
    # No spread in the projections means no slope to fit. Must centre instead of
    # producing infinity.
    upside = np.array([9., 7., 5., 3., 1., 8.])
    adjusted = adjust_within_position(upside, np.full(6, 200.0),
                                      np.array(["RB"] * 6))
    assert np.isfinite(adjusted).all()
    assert np.allclose(adjusted, upside - upside.mean())


def test_the_adjusted_rating_is_centred_on_zero_within_each_position():
    rng = np.random.default_rng(0)
    positions = np.array(["RB"] * 40 + ["WR"] * 40)
    projections = rng.uniform(80, 320, 80)
    upside = 0.03 * projections + rng.normal(0, 1, 80)

    adjusted = adjust_within_position(upside, projections, positions)

    for position in ("RB", "WR"):
        assert adjusted[positions == position].mean() == pytest.approx(0, abs=1e-9)


def test_the_adjustment_leaves_no_correlation_with_the_projection():
    # The whole point: what remains must say something the projection does not.
    rng = np.random.default_rng(1)
    projections = rng.uniform(80, 320, 90)
    upside = 0.03 * projections + rng.normal(0, 1, 90)     # heavily redundant

    adjusted = adjust_within_position(upside, projections, np.array(["RB"] * 90))

    assert abs(np.corrcoef(projections, upside)[0, 1]) > 0.5      # was redundant
    assert np.corrcoef(projections, adjusted)[0, 1] == pytest.approx(0, abs=1e-9)


# ---------------------------------------------------------------------------
# The aggregation rules
# ---------------------------------------------------------------------------


def test_upside_takes_the_best_players_not_the_average():
    # One lottery ticket among four dull players. An average would report this
    # group as unremarkable; the whole point is that it is not.
    upside = np.array([5.0, 0.0, 0.0, 0.0])
    mask = np.ones((1, 4), dtype=bool)
    assert _top_upside(mask, upside, best=2) == 2.5          # (5 + 0) / 2
    assert _top_upside(mask, upside, best=1) == 5.0


def test_upside_does_not_let_a_bust_cancel_a_boom():
    # Your objection to averaging, as a test. Both groups average to zero; only
    # one of them has a ceiling.
    both = _top_upside(np.ones((1, 2), bool), np.array([4.0, -4.0]), best=1)
    neither = _top_upside(np.ones((1, 2), bool), np.array([0.0, 0.0]), best=1)
    assert both > neither


def test_upside_does_not_reward_simply_owning_more_players():
    # A sum would. Two identical players should score the same as twenty.
    few = _top_upside(np.ones((1, 2), bool), np.array([3.0, 3.0]))
    many = _top_upside(np.ones((1, 20), bool), np.full(20, 3.0))
    assert few == many == 3.0


def test_upside_scores_a_group_of_one_on_that_one():
    # Not dragged towards zero by an imaginary second player.
    assert _top_upside(np.array([[True, False]]), np.array([4.0, 9.0]), best=2) == 4.0


def test_upside_is_nan_when_nobody_in_the_group_is_rated():
    assert np.isnan(_top_upside(np.zeros((1, 3), bool), np.array([1., 2., 3.])))
    assert np.isnan(_top_upside(np.ones((1, 2), bool), np.array([np.nan, np.nan])))


def test_risk_weights_by_projection_so_a_shaky_starter_dominates():
    # Same two risk values, swapped onto different players. A risky RB1 must
    # register far more than a risky WR3.
    risk = np.array([2.0, 0.0])
    mask = np.ones((1, 2), dtype=bool)

    big_player_risky = _weighted_risk(mask, risk, np.array([300.0, 100.0]))
    small_player_risky = _weighted_risk(mask, risk, np.array([100.0, 300.0]))

    assert big_player_risky > small_player_risky
    assert big_player_risky == pytest.approx(1.5)     # 2*300 / 400
    assert small_player_risky == pytest.approx(0.5)   # 2*100 / 400


def test_risk_ignores_a_player_with_no_rating():
    risk = np.array([2.0, np.nan])
    weighted = _weighted_risk(np.ones((1, 2), bool), risk,
                              np.array([100.0, 900.0]))
    assert weighted == pytest.approx(2.0)     # the unrated player is not a zero


def test_risk_is_nan_when_nobody_in_the_group_is_rated():
    assert np.isnan(_weighted_risk(np.zeros((1, 2), bool), np.array([1., 2.]),
                                   np.array([100., 200.])))


def test_risk_ranks_the_other_way_round():
    # Positive adjusted risk means shakier than his projection suggests, so a
    # small number is the good one.
    assert "Risk" in LOWER_IS_BETTER
    assert "Upside" not in LOWER_IS_BETTER


# ---------------------------------------------------------------------------
# Wiring into the panel
# ---------------------------------------------------------------------------


@pytest.fixture
def rated_board():
    """A board plus ratings for every player, keyed the way the real ones are."""
    board = make_board(n=200, num_teams=8, num_rounds=10)
    rng = np.random.default_rng(3)
    ratings = pd.DataFrame({
        "canonical_id": board.table["canonical_id"],
        "risk": rng.uniform(1, 9, len(board.table)),
        "upside": rng.uniform(1, 9, len(board.table)),
    })
    return board, ratings


def test_ratings_are_matched_onto_the_player_table(rated_board):
    board, ratings = rated_board
    adjusted = align_ratings(board, ratings)

    assert set(adjusted) == {"risk", "upside"}
    for values in adjusted.values():
        assert len(values) == len(board.table)

    # Kickers and defenses have no projection, so nothing to adjust against.
    unprojected = board.table["projection"].isna().to_numpy()
    assert np.isnan(adjusted["upside"][unprojected]).all()


def test_a_player_the_ratings_do_not_mention_comes_out_unrated(rated_board):
    board, ratings = rated_board
    adjusted = align_ratings(board, ratings.iloc[10:])
    assert np.isnan(adjusted["risk"][0])


def test_the_panel_gains_risk_and_upside_rows_only_when_ratings_are_given(rated_board):
    board, ratings = rated_board
    state = DraftState(config=board.config)
    draft_through(state, 30)
    picks = resimulate(state, board, n_sims=40)

    without = team_strength_table(state, board, picks)
    with_ratings = team_strength_table(state, board, picks, ratings=ratings)

    assert "Risk" not in without.index.get_level_values("Group")
    assert "Risk" in with_ratings.index.get_level_values("Group")
    assert "Upside" in with_ratings.index.get_level_values("Group")
    assert len(with_ratings) > len(without)


def test_bench_risk_is_deliberately_absent(rated_board):
    # A bench is where fliers belong, so high risk there is a feature. A metric
    # that penalised it would push you towards a boring bench.
    board, ratings = rated_board
    state = DraftState(config=board.config)
    draft_through(state, 30)
    table = team_strength_table(state, board, resimulate(state, board, n_sims=40),
                                ratings=ratings)

    groups = set(table.index.get_level_values("Group"))
    assert "Bench upside" in groups
    assert "Bench risk" not in groups


def test_every_team_gets_a_lineup_risk_and_upside(rated_board):
    board, ratings = rated_board
    state = DraftState(config=board.config)
    draft_through(state, 30)
    table = team_strength_table(state, board, resimulate(state, board, n_sims=40),
                                ratings=ratings)

    for row in (("Risk", "Lineup"), ("Upside", "Lineup")):
        assert np.isfinite(table.loc[row].to_numpy(dtype=float)).all()


def test_the_upside_default_is_the_documented_one():
    assert BEST_N_UPSIDE == 2
