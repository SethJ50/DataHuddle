"""Tests for draft mechanics.

snake_order is the highest-risk function in the project. An off-by-one does not
crash -- it yields a plausible draft with the wrong teams owning the picks, and
every probability built on top is silently wrong. These tests are the only thing
standing between that bug and a whole season of confident bad advice.
"""

import pytest

from draft_model.config import DraftConfig
from draft_model.mechanics import effective_value, picks_for_slot, snake_order
from scoring import ScoringFormat


def sequence(num_teams, num_rounds, reversal=False):
    """Team id for every pick, in order -- the whole draft at a glance."""
    return [snake_order(p, num_teams, reversal)
            for p in range(1, num_teams * num_rounds + 1)]


def test_snake_order_hand_written_sequence():
    # The canonical check: 4 teams, 3 rounds, worked out by hand.
    assert sequence(4, 3) == [0, 1, 2, 3, 3, 2, 1, 0, 0, 1, 2, 3]


def test_third_round_reversal_repeats_round_two():
    full = sequence(4, 4, reversal=True)
    assert full[0:4] == [0, 1, 2, 3]     # round 1 forward
    assert full[4:8] == [3, 2, 1, 0]     # round 2 backward
    assert full[8:12] == [3, 2, 1, 0]    # round 3 repeats round 2 -- the point
    assert full[12:16] == [0, 1, 2, 3]   # round 4 flips back


def test_every_team_picks_exactly_once_per_round():
    # Structural invariant: any round must contain each team exactly once. This
    # catches whole classes of arithmetic error the hand-written case might miss.
    for num_teams in (4, 8, 10, 12, 14):
        for reversal in (False, True):
            order = sequence(num_teams, 6, reversal)
            for start in range(0, len(order), num_teams):
                assert sorted(order[start:start + num_teams]) == list(range(num_teams))


def test_first_and_last_picks():
    assert snake_order(1, 12) == 0                 # first overall
    assert snake_order(12, 12) == 11               # end of round 1
    assert snake_order(13, 12) == 11               # turn: same team picks twice
    assert snake_order(24, 12) == 0                # end of round 2


def test_picks_for_slot_matches_snake_order():
    # picks_for_slot must agree with the function it's derived from.
    for position in range(1, 13):
        picks = picks_for_slot(position, 12, 15)
        assert len(picks) == 15                             # one per round
        assert list(picks) == sorted(picks)                 # ascending
        for pick in picks:
            assert snake_order(pick, 12) == position - 1    # 1-indexed -> 0-indexed


def test_picks_for_slot_known_values():
    # Slot 5 of 12: pick 5, then the turn at 20, then 29...
    assert picks_for_slot(5, 12, 3) == (5, 20, 29)
    # Slot 1 gets the very first pick and the very last of round 2.
    assert picks_for_slot(1, 12, 2) == (1, 24)


def test_config_my_picks_is_derived():
    config = DraftConfig(year=2026, num_teams=12, num_rounds=15, draft_position=5,
                         scoring_format=ScoringFormat.HALF_PPR)
    assert config.total_picks == 180
    assert config.my_picks[:3] == (5, 20, 29)
    assert len(config.my_picks) == 15


def test_config_rejects_impossible_leagues():
    # Failing loudly at construction beats an empty simulation later.
    with pytest.raises(ValueError):
        DraftConfig(year=2026, num_teams=12, num_rounds=15, draft_position=13,
                    scoring_format=ScoringFormat.HALF_PPR)
    with pytest.raises(ValueError):
        DraftConfig(year=2026, num_teams=1, num_rounds=15, draft_position=1,
                    scoring_format=ScoringFormat.HALF_PPR)


def test_fingerprint_changes_with_simulation_inputs():
    base = dict(year=2026, num_teams=12, num_rounds=15, draft_position=5,
                scoring_format=ScoringFormat.HALF_PPR)
    original = DraftConfig(**base).fingerprint()

    assert DraftConfig(**base).fingerprint() == original          # stable
    assert DraftConfig(**{**base, "num_teams": 10}).fingerprint() != original
    assert DraftConfig(**{**base, "num_rounds": 16}).fingerprint() != original
    assert DraftConfig(**{**base, "keepers": ("x",)}).fingerprint() != original
    assert DraftConfig(**{**base, "random_seed": 1}).fingerprint() != original


def test_fingerprint_ignores_settings_the_simulation_does_not_use():
    # These change DERIVED numbers (which picks you look at, where replacement
    # level sits) but not the draft itself, and all are recomputed on load. If
    # they changed the fingerprint, editing your lineup would force a full
    # re-simulation that produces a byte-identical matrix.
    base = dict(year=2026, num_teams=12, num_rounds=15, draft_position=5,
                scoring_format=ScoringFormat.HALF_PPR)
    original = DraftConfig(**base).fingerprint()

    assert DraftConfig(**{**base, "draft_position": 6}).fingerprint() == original
    assert DraftConfig(**{**base, "roster_size": 18}).fingerprint() == original
    assert DraftConfig(**{
        **base, "starting_slots": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1},
    }).fingerprint() == original


def test_effective_value_blocks_full_positions():
    # QB limit is 2 -- a third QB must become effectively unpickable.
    assert effective_value(50.0, "QB", {"QB": 2}, 100) > 1000
    assert effective_value(50.0, "QB", {"QB": 1}, 10) == 50.0


def test_effective_value_reaches_for_empty_starter():
    # Past the deadline with zero at the position -> reach (value drops).
    assert effective_value(80.0, "RB", {"RB": 0}, 70) == 65.0
    # Before the deadline, no adjustment.
    assert effective_value(80.0, "RB", {"RB": 0}, 50) == 80.0
    # Already has one -> no reach.
    assert effective_value(80.0, "RB", {"RB": 1}, 70) == 80.0
