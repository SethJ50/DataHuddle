"""Tests for the simulation engine.

The headline test is test_vectorized_matches_reference: the fast implementation
is compact and clever, and clever code drifts. Proving it produces the IDENTICAL
draft to an obviously-correct slow version -- given the same boards -- is what
turns a future drift into a failing test rather than quietly changed advice.
"""

import numpy as np
import pytest

from draft_model.config import POSITIONS, UNDRAFTED, DraftConfig
from draft_model.engine import (
    draw_boards, monte_carlo_sim, position_index, sim_batch, sim_one_draft_reference,
)
from scoring import ScoringFormat


def make_config(num_teams=4, num_rounds=3, **overrides):
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
# position handling
# --------------------------------------------------------------------------

def test_position_index_round_trips():
    idx = position_index(["QB", "DST", "WR"])
    assert [POSITIONS[i] for i in idx] == ["QB", "DST", "WR"]


def test_position_index_rejects_unknown():
    # Silently mapping an unknown position to 0 would make every kicker behave
    # like a quarterback, which no downstream test would obviously catch.
    with pytest.raises(ValueError, match="unknown positions"):
        position_index(["QB", "PUNTER"])


# --------------------------------------------------------------------------
# board drawing
# --------------------------------------------------------------------------

def test_boards_have_the_right_shape_and_seed_reproducibly():
    mu, sd, _ = make_pool(30)
    a = draw_boards(mu, sd, 12, np.random.default_rng(7), n_sims=5)
    b = draw_boards(mu, sd, 12, np.random.default_rng(7), n_sims=5)
    assert a.shape == (5, 30, 12)
    assert np.array_equal(a, b)


def test_marginal_spread_is_unchanged_by_rho():
    # The whole point of the sqrt(1 - rho^2) coefficient: each manager's opinion
    # stays N(mu, sd) for ANY rho. rho only changes how much they AGREE.
    mu = np.array([50.0])
    sd = np.array([10.0])
    for rho in (0.0, 0.35, 0.9):
        boards = draw_boards(mu, sd, 4, np.random.default_rng(1), n_sims=20000, rho=rho)
        assert boards.std() == pytest.approx(10.0, rel=0.05)


def test_rho_controls_agreement_between_managers():
    mu, sd = np.arange(1.0, 41.0), np.full(40, 8.0)

    independent = draw_boards(mu, sd, 6, np.random.default_rng(3), n_sims=400, rho=0.0)
    correlated = draw_boards(mu, sd, 6, np.random.default_rng(3), n_sims=400, rho=0.9)

    def mean_agreement(boards):
        # Correlation between manager 0 and manager 1's deviations from centre.
        a = boards[:, :, 0] - mu[None, :]
        b = boards[:, :, 1] - mu[None, :]
        return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])

    assert mean_agreement(independent) < 0.15
    assert mean_agreement(correlated) > 0.7


# --------------------------------------------------------------------------
# THE headline test
# --------------------------------------------------------------------------

def test_vectorized_matches_reference():
    # Same boards in, identical draft out. Not "statistically similar" -- equal.
    config = make_config(num_teams=4, num_rounds=3)
    mu, sd, pos_index = make_pool(40, seed=11)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(5), n_sims=3)

    fast = sim_batch(boards, pos_index, config)
    for sim in range(boards.shape[0]):
        slow = sim_one_draft_reference(boards[sim], pos_index, config)
        assert np.array_equal(fast[sim], slow), f"divergence in simulation {sim}"


def test_vectorized_matches_reference_with_larger_league():
    # 12 teams x 6 rounds exercises the snake turns and the starter deadlines.
    config = make_config(num_teams=12, num_rounds=6)
    mu, sd, pos_index = make_pool(120, seed=23)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(9), n_sims=2)

    fast = sim_batch(boards, pos_index, config)
    for sim in range(boards.shape[0]):
        assert np.array_equal(fast[sim], sim_one_draft_reference(boards[sim], pos_index, config))


# --------------------------------------------------------------------------
# structural invariants
# --------------------------------------------------------------------------

def test_every_simulation_drafts_exactly_total_picks():
    config = make_config(num_teams=12, num_rounds=6)
    mu, sd, pos_index = make_pool(120, seed=2)
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=40, batch_size=16)

    drafted = (picks < UNDRAFTED).sum(axis=1)
    assert (drafted == config.total_picks).all()


def test_no_pick_number_is_used_twice_within_a_simulation():
    config = make_config(num_teams=12, num_rounds=6)
    mu, sd, pos_index = make_pool(120, seed=4)
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=25, batch_size=10)

    for row in picks:
        used = row[row < UNDRAFTED]
        assert len(set(used.tolist())) == len(used)
        assert sorted(used.tolist()) == list(range(1, config.total_picks + 1))


def test_counting_identity():
    # Pure arithmetic, no data required: by pick k, exactly k-1 players are gone.
    # Catches sign flips and unit mixups that nothing else would surface.
    config = make_config(num_teams=12, num_rounds=8)
    mu, sd, pos_index = make_pool(150, seed=6)
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=30, batch_size=15)

    for k in (13, 25, 50, 90):
        gone = (picks < k).sum(axis=1)
        assert (gone == k - 1).all(), f"pick {k}: expected {k-1} gone, saw {set(gone.tolist())}"


def test_hard_limits_are_respected():
    # No simulated manager should end up with 3 quarterbacks (HARD_LIMIT QB = 2).
    config = make_config(num_teams=4, num_rounds=10)
    mu, sd, pos_index = make_pool(80, seed=8)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(12), n_sims=6)
    picks = sim_batch(boards, pos_index, config)

    qb = position_index(["QB"])[0]
    for sim in range(picks.shape[0]):
        for team in range(config.num_teams):
            owned = [i for i in range(len(mu))
                     if picks[sim, i] < UNDRAFTED
                     and (picks[sim, i] - 1) % config.num_teams is not None]
            # Recompute ownership properly via snake order.
            from draft_model.mechanics import snake_order
            qbs = sum(
                1 for i in owned
                if pos_index[i] == qb
                and snake_order(int(picks[sim, i]), config.num_teams) == team
            )
            assert qbs <= 2


def test_keepers_are_never_drafted():
    config = make_config(num_teams=12, num_rounds=6)
    mu, sd, pos_index = make_pool(120, seed=14)

    already = np.zeros(120, dtype=bool)
    already[[0, 5, 9]] = True          # three elite players kept

    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=20, batch_size=10,
                            already_drafted=already)
    assert (picks[:, [0, 5, 9]] == UNDRAFTED).all()


def test_partial_horizon_only_fills_that_range():
    # The in-draft tool simulates ~11 picks, not 150. Everything outside the
    # window must stay UNDRAFTED.
    config = make_config(num_teams=12, num_rounds=6)
    mu, sd, pos_index = make_pool(120, seed=16)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(17), n_sims=4)

    picks = sim_batch(boards, pos_index, config, start_pick=25, end_pick=36)
    drafted = picks[picks < UNDRAFTED]
    assert drafted.min() >= 25 and drafted.max() <= 36
    assert (picks < UNDRAFTED).sum(axis=1).tolist() == [12] * 4


def test_pool_too_small_raises():
    # Silently corrupting is the alternative: argmin over an all-infinite row
    # returns 0, quietly "drafting" someone already taken.
    config = make_config(num_teams=12, num_rounds=6)   # needs 72 picks
    mu, sd, pos_index = make_pool(30, seed=18)
    boards = draw_boards(mu, sd, config.num_teams, np.random.default_rng(19), n_sims=2)

    with pytest.raises(ValueError, match="pool too small"):
        sim_batch(boards, pos_index, config)


def test_batching_does_not_change_results():
    # batch_size is a memory dial, not a behavioural one.
    config = make_config(num_teams=12, num_rounds=5)
    mu, sd, pos_index = make_pool(100, seed=20)

    one = monte_carlo_sim(mu, sd, pos_index, config, n_sims=24, batch_size=24,
                          rng=np.random.default_rng(99))
    many = monte_carlo_sim(mu, sd, pos_index, config, n_sims=24, batch_size=6,
                           rng=np.random.default_rng(99))
    assert np.array_equal(one, many)
