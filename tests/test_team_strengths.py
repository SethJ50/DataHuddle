"""Tests for the league-wide team strengths panel.

Two halves. The service builds one table scoring every team on every category;
the presentation module reshapes it into "where do I rank" and "who is best at
this". Both halves are tested here because the seam between them -- which
direction a category ranks in -- is the easiest thing to get backwards.

The subtle points:

1. Replacement categories rank BACKWARDS. A small gap means depth.
2. An unfilled lineup slot counts at REPLACEMENT level, not zero, or an ordinary
   hole looks catastrophic and "as drafted" is unreadable in round 2.
3. A category that cannot be measured is NaN, and NaN must not rank last --
   "we could not measure this" and "you are worst" are different claims.
"""

import numpy as np
import pandas as pd
import pytest

from presentation.team_strengths import (
    category_frame, category_label, category_options, my_team_frame, ranks_for,
    shade_ranks,
)
from services.draft_runner_service import (
    DraftState, _bench_average, _replacement_gap, _slot_points, resimulate,
    team_strength_table,
)
from tests.test_draft_runner_live import draft_through, make_board


# ---------------------------------------------------------------------------
# The scoring pieces
# ---------------------------------------------------------------------------


def test_an_unfilled_slot_counts_at_replacement_level():
    # A team with no tight end has not scored zero at tight end -- they would
    # stream one. Replacement still shows the hole as weakness without
    # exaggerating it into a catastrophe.
    empty = np.zeros((1, 3), dtype=bool)
    assert _slot_points(empty, np.array([300., 200., 100.]), 2, 50.0) == [100.0]


def test_a_partly_filled_slot_tops_up_only_the_gap():
    mask = np.array([[True, False, False]])
    points = _slot_points(mask, np.array([300., 200., 100.]), 2, 50.0)
    assert points.tolist() == [350.0]              # 300 played + 50 replacement


def test_bench_average_is_nan_when_there_is_nobody_spare():
    # Not zero. "No backup" and "a worthless backup" are different situations
    # and the panel should not conflate them.
    assert np.isnan(_bench_average(np.zeros((1, 3), bool), np.array([1., 2., 3.])))


def test_bench_average_is_the_mean_of_the_spares():
    mask = np.array([[True, True, False]])
    assert _bench_average(mask, np.array([200., 100., 999.])) == 150.0


def test_the_replacement_gap_is_worst_starter_minus_best_backup():
    projections = np.array([300., 250., 200., 150.])
    starters = np.array([[True, True, False, False]])
    bench = np.array([[False, False, True, True]])
    assert _replacement_gap(starters, bench, projections) == 50.0   # 250 - 200


def test_the_replacement_gap_is_never_negative():
    # The lineup is filled greedily by projection, so the best bencher can never
    # out-project the worst starter. This is a depth measure, not a lineup error.
    board = make_board(n=200, num_teams=8, num_rounds=10)
    state = DraftState(config=board.config)
    draft_through(state, 45)
    table = team_strength_table(state, board, resimulate(state, board, n_sims=40))

    gaps = table.loc["Replacement"].to_numpy(dtype=float)
    assert (gaps[np.isfinite(gaps)] >= 0).all()


def test_the_replacement_gap_is_nan_with_nobody_on_one_side():
    projections = np.array([300., 250.])
    starters = np.array([[True, False]])
    assert np.isnan(_replacement_gap(starters, np.zeros((1, 2), bool), projections))


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


@pytest.fixture
def strengths():
    """A mid-draft league of 8, scored in projected-final mode."""
    board = make_board(n=200, num_teams=8, num_rounds=10)
    state = DraftState(config=board.config)
    draft_through(state, 30)
    picks = resimulate(state, board, n_sims=60)
    return board, state, team_strength_table(state, board, picks)


def test_the_table_has_one_column_per_team(strengths):
    board, _, table = strengths
    assert list(table.columns) == list(range(1, board.config.num_teams + 1))


def test_the_lineup_total_is_the_sum_of_the_starting_slots(strengths):
    # The headline number is not computed separately -- it is the breakdown added
    # up, so the two can never disagree on screen.
    _, _, table = strengths
    parts = table.loc["Starting"].drop(index="Lineup total").sum()
    assert np.allclose(parts.to_numpy(), table.loc[("Starting", "Lineup total")])


def test_projected_mode_needs_the_picks_matrix(strengths):
    _, state, _ = strengths
    board = make_board(n=200, num_teams=8, num_rounds=10)
    with pytest.raises(ValueError, match="picks matrix"):
        team_strength_table(state, board, picks=None, projected=True)


def test_as_drafted_needs_no_simulation_at_all():
    board = make_board(n=200, num_teams=8, num_rounds=10)
    state = DraftState(config=board.config)
    draft_through(state, 30)
    table = team_strength_table(state, board, projected=False)
    assert np.isfinite(table.loc[("Starting", "Lineup total")].to_numpy()).all()


def test_the_two_modes_disagree_and_projected_is_the_larger():
    # Projected-final fills the remaining picks with real players; as-drafted
    # leaves those slots at replacement. So projected must be at least as strong,
    # and this is the check that the toggle actually changes anything.
    board = make_board(n=200, num_teams=8, num_rounds=10)
    state = DraftState(config=board.config)
    draft_through(state, 30)
    picks = resimulate(state, board, n_sims=60)

    row = ("Starting", "Lineup total")
    projected = team_strength_table(state, board, picks, projected=True).loc[row]
    drafted = team_strength_table(state, board, projected=False).loc[row]

    assert (projected >= drafted).all()
    assert (projected > drafted).any()


def test_a_team_that_drafted_nobody_still_scores_replacement_level():
    # Round 1, most teams have nothing. Their total must be a real number rather
    # than zero, or the as-drafted view is unreadable early.
    board = make_board(n=200, num_teams=8, num_rounds=10)
    state = DraftState(config=board.config)
    table = team_strength_table(state, board, projected=False)
    totals = table.loc[("Starting", "Lineup total")]
    assert (totals > 0).all()
    assert totals.nunique() == 1              # nobody has drafted; all equal


# ---------------------------------------------------------------------------
# The two views
# ---------------------------------------------------------------------------


def test_replacement_categories_rank_backwards():
    # The single easiest thing to get wrong in this panel.
    row = pd.Series({1: 10.0, 2: 50.0, 3: 30.0})
    assert ranks_for(row, lower_is_better=True)[1] == 1      # smallest is best
    assert ranks_for(row, lower_is_better=False)[2] == 1     # largest is best


def test_an_unmeasurable_category_is_not_ranked_last():
    row = pd.Series({1: 10.0, 2: np.nan, 3: 30.0})
    ranks = ranks_for(row)
    assert np.isnan(ranks[2])
    assert ranks[3] == 1


def test_my_team_view_reports_my_value_and_my_rank(strengths):
    board, _, table = strengths
    me = board.config.draft_position
    frame = my_team_frame(table, me)

    row = frame.loc[frame["Category"] == "Lineup total"].iloc[0]
    assert row["Value"] == table.loc[("Starting", "Lineup total"), me]
    assert 1 <= row["Rank"] <= board.config.num_teams
    assert row["Best"] >= row["Value"] >= row["Worst"]


def test_my_team_view_flips_best_and_worst_for_replacements(strengths):
    # "Best" must mean the best OUTCOME, not the largest number, or the column
    # says the opposite of what it means for a third of the panel.
    board, _, table = strengths
    frame = my_team_frame(table, board.config.draft_position)
    row = frame.loc[frame["Category"] == "Replacement RB"].iloc[0]
    assert row["Best"] <= row["Worst"]


def test_my_team_view_covers_every_category(strengths):
    _, _, table = strengths
    assert len(my_team_frame(table, 1)) == len(table)


def test_category_view_is_sorted_best_first(strengths):
    _, _, table = strengths

    higher = category_frame(table, "Starting", "RB")["Value"].to_numpy()
    assert (np.diff(higher) <= 0).all()               # descending

    lower = category_frame(table, "Replacement", "RB")["Value"].dropna().to_numpy()
    assert (np.diff(lower) >= 0).all()                # ascending: small is good


def test_category_view_carries_the_team_slot_for_highlighting(strengths):
    board, _, table = strengths
    frame = category_frame(table, "Starting", "WR")
    assert board.config.draft_position in set(frame["slot"])
    assert frame["Team"].iloc[0] == f"Team {frame['slot'].iloc[0]}"


def test_category_options_hides_anything_nobody_can_be_scored_on():
    # Before anyone has a spare player, bench and replacement are NaN for every
    # team. Offering them would only ever show an empty table.
    board = make_board(n=200, num_teams=8, num_rounds=10)
    state = DraftState(config=board.config)
    table = team_strength_table(state, board, projected=False)

    options = category_options(table)
    assert ("Starting", "Lineup total") in options
    assert ("Bench", "RB") not in options


def test_category_label_does_not_repeat_itself():
    assert category_label("Starting", "Lineup total") == "Lineup total"
    assert category_label("Starting", "RB") == "Starting RB"
    assert category_label("Bench", "TE") == "Bench TE"


def test_the_rank_shading_darkens_towards_first_place():
    frame = pd.DataFrame({"Rank": [1.0, 12.0, np.nan]})
    styles = shade_ranks(12)(frame)

    assert styles.iloc[0, 0] and styles.iloc[1, 0]
    assert styles.iloc[0, 0] != styles.iloc[1, 0]
    assert styles.iloc[2, 0] == ""            # unranked stays blank
