"""Tests for DraftState: the pick log and everything derived from it.

The riskiest thing here is team indexing. `snake_order` returns 0-indexed team
ids because numpy wants them; the log stores 1-indexed slots because people say
"I pick fifth". A mistake in that conversion does not crash -- it produces a
plausible draft in which the wrong teams own the picks, and every roster and
probability built on top is quietly wrong. Several tests below exist only to pin
that down.
"""

import numpy as np
import pytest

from draft_model.config import POSITIONS, DraftConfig, Keeper
from draft_model.engine import position_index
from scoring import ScoringFormat
from services.draft_runner_service import DraftState


def make_config(num_teams=4, num_rounds=3, **overrides):
    """A small league -- small enough to reason about a whole draft by hand."""
    values = dict(year=2026, num_teams=num_teams, num_rounds=num_rounds,
                  draft_position=1, scoring_format=ScoringFormat.FULL_PPR)
    values.update(overrides)
    return DraftConfig(**values)


def make_table(n=12):
    """A stand-in model table: one row per player, in picks-matrix column order.

    Only the columns DraftState actually reads are present -- canonical_id to
    identify a player, and position so a position number can be derived.
    """
    import pandas as pd
    return pd.DataFrame({
        "ffc_player_id": range(1, n + 1),
        "canonical_id": [f"id{i}" for i in range(n)],
        "position": [POSITIONS[i % len(POSITIONS)] for i in range(n)],
    })


# --------------------------------------------------------------------------
# Where are we in the draft?
# --------------------------------------------------------------------------

def test_a_new_draft_starts_at_pick_one_with_team_one_on_the_clock():
    state = DraftState(config=make_config())
    assert state.current_pick == 1
    assert state.on_the_clock == 1
    assert not state.is_complete


def test_the_log_length_is_the_progress():
    state = DraftState(config=make_config())
    for i in range(5):
        state.make_pick(canonical_id=f"id{i}")
    assert state.current_pick == 6


def test_round_two_runs_backwards():
    # The snake, checked through the state rather than through mechanics alone.
    # In a 4-team league picks 1-4 go 1,2,3,4 and picks 5-8 go 4,3,2,1.
    state = DraftState(config=make_config(num_teams=4))
    seen = []
    for i in range(8):
        seen.append(state.on_the_clock)
        state.make_pick(canonical_id=f"id{i}")
    assert seen == [1, 2, 3, 4, 4, 3, 2, 1]


def test_the_draft_reports_complete_and_stops_naming_a_team():
    state = DraftState(config=make_config(num_teams=2, num_rounds=2))   # 4 picks
    for i in range(4):
        state.make_pick(canonical_id=f"id{i}")
    assert state.is_complete
    assert state.on_the_clock is None
    with pytest.raises(ValueError, match="complete"):
        state.make_pick(canonical_id="id9")


# --------------------------------------------------------------------------
# Guards on making a pick
# --------------------------------------------------------------------------

def test_the_same_player_cannot_be_drafted_twice():
    # One person on two rosters would corrupt positional need, availability and
    # every roster view at once, and none of it would look obviously wrong.
    state = DraftState(config=make_config())
    state.make_pick(canonical_id="id0")
    with pytest.raises(ValueError, match="already been drafted"):
        state.make_pick(canonical_id="id0")


def test_a_player_kept_by_another_team_cannot_be_drafted():
    config = make_config(keepers=(Keeper(team=3, round=2, canonical_id="id5"),))
    state = DraftState(config=config)
    with pytest.raises(ValueError, match="kept by another team"):
        state.make_pick(canonical_id="id5")


def test_an_off_pool_pick_still_consumes_a_pick_number():
    # A live opponent takes a kicker FFC has no ADP for. We cannot name him, but
    # his pick happened and everything after it must shift by one.
    state = DraftState(config=make_config())
    state.make_pick(source="unknown")
    assert state.current_pick == 2
    assert state.drafted_canonical_ids == set()


# --------------------------------------------------------------------------
# Keepers
# --------------------------------------------------------------------------

def test_a_keeper_is_recorded_automatically_when_his_pick_arrives():
    # Team 3 of 4, round 1 -> pick 3.
    config = make_config(num_teams=4, num_rounds=3,
                         keepers=(Keeper(team=3, round=1, canonical_id="id7"),))
    state = DraftState(config=config)

    assert state.apply_keeper_if_due() is False      # pick 1 is a normal pick
    state.make_pick(canonical_id="id0")
    assert state.apply_keeper_if_due() is False      # pick 2 is too
    state.make_pick(canonical_id="id1")

    assert state.apply_keeper_if_due() is True       # pick 3 belongs to a keeper
    assert state.picks[-1] == {"pick": 3, "team": 3, "player_id": None,
                               "canonical_id": "id7", "source": "keeper",
                               "position": None}
    assert state.current_pick == 4


def test_back_to_back_keepers_need_the_loop():
    # Two teams keeping on consecutive picks is why callers must loop rather than
    # calling apply_keeper_if_due once.
    config = make_config(num_teams=4, num_rounds=3, keepers=(
        Keeper(team=1, round=1, canonical_id="id7"),
        Keeper(team=2, round=1, canonical_id="id8"),
    ))
    state = DraftState(config=config)

    recorded = 0
    while state.apply_keeper_if_due():
        recorded += 1
    assert recorded == 2
    assert state.current_pick == 3


def test_a_kept_player_is_unavailable_before_his_keeper_pick_arrives():
    config = make_config(keepers=(Keeper(team=4, round=3, canonical_id="id9"),))
    state = DraftState(config=config)
    assert "id9" not in state.drafted_canonical_ids        # not picked yet...
    assert "id9" in state.unavailable_canonical_ids        # ...but nobody can have him


# --------------------------------------------------------------------------
# Rewind
# --------------------------------------------------------------------------

def test_rewinding_discards_everything_after_that_pick():
    state = DraftState(config=make_config())
    for i in range(6):
        state.make_pick(canonical_id=f"id{i}")

    discarded = state.rewind_to(3)
    assert discarded == 4
    assert len(state.picks) == 2
    assert state.current_pick == 3
    assert state.drafted_canonical_ids == {"id0", "id1"}


def test_rewinding_to_one_empties_the_draft():
    state = DraftState(config=make_config())
    state.make_pick(canonical_id="id0")
    state.rewind_to(1)
    assert state.picks == []
    assert state.current_pick == 1


def test_rewinding_past_the_start_is_refused():
    state = DraftState(config=make_config())
    with pytest.raises(ValueError, match="picks start at 1"):
        state.rewind_to(0)


def test_a_rewound_player_can_be_drafted_again():
    state = DraftState(config=make_config())
    state.make_pick(canonical_id="id0")
    state.rewind_to(1)
    state.make_pick(canonical_id="id0")          # must not raise
    assert state.drafted_canonical_ids == {"id0"}


# --------------------------------------------------------------------------
# The arrays handed to the simulator
# --------------------------------------------------------------------------

def test_drafted_mask_lines_up_with_the_table_rows():
    table = make_table(12)
    state = DraftState(config=make_config())
    state.make_pick(canonical_id="id3")
    state.make_pick(canonical_id="id7")

    mask = state.drafted_mask(table)
    assert mask.sum() == 2
    assert mask[3] and mask[7]
    assert not mask[0]


def test_a_pick_by_team_one_lands_in_array_index_zero():
    # THE indexing test. The log stores 1-indexed team slots; the counts array is
    # 0-indexed. Getting this wrong credits the wrong team with every pick.
    table = make_table(12)
    pos_index = position_index(table["position"])
    state = DraftState(config=make_config(num_teams=4))

    state.make_pick(canonical_id="id0")                       # pick 1 belongs to team 1
    counts = state.roster_counts(table, pos_index)

    assert counts[0, 0].sum() == 1               # team 1 -> index 0
    assert counts[0, 1:].sum() == 0


def test_roster_counts_match_a_hand_counted_draft():
    table = make_table(12)                       # positions cycle QB,RB,WR,TE,K,DST
    pos_index = position_index(table["position"])
    state = DraftState(config=make_config(num_teams=4))

    # Picks 1-4 go to teams 1,2,3,4; picks 5-6 go back to teams 4,3.
    for i in range(6):
        state.make_pick(canonical_id=f"id{i}")

    counts = state.roster_counts(table, pos_index)
    assert counts.shape == (1, 4, len(POSITIONS))
    assert counts.sum() == 6
    assert counts[0, 0].sum() == 1               # team 1: one pick
    assert counts[0, 3].sum() == 2               # team 4: picks 4 and 5
    # id0 is a QB and went to team 1.
    assert counts[0, 0, POSITIONS.index("QB")] == 1


def test_an_off_pool_pick_is_skipped_in_the_counts():
    # He has no row, so his position cannot be tallied -- but nothing crashes and
    # the other picks are still counted correctly.
    table = make_table(12)
    pos_index = position_index(table["position"])
    state = DraftState(config=make_config(num_teams=4))

    state.make_pick(canonical_id="id0")
    state.make_pick(player_id="off-pool")                        # a kicker outside the pool

    counts = state.roster_counts(table, pos_index)
    assert counts.sum() == 1


def test_roster_counts_shape_is_ready_for_the_simulator():
    # The leading 1 matters: monte_carlo_sim takes a single row and applies it to
    # every simulation, so this needs no repeating by the caller.
    table = make_table(12)
    pos_index = position_index(table["position"])
    state = DraftState(config=make_config(num_teams=12))
    assert state.roster_counts(table, pos_index).shape == (1, 12, len(POSITIONS))
