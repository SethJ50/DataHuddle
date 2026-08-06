"""Tests for the live re-simulation that drives the in-draft console.

These check the CHAIN -- state to simulator to columns -- rather than the
simulator itself, which draft_model/ already covers. The failure they mostly
guard against is a quiet one: numbers that look completely reasonable but were
computed against the wrong board, because a mask or a pick number was off.

The monotonicity test is the sharpest of them. A player's chance of surviving
cannot go UP at a later pick, whatever else is wrong, so a violation means the
board state and the horizon disagree somewhere.
"""

import numpy as np
import pandas as pd
import pytest

from draft_model.config import DraftConfig, Keeper
from draft_model.queries import compute_vorp, replacement_value
from scoring import ScoringFormat
from services.draft_runner_service import (
    DraftState, avail_target_pick, live_columns, positional_costs,
    positional_costs_for_team, remaining_picks, resimulate, team_picks_from,
)
from services.draft_sim_service import DraftBoard


class FakeArtifact:
    """Just the calibrated numbers a re-sim reads off a loaded artifact."""

    def __init__(self, n):
        self.mu = np.arange(1.0, n + 1)          # player i's centre is pick i+1
        self.sd = 2.0 + np.arange(n) * 0.10
        self.metadata = {"calibrated": True}


def make_board(n=120, num_teams=6, num_rounds=6, keepers=()):
    """A DraftBoard over a synthetic pool, with no database or artifact file.

    The table mirrors build_table's output: one row per player in picks-matrix
    column order, with canonical_id, name, position, adp_target and projection.
    Kickers and defenses get NO projection, exactly as in the real app, so their
    VORP is NaN and they drop out of every value calculation.
    """
    rng = np.random.default_rng(1)
    positions = rng.choice(["QB", "RB", "WR", "TE", "K", "DST"], n,
                           p=[.12, .26, .34, .14, .07, .07])
    projection = np.linspace(320.0, 40.0, n)
    projection[np.isin(positions, ["K", "DST"])] = np.nan

    table = pd.DataFrame({
        "ffc_player_id": range(1000, 1000 + n),
        "canonical_id": [f"id{i}" for i in range(n)],
        "name": [f"Player {i}" for i in range(n)],
        "position": positions,
        "team": ["DET"] * n,
        "adp_target": np.arange(1.0, n + 1),
        "projection": projection,
    })

    config = DraftConfig(year=2026, num_teams=num_teams, num_rounds=num_rounds,
                         draft_position=2, scoring_format=ScoringFormat.HALF_PPR,
                         keepers=keepers)

    replacement = replacement_value(projection, positions,
                                    config.starting_slots, num_teams)
    vorp = compute_vorp(projection, positions, replacement)
    kept = table["canonical_id"].isin(config.kept_player_ids).to_numpy()
    vorp[kept] = np.nan

    return DraftBoard(config=config, table=table, artifact=FakeArtifact(n),
                      vorp=vorp, replacement=replacement, stale=False, kept=kept)


def draft_through(state, count):
    """Take the first `count` players, in table order, to advance the draft."""
    for i in range(count):
        state.make_pick(canonical_id=f"id{i}")
    return state


# --------------------------------------------------------------------------
# The re-simulation itself
# --------------------------------------------------------------------------

def test_a_drafted_player_never_shows_up_as_available():
    board = make_board()
    state = draft_through(DraftState(config=board.config), 20)

    picks = resimulate(state, board, n_sims=200)
    columns = live_columns(state, board, picks)

    assert not columns["canonical_id"].isin(state.drafted_canonical_ids).any()
    assert len(columns) == len(board.table) - 20


def test_availability_never_increases_at_a_later_pick():
    # A player cannot become MORE likely to survive the longer you wait. If this
    # fails, the board state and the horizon disagree somewhere in the chain.
    board = make_board()
    state = draft_through(DraftState(config=board.config), 15)

    picks = resimulate(state, board, n_sims=300)
    columns = live_columns(state, board, picks)

    probability_columns = [c for c in columns.columns if c.startswith("P@")]
    assert len(probability_columns) >= 2
    grid = columns[probability_columns].to_numpy()
    assert (np.diff(grid, axis=1) <= 1e-9).all()


def test_the_best_players_are_least_likely_to_last():
    board = make_board()
    state = DraftState(config=board.config)
    picks = resimulate(state, board, n_sims=300)
    columns = live_columns(state, board, picks)

    first = [c for c in columns.columns if c.startswith("P@")][0]
    # The table is in ADP order, so early rows are the sought-after players.
    assert columns[first].iloc[:5].mean() < columns[first].iloc[-5:].mean()


def test_the_same_board_always_gives_the_same_numbers():
    # No rng is passed, so the run derives from the config's seed. Without this
    # the percentages would shimmer on every rerun with pure Monte Carlo noise.
    board = make_board()
    state = draft_through(DraftState(config=board.config), 10)
    assert np.array_equal(resimulate(state, board, n_sims=200),
                          resimulate(state, board, n_sims=200))


# --------------------------------------------------------------------------
# Keepers
# --------------------------------------------------------------------------

def test_kept_players_are_not_offered_as_candidates():
    board = make_board(keepers=(Keeper(team=3, round=2, canonical_id="id4"),))
    state = DraftState(config=board.config)

    columns = live_columns(state, board, resimulate(state, board, n_sims=200))
    assert "id4" not in set(columns["canonical_id"])


# --------------------------------------------------------------------------
# Which picks the columns are measured at
# --------------------------------------------------------------------------

def test_remaining_picks_shrink_as_the_draft_moves():
    board = make_board()
    state = DraftState(config=board.config)
    before = remaining_picks(state)

    draft_through(state, before[0])          # play past your first pick
    after = remaining_picks(state)

    assert len(after) == len(before) - 1
    assert before[0] not in after


def test_a_pick_spent_on_your_own_keeper_is_not_offered():
    # config.my_selectable_picks already drops it; this pins the runner to that
    # rather than to my_picks, which would show a pick you cannot use.
    board = make_board(keepers=(Keeper(team=2, round=3, canonical_id="id4"),))
    state = DraftState(config=board.config)
    spent = next(p for p, cid in board.config.keeper_picks.items() if cid == "id4")

    assert spent in board.config.my_picks           # you own it...
    assert spent not in remaining_picks(state)      # ...but cannot select at it


# --------------------------------------------------------------------------
# Positional cost of waiting
# --------------------------------------------------------------------------

def test_positional_costs_cover_the_scoring_positions():
    board = make_board()
    state = draft_through(DraftState(config=board.config), 12)

    costs = positional_costs(state, board, resimulate(state, board, n_sims=300))

    assert set(costs["position"]) <= {"QB", "RB", "WR", "TE"}   # K/DST have no VORP
    assert (costs["cost"] >= 0).all()
    assert costs["cost"].is_monotonic_decreasing                # sorted, urgent first


# --------------------------------------------------------------------------
# The end of the draft
# --------------------------------------------------------------------------

def test_the_end_of_the_draft_is_handled_rather_than_crashing():
    board = make_board(num_teams=4, num_rounds=3)     # 12 picks
    state = draft_through(DraftState(config=board.config), 12)
    assert state.is_complete

    picks = resimulate(state, board, n_sims=50)
    columns = live_columns(state, board, picks)

    assert not [c for c in columns.columns if c.startswith("P@")]
    assert (columns["cost_of_waiting"] == 0.0).all()
    assert positional_costs(state, board, picks).empty


# --------------------------------------------------------------------------
# The cache key
# --------------------------------------------------------------------------

def test_rewinding_to_a_different_player_changes_the_cache_key():
    # The bug this guards: keying the cache on the NUMBER of picks. Rewind and
    # take someone else and the count is identical while the draft is not, so a
    # count-keyed cache would serve the previous draft's probabilities.
    board = make_board()
    state = draft_through(DraftState(config=board.config), 3)
    original = state.state_key

    state.rewind_to(3)
    state.make_pick(canonical_id="id99")

    assert len(state.picks) == 3               # same count...
    assert state.state_key != original         # ...different draft


# --------------------------------------------------------------------------
# Which pick the availability column measures
# --------------------------------------------------------------------------

def test_avail_measures_your_next_pick_when_it_is_not_your_turn():
    board = make_board(num_teams=6, num_rounds=6)
    state = DraftState(config=board.config)          # pick 1, team 1; you are 2
    assert state.on_the_clock != board.config.draft_position
    assert avail_target_pick(state) == remaining_picks(state)[0]


def test_avail_looks_one_further_ahead_on_your_own_turn():
    # THE bug this fixes. On your turn, your next pick IS the current pick, so
    # measuring there reads ~100% for everyone still on the board -- the column
    # goes blank of information exactly when you are choosing.
    board = make_board(num_teams=6, num_rounds=6)
    state = DraftState(config=board.config)
    draft_through(state, board.config.draft_position - 1)   # up to your turn

    assert state.on_the_clock == board.config.draft_position
    mine = remaining_picks(state)
    assert mine[0] == state.current_pick        # ...which is why
    assert avail_target_pick(state) == mine[1]


def test_avail_is_dropped_on_your_final_pick():
    # Nothing later to wait for, so there is no honest number to show.
    board = make_board(num_teams=4, num_rounds=2)
    state = DraftState(config=board.config)
    while len(remaining_picks(state)) > 1:
        state.make_pick(canonical_id=f"id{len(state.picks)}")
    while state.on_the_clock != board.config.draft_position:
        state.make_pick(canonical_id=f"id{len(state.picks)}")

    assert len(remaining_picks(state)) == 1
    assert avail_target_pick(state) is None


def test_avail_is_dropped_once_your_picks_are_done():
    board = make_board(num_teams=4, num_rounds=2)
    state = DraftState(config=board.config)
    draft_through(state, board.config.total_picks)
    assert avail_target_pick(state) is None


def test_the_avail_pick_is_a_pick_you_actually_own():
    board = make_board(num_teams=6, num_rounds=6)
    state = DraftState(config=board.config)
    for _ in range(10):
        target = avail_target_pick(state)
        if target is None:
            break
        assert target in board.config.my_selectable_picks
        state.make_pick(canonical_id=f"id{len(state.picks)}")


# --------------------------------------------------------------------------
# Cost of waiting, for whoever is on the clock
# --------------------------------------------------------------------------

def test_team_picks_from_returns_that_teams_own_picks():
    board = make_board(num_teams=6, num_rounds=6)
    state = DraftState(config=board.config)
    for team in (1, 4, 6):
        owned = team_picks_from(state, team, 1)
        assert len(owned) == board.config.num_rounds
        # Snake order: team 1 picks first, team 6 last, in round 1.
        assert owned[0] == team


def test_team_picks_from_skips_picks_already_gone_by():
    board = make_board(num_teams=6, num_rounds=6)
    state = DraftState(config=board.config)
    draft_through(state, 10)
    assert all(p >= state.current_pick
               for p in team_picks_from(state, 3, state.current_pick))


def test_team_picks_from_excludes_a_pick_spent_on_that_teams_keeper():
    # A pick already spent is not a decision that team gets to make.
    board = make_board(num_teams=6, num_rounds=6,
                       keepers=(Keeper(team=4, round=2, canonical_id="id4"),))
    state = DraftState(config=board.config)
    spent = next(iter(board.config.keeper_picks))
    assert spent not in team_picks_from(state, 4, 1)


def test_different_teams_get_different_costs():
    # The point of following the clock: two managers face different decisions
    # from the same board, because their next turns are at different distances.
    board = make_board(num_teams=6, num_rounds=6)
    state = DraftState(config=board.config)
    draft_through(state, 3)
    picks = resimulate(state, board, n_sims=300)

    first = positional_costs_for_team(state, board, picks, 1)
    last = positional_costs_for_team(state, board, picks, 6)

    assert not first.empty and not last.empty
    merged = first.merge(last, on="position", suffixes=("_a", "_b"))
    assert not merged["cost_a"].equals(merged["cost_b"])


def test_a_team_with_one_pick_left_has_no_cost_of_waiting():
    # Nothing to wait FOR, so the honest answer is an empty frame.
    board = make_board(num_teams=4, num_rounds=2)
    state = DraftState(config=board.config)
    draft_through(state, 5)          # into the last round
    picks = resimulate(state, board, n_sims=100)

    on_clock = state.on_the_clock
    assert len(team_picks_from(state, on_clock, state.current_pick)) < 2
    assert positional_costs_for_team(state, board, picks, on_clock).empty


def test_your_own_costs_still_come_out_of_the_wrapper():
    # positional_costs is now a thin wrapper, and must not have drifted.
    board = make_board(num_teams=6, num_rounds=6)
    state = DraftState(config=board.config)
    draft_through(state, 4)
    picks = resimulate(state, board, n_sims=200)

    direct = positional_costs_for_team(state, board, picks,
                                       board.config.draft_position)
    assert positional_costs(state, board, picks).equals(direct)
