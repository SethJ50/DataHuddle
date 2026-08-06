"""Tests for joining a team's existing picks to their simulated future ones.

The join exists because neither half is a roster on its own. A live
re-simulation starts at the CURRENT pick, so players already taken sit at
UNDRAFTED in the matrix and belong to nobody; the pick log has them but says
nothing about what is still to come. `projected_roster` puts the two together.

Getting this wrong is quiet rather than loud -- a team's roster just comes out
too small, or a player gets counted twice -- so these tests pin the seam.
"""

import numpy as np

from services.draft_runner_service import (
    DraftState, held_mask, projected_roster, resimulate, team_picks_from,
)
from tests.test_draft_runner_live import draft_through, make_board


def test_held_mask_finds_only_that_team_s_players():
    board = make_board()
    state = DraftState(config=board.config)
    draft_through(state, 4)              # picks 1-4 go to teams 1,2,3,4

    assert held_mask(state, board, 1).sum() == 1
    assert held_mask(state, board, 1)[0]          # team 1 took player 0
    assert held_mask(state, board, 2)[1]          # team 2 took player 1
    assert not held_mask(state, board, 5).any()   # team 5 has not picked


def test_held_mask_finds_a_player_recorded_by_either_id():
    # Team defenses have no canonical id, so a canonical-only match would miss
    # them. Both id kinds must work.
    board = make_board()
    state = DraftState(config=board.config)
    state.make_pick(player_id=str(board.table["ffc_player_id"].iloc[7]))

    assert held_mask(state, board, 1)[7]


def test_a_held_player_is_on_the_roster_in_every_simulation():
    # Picks already made cannot come out differently.
    board = make_board()
    state = DraftState(config=board.config)
    draft_through(state, 3)

    picks = resimulate(state, board, n_sims=50)
    roster = projected_roster(state, board, picks, team_slot=1)

    assert roster[:, 0].all()            # team 1's player 0, in all 50 sims


def test_the_roster_grows_by_exactly_the_picks_that_remain():
    # The count is not a guess: a team ends with what they hold plus one player
    # per remaining pick. If the join dropped or double-counted anything, this
    # is the test that notices.
    board = make_board()
    state = DraftState(config=board.config)
    draft_through(state, 8)

    picks = resimulate(state, board, n_sims=40)
    for team in (1, 3, 6):
        held = held_mask(state, board, team).sum()
        upcoming = len(team_picks_from(state, team, state.current_pick))
        roster = projected_roster(state, board, picks, team_slot=team)
        assert (roster.sum(axis=1) == held + upcoming).all(), f"team {team}"


def test_nobody_is_on_two_rosters_at_once():
    # The strongest whole-draft check: every player belongs to at most one team
    # in a given simulation. Catches a held/simulated overlap immediately.
    board = make_board()
    state = DraftState(config=board.config)
    draft_through(state, 10)
    picks = resimulate(state, board, n_sims=30)

    rosters = [projected_roster(state, board, picks, team_slot=t)
               for t in range(1, board.config.num_teams + 1)]

    assert np.stack(rosters).sum(axis=0).max() == 1


def test_before_any_pick_the_roster_is_purely_simulated():
    board = make_board()
    state = DraftState(config=board.config)
    picks = resimulate(state, board, n_sims=30)

    roster = projected_roster(state, board, picks, team_slot=1)

    assert not held_mask(state, board, 1).any()
    assert (roster.sum(axis=1) == board.config.num_rounds).all()


def test_at_the_end_of_a_draft_the_roster_is_purely_held():
    board = make_board(num_teams=4, num_rounds=3)
    state = DraftState(config=board.config)
    draft_through(state, 12)                     # a full 4x3 draft

    assert state.is_complete
    # No picks remain, so the matrix contributes nothing and the join is the
    # pick log alone. It must still return a full roster rather than an empty one.
    empty = np.full((5, len(board.table)), 999)
    roster = projected_roster(state, board, empty, team_slot=1)
    assert (roster.sum(axis=1) == 3).all()
