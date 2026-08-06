"""Tests for Draft Sim mode: the AI managers and the dual-key pick log.

Two things are being pinned down here.

First, that a pick is identified by its MODEL-TABLE id rather than its canonical
one. Canonical ids come from nflreadpy and are nullable by design -- a team
defense is not a person and never resolves to one -- so keyed on them, pandas'
`isin` matches every missing id against every other. Drafting one defense marked
them all as gone and made the second impossible to take. The AI hits that case in
every full draft, since the K/DST starter deadline is pick 170.

Second, that a simulated draft is REPRODUCIBLE. The AI's opinions are drawn once
from the session seed, so rewinding and playing forward has to reproduce the same
picks exactly -- that is what lets you replay one of your own decisions against
an unchanged field.
"""

import numpy as np
import pandas as pd
import pytest

from draft_model.config import POSITIONS, DraftConfig, Keeper
from draft_model.engine import position_index
from draft_model.queries import compute_vorp, replacement_value
from scoring import ScoringFormat
from services.draft_runner_service import (
    DraftState, advance_until_your_turn, auto_pick, session_board, team_players,
)
from services.draft_sim_service import DraftBoard


class FakeArtifact:
    """Just the calibrated numbers the AI draws its opinions from."""

    def __init__(self, n):
        self.mu = np.arange(1.0, n + 1)
        self.sd = 2.0 + np.arange(n) * 0.10
        self.metadata = {"calibrated": True}


def make_board(n=160, num_teams=10, num_rounds=15, keepers=()):
    """A DraftBoard over a synthetic pool that includes kickers and defenses.

    Those two positions carry NO canonical_id, exactly as the real app produces
    them, which is the case the dual-key log exists to handle.
    """
    rng = np.random.default_rng(3)
    positions = rng.choice(["QB", "RB", "WR", "TE", "K", "DST"], n,
                           p=[.12, .26, .34, .14, .07, .07])
    projection = np.linspace(320.0, 40.0, n)
    projection[np.isin(positions, ["K", "DST"])] = np.nan

    table = pd.DataFrame({
        "ffc_player_id": range(1, n + 1),
        "canonical_id": [None if p in ("K", "DST") else f"id{i}"
                         for i, p in enumerate(positions)],
        "name": [f"Player {i}" for i in range(n)],
        "position": positions,
        "team": ["DET"] * n,
        "adp_target": np.arange(1.0, n + 1),
        "projection": projection,
    })

    config = DraftConfig(year=2026, num_teams=num_teams, num_rounds=num_rounds,
                         draft_position=3, scoring_format=ScoringFormat.HALF_PPR,
                         keepers=keepers)
    replacement = replacement_value(projection, positions,
                                    config.starting_slots, num_teams)
    vorp = compute_vorp(projection, positions, replacement)
    kept = table["canonical_id"].isin(config.kept_player_ids).to_numpy()
    vorp[kept] = np.nan

    return DraftBoard(config=config, table=table, artifact=FakeArtifact(n),
                      vorp=vorp, replacement=replacement, stale=False, kept=kept)


def play_out(state, board, boards):
    """Run a whole draft with the AI making every pick."""
    while not state.is_complete:
        if state.apply_keeper_if_due():
            continue
        auto_pick(state, board, boards)


# --------------------------------------------------------------------------
# The dual-key pick log
# --------------------------------------------------------------------------

def test_two_team_defenses_do_not_collide():
    # Keyed on canonical_id, drafting ONE defense marked them ALL as drafted and
    # the second could never be taken at all.
    table = pd.DataFrame({
        "ffc_player_id": [1, 2, 3],
        "canonical_id": ["id0", np.nan, np.nan],
        "position": ["RB", "DST", "DST"],
    })
    config = DraftConfig(year=2026, num_teams=2, num_rounds=3, draft_position=1,
                         scoring_format=ScoringFormat.FULL_PPR)
    state = DraftState(config=config)

    state.make_pick(player_id=2, canonical_id=np.nan)
    assert state.drafted_mask(table).tolist() == [False, True, False]

    state.make_pick(player_id=3, canonical_id=np.nan)
    assert state.drafted_mask(table).tolist() == [False, True, True]

    counts = state.roster_counts(table, position_index(table["position"]))
    assert counts.sum() == 2


def test_the_same_player_is_still_refused_twice():
    table = pd.DataFrame({"ffc_player_id": [1], "canonical_id": [np.nan],
                          "position": ["DST"]})
    config = DraftConfig(year=2026, num_teams=2, num_rounds=3, draft_position=1,
                         scoring_format=ScoringFormat.FULL_PPR)
    state = DraftState(config=config)
    state.make_pick(player_id=1, canonical_id=np.nan)
    with pytest.raises(ValueError, match="already been drafted"):
        state.make_pick(player_id=1)


def test_a_pick_needs_at_least_one_id():
    config = DraftConfig(year=2026, num_teams=2, num_rounds=3, draft_position=1,
                         scoring_format=ScoringFormat.FULL_PPR)
    with pytest.raises(ValueError, match="needs a player_id or a canonical_id"):
        DraftState(config=config).make_pick()


def test_a_keeper_is_recorded_by_canonical_id_alone():
    # apply_keeper_if_due has no table to look a model-table id up in, which is
    # exactly why make_pick accepts either id on its own.
    config = DraftConfig(year=2026, num_teams=4, num_rounds=3, draft_position=1,
                         scoring_format=ScoringFormat.FULL_PPR,
                         keepers=(Keeper(team=1, round=1, canonical_id="id7"),))
    state = DraftState(config=config)
    assert state.apply_keeper_if_due() is True
    assert state.picks[0]["canonical_id"] == "id7"
    assert state.picks[0]["player_id"] is None


# --------------------------------------------------------------------------
# The AI
# --------------------------------------------------------------------------

def test_a_full_simulated_draft_fills_every_pick_exactly_once():
    board = make_board()
    state = DraftState(config=board.config, seed=99)
    play_out(state, board, session_board(state, board))

    assert len(state.picks) == board.config.total_picks
    ids = [p["player_id"] for p in state.picks]
    assert len(set(ids)) == len(ids), "a player was drafted twice"


def test_the_ai_drafts_kickers_and_defenses_without_colliding():
    # The case that was broken. Their starter deadline is pick 170, so a full
    # draft always reaches them.
    board = make_board()
    state = DraftState(config=board.config, seed=99)
    play_out(state, board, session_board(state, board))

    by_id = board.table.set_index(board.table["ffc_player_id"].astype(str))
    drafted_positions = [by_id.loc[p["player_id"], "position"] for p in state.picks]
    assert drafted_positions.count("DST") > 1
    assert drafted_positions.count("K") > 1


def test_the_same_seed_reproduces_the_same_draft():
    board = make_board()

    first = DraftState(config=board.config, seed=99)
    play_out(first, board, session_board(first, board))

    second = DraftState(config=board.config, seed=99)
    play_out(second, board, session_board(second, board))

    assert [p["player_id"] for p in first.picks] == [p["player_id"] for p in second.picks]


def test_rewinding_and_replaying_reproduces_the_same_ai_picks():
    # The point of drawing opinions once from a seed: you can replay one of YOUR
    # decisions differently against an unchanged field.
    board = make_board()
    state = DraftState(config=board.config, seed=42)
    boards = session_board(state, board)
    play_out(state, board, boards)
    original = [p["player_id"] for p in state.picks]

    state.rewind_to(20)
    play_out(state, board, boards)

    assert [p["player_id"] for p in state.picks] == original


def test_a_different_seed_gives_a_different_draft():
    board = make_board()
    a = DraftState(config=board.config, seed=1)
    b = DraftState(config=board.config, seed=2)
    play_out(a, board, session_board(a, board))
    play_out(b, board, session_board(b, board))
    assert [p["player_id"] for p in a.picks] != [p["player_id"] for p in b.picks]


def test_the_ai_never_takes_a_kept_player():
    board = make_board(keepers=(Keeper(team=5, round=1, canonical_id="id0"),))
    state = DraftState(config=board.config, seed=7)
    play_out(state, board, session_board(state, board))

    taken = [p for p in state.picks if p.get("canonical_id") == "id0"]
    assert len(taken) == 1
    assert taken[0]["source"] == "keeper"


def test_every_ai_pick_is_marked_auto():
    board = make_board(num_rounds=3)
    state = DraftState(config=board.config, seed=5)
    play_out(state, board, session_board(state, board))
    assert {p["source"] for p in state.picks} == {"auto"}


# --------------------------------------------------------------------------
# Advancing to your turn
# --------------------------------------------------------------------------

def test_advancing_stops_when_it_is_your_turn():
    board = make_board(num_rounds=5)
    state = DraftState(config=board.config, seed=11)
    boards = session_board(state, board)

    made = advance_until_your_turn(state, board, boards)

    assert state.on_the_clock == board.config.draft_position
    assert made == board.config.draft_position - 1     # picks 1 and 2, you are 3


def test_advancing_from_your_own_turn_does_nothing():
    board = make_board(num_rounds=5)
    state = DraftState(config=board.config, seed=11)
    boards = session_board(state, board)
    advance_until_your_turn(state, board, boards)

    assert advance_until_your_turn(state, board, boards) == 0


def test_advancing_respects_a_limit():
    board = make_board(num_rounds=5)
    state = DraftState(config=board.config, seed=11)
    # Your own pick is 3rd, so ask to advance past it with a limit that binds.
    advance_until_your_turn(state, board, session_board(state, board))
    state.make_pick(player_id=board.table.iloc[50]["ffc_player_id"],
                    canonical_id=board.table.iloc[50]["canonical_id"])

    made = advance_until_your_turn(state, board, session_board(state, board), limit=2)
    assert made == 2


def test_advancing_stops_at_the_end_of_the_draft():
    board = make_board(num_teams=4, num_rounds=2)
    # Nobody's slot is 3 after the draft ends, so this must terminate on
    # is_complete rather than spinning.
    state = DraftState(config=board.config, seed=4)
    boards = session_board(state, board)
    while not state.is_complete:
        advance_until_your_turn(state, board, boards)
        if not state.is_complete:
            state.make_pick(player_id=board.table.iloc[len(state.picks)]["ffc_player_id"],
                            canonical_id=board.table.iloc[len(state.picks)]["canonical_id"])
    assert state.is_complete


# --------------------------------------------------------------------------
# Rosters
# --------------------------------------------------------------------------

def test_a_teams_roster_includes_players_with_no_canonical_id():
    # A defense on your roster must still show up in the roster panel.
    board = make_board(num_rounds=6)
    state = DraftState(config=board.config, seed=8)
    play_out(state, board, session_board(state, board))

    mine = team_players(state, board, board.config.draft_position)
    drafted_by_me = [p for p in state.picks if p["team"] == board.config.draft_position]
    assert len(mine) == len(drafted_by_me)

# --------------------------------------------------------------------------
# Recording a pick for a player who is not in the pool
# --------------------------------------------------------------------------

def small_config(num_teams=4, num_rounds=3, **overrides):
    values = dict(year=2026, num_teams=num_teams, num_rounds=num_rounds,
                  draft_position=1, scoring_format=ScoringFormat.FULL_PPR)
    values.update(overrides)
    return DraftConfig(**values)


def tiny_table():
    """Three real players, so an unlisted pick has nothing to match against."""
    return pd.DataFrame({
        "ffc_player_id": [1, 2, 3],
        "canonical_id": ["id0", "id1", "id2"],
        "position": ["RB", "WR", "QB"],
    })


def test_an_unlisted_pick_consumes_a_pick_number():
    # Without this the whole draft slides: every later pick number is off by one
    # and every availability figure is measured at the wrong turn.
    state = DraftState(config=small_config())
    state.make_pick(source="unknown", position="K")

    assert state.current_pick == 2
    assert state.on_the_clock == 2
    assert state.drafted_player_ids == set()
    assert state.drafted_canonical_ids == set()


def test_an_unlisted_pick_still_counts_towards_that_teams_roster():
    # The reason a position is captured at all. Left untallied, the simulator
    # goes on believing that team still needs a kicker and keeps reaching for one.
    table = tiny_table()
    pos_index = position_index(table["position"])
    state = DraftState(config=small_config())

    state.make_pick(source="unknown", position="K")     # team 1 takes a kicker
    counts = state.roster_counts(table, pos_index)

    assert counts.sum() == 1
    assert counts[0, 0, POSITIONS.index("K")] == 1


def test_an_unlisted_pick_without_a_position_counts_nothing():
    # Nothing is ever invented: no position given, no tally made.
    table = tiny_table()
    state = DraftState(config=small_config())
    state.make_pick(source="unknown")

    assert state.current_pick == 2
    assert state.roster_counts(table, position_index(table["position"])).sum() == 0


def test_unlisted_picks_land_against_the_right_teams():
    # Snake order still governs, so three unlisted picks in a row go to teams
    # 1, 2 and 3 -- not all to whoever was first on the clock.
    table = tiny_table()
    pos_index = position_index(table["position"])
    state = DraftState(config=small_config(num_teams=4))

    for position in ("K", "DST", "K"):
        state.make_pick(source="unknown", position=position)

    counts = state.roster_counts(table, pos_index)
    assert counts[0, 0].sum() == 1
    assert counts[0, 1].sum() == 1
    assert counts[0, 2].sum() == 1
    assert counts[0, 3].sum() == 0


def test_a_position_that_is_not_a_position_is_refused():
    # A typo must fail here rather than silently never matching anything.
    state = DraftState(config=small_config())
    with pytest.raises(ValueError, match="is not a position"):
        state.make_pick(source="unknown", position="KICKER")


def test_a_real_pick_can_carry_a_position_without_changing_anything():
    # The table row wins when there is one, so a supplied position is ignored
    # rather than double-counted.
    table = tiny_table()
    pos_index = position_index(table["position"])
    state = DraftState(config=small_config())

    state.make_pick(player_id=1, canonical_id="id0", position="K")
    counts = state.roster_counts(table, pos_index)

    assert counts.sum() == 1
    assert counts[0, 0, POSITIONS.index("RB")] == 1     # from the table, not "K"


def test_an_unlisted_pick_shows_its_position_on_the_board():
    from presentation.draft_board_view import build_board_grid, entries_from_pick_log

    state = DraftState(config=small_config())
    state.make_pick(source="unknown", position="K")

    grid = build_board_grid(entries_from_pick_log(state.picks, {}),
                            state.config)
    assert grid.loc["R1"].iloc[0] == "1. Unknown (K)"


def test_an_unlisted_pick_with_no_position_shows_a_placeholder():
    from presentation.draft_board_view import build_board_grid, entries_from_pick_log

    state = DraftState(config=small_config())
    state.make_pick(source="unknown")

    grid = build_board_grid(entries_from_pick_log(state.picks, {}),
                            state.config)
    assert grid.loc["R1"].iloc[0] == "1. —"


def test_unlisted_picks_survive_a_rewind():
    state = DraftState(config=small_config())
    state.make_pick(source="unknown", position="K")
    state.make_pick(source="unknown", position="DST")
    state.rewind_to(2)

    assert len(state.picks) == 1
    assert state.picks[0]["position"] == "K"
