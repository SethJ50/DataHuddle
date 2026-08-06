"""Tests for keepers: players a team holds instead of using a draft pick.

A keeper does two things at once, and BOTH have to hold or the simulation
describes a league nobody is in:

  1. The player is off the board from pick 1. Nobody else can take him.
  2. His team's pick in that round is SPENT on him. That team makes no other
     selection there, so the draft makes fewer real selections than it has picks.

Point 2 is the one that is easy to get wrong and impossible to notice. Miss it
and every remaining player is drafted slightly later than he should be, which
looks entirely plausible -- the draft still fills every slot, it just quietly
hands out selections the real league never gets.

The counting tests below exist to pin that down.
"""

import numpy as np
import pytest

from draft_model.calibrate import validate_sim
from draft_model.config import UNDRAFTED, DraftConfig, Keeper, normalize_keepers
from draft_model.engine import (
    draw_boards, monte_carlo_sim, position_index, sim_batch, sim_one_draft_reference,
)
from draft_model.mechanics import picks_for_slot, snake_order
from scoring import ScoringFormat


def make_config(num_teams=4, num_rounds=4, **overrides):
    """A small league, small enough that a whole draft can be reasoned about."""
    values = dict(year=2026, num_teams=num_teams, num_rounds=num_rounds,
                  draft_position=1, scoring_format=ScoringFormat.FULL_PPR)
    values.update(overrides)
    return DraftConfig(**values)


def make_pool(n_players=60, seed=0):
    """A believable little player pool: ADP 1..n, width growing with ADP."""
    rng = np.random.default_rng(seed)
    mu = np.arange(1, n_players + 1, dtype=float)
    sd = 1.0 + mu * 0.12
    positions = rng.choice(["QB", "RB", "WR", "TE", "K", "DST"], size=n_players,
                           p=[0.12, 0.26, 0.34, 0.14, 0.07, 0.07])
    return mu, sd, position_index(positions)


# --------------------------------------------------------------------------
# Working out which pick a keeper consumes
# --------------------------------------------------------------------------

def test_keeper_pick_comes_from_snake_order():
    # Team 3 of 12, round 2. Round 2 runs backwards, so team 3 (slot 3) picks
    # 10th in that round: 12 + 10 = pick 22.
    config = make_config(num_teams=12, num_rounds=5,
                         keepers=(Keeper(team=3, round=2, canonical_id="p"),))
    assert config.keeper_picks == {22: "p"}


def test_keeper_pick_agrees_with_picks_for_slot():
    # Whatever the shape, the keeper's pick must be one the team actually owns.
    # Deriving it any other way is how the board and the simulation drift apart.
    config = make_config(num_teams=10, num_rounds=6, keepers=(
        Keeper(team=1, round=1, canonical_id="a"),
        Keeper(team=7, round=4, canonical_id="b"),
        Keeper(team=10, round=6, canonical_id="c"),
    ))
    for pick, canonical_id in config.keeper_picks.items():
        keeper = next(k for k in config.keepers if k.canonical_id == canonical_id)
        owned = picks_for_slot(keeper.team, 10, 6)
        assert pick == owned[keeper.round - 1]
        assert snake_order(pick, 10) == keeper.team - 1   # slots are 1-indexed


def test_keeper_pick_respects_third_round_reversal():
    plain = make_config(num_teams=8, num_rounds=5,
                        keepers=(Keeper(team=2, round=3, canonical_id="p"),))
    reversed_ = make_config(num_teams=8, num_rounds=5, third_round_reversal=True,
                            keepers=(Keeper(team=2, round=3, canonical_id="p"),))
    assert plain.keeper_picks != reversed_.keeper_picks


def test_my_selectable_picks_drops_the_pick_i_spend_on_my_own_keeper():
    config = make_config(num_teams=12, num_rounds=5, draft_position=4,
                         keepers=(Keeper(team=4, round=3, canonical_id="mine"),))
    spent = next(iter(config.keeper_picks))

    assert spent in config.my_picks              # still a pick I own...
    assert spent not in config.my_selectable_picks   # ...but not one I can use
    assert len(config.my_selectable_picks) == len(config.my_picks) - 1


def test_another_teams_keeper_does_not_touch_my_picks():
    config = make_config(num_teams=12, num_rounds=5, draft_position=4,
                         keepers=(Keeper(team=9, round=3, canonical_id="theirs"),))
    assert config.my_selectable_picks == config.my_picks


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("keeper", [
    Keeper(team=0, round=1, canonical_id="p"),      # team below the range
    Keeper(team=13, round=1, canonical_id="p"),     # team above the range
    Keeper(team=1, round=0, canonical_id="p"),      # round below the range
    Keeper(team=1, round=99, canonical_id="p"),     # round past the draft
])
def test_keeper_outside_the_league_is_rejected(keeper):
    with pytest.raises(ValueError):
        make_config(num_teams=12, num_rounds=5, keepers=(keeper,))


def test_two_keepers_on_one_teams_round_is_rejected():
    # One pick cannot be spent twice. Allowing it would silently drop one of the
    # two players from the draft entirely.
    with pytest.raises(ValueError, match="cannot be spent twice"):
        make_config(num_teams=12, num_rounds=5, keepers=(
            Keeper(team=2, round=3, canonical_id="a"),
            Keeper(team=2, round=3, canonical_id="b"),
        ))


def test_same_player_kept_by_two_teams_is_rejected():
    with pytest.raises(ValueError, match="more than one team"):
        make_config(num_teams=12, num_rounds=5, keepers=(
            Keeper(team=2, round=3, canonical_id="same"),
            Keeper(team=5, round=1, canonical_id="same"),
        ))


def test_legacy_bare_id_is_refused_with_a_useful_message():
    # Keepers used to be stored as plain ids. Passing one now means a caller was
    # missed in the migration, and the message has to say what to do about it.
    with pytest.raises(TypeError, match="Keeper"):
        make_config(num_teams=12, num_rounds=5, keepers=("00-0034796",))


def test_a_dict_is_accepted_as_a_keeper():
    config = make_config(num_teams=12, num_rounds=5, keepers=(
        {"team": 3, "round": 2, "canonical_id": "p"},
    ))
    assert config.keepers == (Keeper(3, 2, "p"),)


# --------------------------------------------------------------------------
# Reading stored keepers back
# --------------------------------------------------------------------------

def test_normalize_keepers_sorts_legacy_ids_into_unassigned():
    assigned, unassigned = normalize_keepers(["00-0034796", "00-0036322"])
    assert assigned == ()
    assert unassigned == ("00-0034796", "00-0036322")


def test_normalize_keepers_reads_the_current_shape():
    assigned, unassigned = normalize_keepers([
        {"team": 2, "round": 4, "canonical_id": "b"},
        {"team": 1, "round": 1, "canonical_id": "a"},
    ])
    assert unassigned == ()
    assert assigned == (Keeper(1, 1, "a"), Keeper(2, 4, "b"))   # sorted by team


def test_normalize_keepers_flags_a_half_filled_entry():
    # A player chosen but no round yet -- the state the UI is in mid-edit.
    assigned, unassigned = normalize_keepers([
        {"team": None, "round": None, "canonical_id": "a"},
        {"team": 3, "round": 2, "canonical_id": "b"},
    ])
    assert assigned == (Keeper(3, 2, "b"),)
    assert unassigned == ("a",)


def test_normalize_keepers_is_safe_to_run_twice():
    once, _ = normalize_keepers([{"team": 3, "round": 2, "canonical_id": "b"}])
    twice, _ = normalize_keepers(once)
    assert once == twice


BASE_DOC = {
    "num_teams": 12, "num_rounds": 5, "draft_position": 4,
    "scoring_format": "half_ppr", "platform": "espn",
}


def test_unassigned_keeper_blocks_building_a_config():
    # The important failure. An unassigned keeper has no round, so there is no
    # pick to consume -- and if he were simply ignored he would be treated as a
    # draftable player, letting somebody else take a man who is not available.
    doc = {**BASE_DOC, "has_keepers": True, "keepers": ["00-0034796"]}
    with pytest.raises(ValueError, match="need a team and a round"):
        DraftConfig.from_draft_doc(doc, year=2026)


def test_keepers_are_dropped_when_the_league_says_it_has_none():
    doc = {**BASE_DOC, "has_keepers": False,
           "keepers": [{"team": 1, "round": 1, "canonical_id": "a"}]}
    assert DraftConfig.from_draft_doc(doc, year=2026).keepers == ()


def test_a_redraft_league_has_no_keeper_picks():
    config = DraftConfig.from_draft_doc({**BASE_DOC}, year=2026)
    assert config.keeper_picks == {}
    assert config.kept_player_ids == frozenset()
    assert config.my_selectable_picks == config.my_picks


# --------------------------------------------------------------------------
# What the simulator actually does with them
# --------------------------------------------------------------------------

def test_keeper_goes_at_his_own_pick_in_every_simulation():
    config = make_config(num_teams=4, num_rounds=4)
    mu, sd, pos_index = make_pool(40, seed=3)

    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=25, batch_size=10,
                            keeper_picks={7: 0, 11: 5})

    assert (picks[:, 0] == 7).all()
    assert (picks[:, 5] == 11).all()


def test_nobody_else_can_draft_a_kept_player():
    # The player is off the board from pick 1, not just at his keeper pick. An
    # elite player kept in a late round is the case that catches this: he would
    # otherwise be taken first overall long before his keeper pick arrives.
    config = make_config(num_teams=4, num_rounds=4)
    mu, sd, pos_index = make_pool(40, seed=4)

    # Column 0 is the best player in the pool, kept at the very last pick.
    last_pick = config.total_picks
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=25, batch_size=10,
                            keeper_picks={last_pick: 0})

    assert (picks[:, 0] == last_pick).all()


def test_a_keeper_consumes_a_pick_rather_than_adding_one():
    # The heart of it. With one keeper, the draft still fills every pick number,
    # but one of those picks goes to the keeper -- so exactly one FEWER player is
    # selected than in the same draft without him.
    config = make_config(num_teams=4, num_rounds=4)
    mu, sd, pos_index = make_pool(40, seed=5)

    without = monte_carlo_sim(mu, sd, pos_index, config, n_sims=10, batch_size=10)
    with_keeper = monte_carlo_sim(mu, sd, pos_index, config, n_sims=10, batch_size=10,
                                  keeper_picks={9: 3})

    drafted_without = (without < UNDRAFTED).sum(axis=1)
    drafted_with = (with_keeper < UNDRAFTED).sum(axis=1)

    # Every pick number is still used exactly once...
    assert (drafted_without == config.total_picks).all()
    assert (drafted_with == config.total_picks).all()

    # ...but one of them was spent on a player who was never selectable, so the
    # pool of genuinely selected players is one smaller.
    selected_with = drafted_with - 1
    assert (selected_with == config.total_picks - 1).all()


def test_players_last_longer_when_picks_are_spent_on_keepers():
    # The consequence that matters for advice. Spending picks on keepers means
    # fewer selections, so a mid-round player survives further into the draft.
    config = make_config(num_teams=6, num_rounds=6)
    mu, sd, pos_index = make_pool(80, seed=6)

    # Every team keeps someone from the deep end of the pool, in round 6.
    keepers = {}
    for team in range(1, 7):
        pick = picks_for_slot(team, 6, 6)[5]
        keepers[pick] = 60 + team          # deep players nobody would draft early

    without = monte_carlo_sim(mu, sd, pos_index, config, n_sims=40, batch_size=20)
    with_keepers = monte_carlo_sim(mu, sd, pos_index, config, n_sims=40, batch_size=20,
                                   keeper_picks=keepers)

    # A player near the end of the drafted range: kept picks mean the draft
    # reaches less deep, so he goes later or not at all.
    watch = 30
    survived_without = (without[:, watch] >= UNDRAFTED).mean()
    survived_with = (with_keepers[:, watch] >= UNDRAFTED).mean()
    assert survived_with >= survived_without


def test_vectorized_matches_reference_with_keepers():
    # The whole-project invariant, extended to keepers: the fast path and the
    # obviously-correct slow path must produce the IDENTICAL draft.
    config = make_config(num_teams=4, num_rounds=4)
    mu, sd, pos_index = make_pool(40, seed=7)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(8), n_sims=3)

    keeper_picks = {2: 11, 9: 4}

    fast = sim_batch(boards, pos_index, config, keeper_picks=keeper_picks)
    for sim in range(3):
        slow = sim_one_draft_reference(boards[sim], pos_index, config,
                                       keeper_picks=keeper_picks)
        assert np.array_equal(fast[sim], slow)


def test_keeper_counts_against_his_teams_positional_limits():
    # A kept quarterback fills a quarterback slot. If he did not, his team could
    # roster one more than the hard limit allows.
    config = make_config(num_teams=4, num_rounds=4)
    mu, sd, pos_index = make_pool(40, seed=9)

    qb_columns = np.flatnonzero(pos_index == 0)
    keeper_column = int(qb_columns[0])
    keeper_pick = picks_for_slot(1, 4, 4)[0]      # team 1's first-round pick

    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(10), n_sims=2)
    fast = sim_batch(boards, pos_index, config,
                     keeper_picks={keeper_pick: keeper_column})
    slow = sim_one_draft_reference(boards[0], pos_index, config,
                                   keeper_picks={keeper_pick: keeper_column})

    # If the roster count were not bumped for the keeper, the two paths would
    # disagree the moment team 1 considers another quarterback.
    assert np.array_equal(fast[0], slow)


def test_counting_checks_still_pass_with_keepers():
    # Recording a keeper at his real pick number -- rather than at a "gone before
    # the draft" sentinel -- is what keeps the counting identities true without
    # a keeper-aware correction term in each of them.
    config = make_config(num_teams=6, num_rounds=5)
    mu, sd, pos_index = make_pool(70, seed=11)

    keepers = {picks_for_slot(team, 6, 5)[2]: 50 + team for team in range(1, 7)}
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=30, batch_size=15,
                            keeper_picks=keepers)

    results = validate_sim(picks, mu, sd, config, raise_on_failure=False,
                           keeper_picks=keepers, checkpoints=(5, 12, 25))

    for name in ("pick_count", "unique_picks", "counting_identity"):
        assert results[name]["passed"], results[name]["detail"]


def test_keepers_are_left_out_of_the_calibration_check():
    # A keeper's simulated ADP is just the pick his team spent, which has nothing
    # to do with his market ADP. Scoring him would report an error that no amount
    # of calibration could remove.
    config = make_config(num_teams=6, num_rounds=5)
    mu, sd, pos_index = make_pool(70, seed=12)

    # Keep the pool's best player in the LAST round: his ADP says pick 1, his
    # keeper pick says 30. Unexcluded, that single 29-pick gap would blow the
    # calibration tolerance on its own.
    last = picks_for_slot(1, 6, 5)[4]
    keepers = {last: 0}
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=30, batch_size=15,
                            keeper_picks=keepers)

    scored = validate_sim(picks, mu, sd, config, raise_on_failure=False,
                          keeper_picks=keepers)
    unscored = validate_sim(picks, mu, sd, config, raise_on_failure=False)

    assert scored["calibration"]["detail"] != unscored["calibration"]["detail"]


def test_pool_size_guard_does_not_count_keeper_picks():
    # Keeper picks make no selection, so they need no player behind them. A pool
    # exactly large enough for the real selections must be accepted.
    config = make_config(num_teams=2, num_rounds=3)     # 6 picks
    mu, sd, pos_index = make_pool(6, seed=13)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(14), n_sims=2)

    # Two keepers -> 4 real selections. Five players are selectable (six minus
    # the one keeper that is inside the pool), which is enough.
    sim_batch(boards, pos_index, config, keeper_picks={1: 0, 4: 1})


def test_pool_too_small_still_raises():
    config = make_config(num_teams=2, num_rounds=3)     # 6 picks
    mu, sd, pos_index = make_pool(4, seed=15)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(16), n_sims=2)

    with pytest.raises(ValueError, match="pool too small"):
        sim_batch(boards, pos_index, config)
