"""Tests for calibration and validation.

Two themes:
  - The summary statistics must mask the UNDRAFTED sentinel. Forgetting to would
    drag every average toward 999 and produce garbage that still looks numeric.
  - validate_sim must actually FAIL on broken input. A validator that passes
    everything is worse than none, because it manufactures confidence.
"""

import numpy as np
import pytest

from draft_model.calibrate import (
    calibrate_sampler, draft_rate, prob_undrafted, simulated_mean_pick,
    simulated_stdev_pick, validate_sim,
)
from draft_model.config import UNDRAFTED, DraftConfig
from draft_model.engine import monte_carlo_sim, position_index
from scoring import ScoringFormat


def make_config(num_teams=6, num_rounds=4, **overrides):
    values = dict(year=2026, num_teams=num_teams, num_rounds=num_rounds,
                  draft_position=1, scoring_format=ScoringFormat.FULL_PPR)
    values.update(overrides)
    return DraftConfig(**values)


def make_pool(n_players=60, seed=0):
    rng = np.random.default_rng(seed)
    adp = np.arange(1.0, n_players + 1)
    stdev = 1.0 + adp * 0.10
    positions = rng.choice(["QB", "RB", "WR", "TE"], size=n_players,
                           p=[0.15, 0.3, 0.4, 0.15])
    return adp, stdev, position_index(positions)


# --------------------------------------------------------------------------
# summary statistics
# --------------------------------------------------------------------------

def test_mean_pick_ignores_undrafted():
    # Without masking, the 999 would pull this mean to ~334 instead of 2.
    picks = np.array([[1, UNDRAFTED], [3, UNDRAFTED]], dtype=np.int16)
    result = simulated_mean_pick(picks)
    assert result[0] == pytest.approx(2.0)
    assert np.isnan(result[1])


def test_stdev_ignores_undrafted():
    picks = np.array([[10, UNDRAFTED], [20, UNDRAFTED], [30, 5]], dtype=np.int16)
    result = simulated_stdev_pick(picks)
    assert result[0] == pytest.approx(np.std([10, 20, 30]))
    assert np.isnan(result[1])          # one observation -> no spread


def test_prob_undrafted_and_draft_rate_are_complements():
    picks = np.array([[1, UNDRAFTED], [2, UNDRAFTED], [3, 4]], dtype=np.int16)
    assert prob_undrafted(picks)[1] == pytest.approx(2 / 3)
    assert draft_rate(picks)[1] == pytest.approx(1 / 3)
    assert draft_rate(picks)[0] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

def test_calibration_reduces_error_when_there_is_error_to_reduce():
    # Calibration corrects the min-of-twelve effect, whose size scales with how
    # WIDE the spreads are. With narrow spreads the uncalibrated simulation is
    # already accurate and there is nothing to fix; this pool uses wide spreads,
    # which is the regime real FFC data sits in (measured 4.01 -> 1.50 picks).
    config = make_config(num_teams=12, num_rounds=8)
    rng = np.random.default_rng(3)
    n = 160
    adp = np.arange(1.0, n + 1)
    stdev = 1.0 + adp * 0.40                     # wide
    pos_index = position_index(rng.choice(["QB", "RB", "WR", "TE"], n,
                                          p=[.15, .3, .4, .15]))
    core = adp <= config.total_picks

    uncalibrated = monte_carlo_sim(adp, stdev, pos_index, config, n_sims=1500)
    before = np.nanmean(np.abs(adp[core] - simulated_mean_pick(uncalibrated)[core]))

    mu, sd, _ = calibrate_sampler(adp, stdev, pos_index, config,
                                  n_iterations=8, n_sims=1500, verbose=False)
    calibrated = monte_carlo_sim(mu, sd, pos_index, config, n_sims=1500)
    after = np.nanmean(np.abs(adp[core] - simulated_mean_pick(calibrated)[core]))

    assert after < before, f"calibration made it worse: {before:.2f} -> {after:.2f}"


def test_calibration_does_not_run_away():
    # REGRESSION TEST for a real bug. The mu update was originally applied to
    # every player, including those the simulation rarely drafts. For those,
    # sim_adp is conditional on the drafts where they went EARLY, so it reads
    # low, the residual reads high, mu gets pushed later, and they are drafted
    # even less often -- a positive feedback loop. One boundary player's mu
    # climbed 36.9 -> 42.4 -> 47.3 over three passes while his target stayed 31.
    config = make_config(num_teams=6, num_rounds=4)     # 24 picks, 60 players
    adp, stdev, pos_index = make_pool(60, seed=3)

    mu, _, _ = calibrate_sampler(adp, stdev, pos_index, config,
                                 n_iterations=10, n_sims=800, verbose=False)

    # Nobody should be shoved absurdly far from where the vendor put them.
    assert np.max(np.abs(mu - adp)) < 25, "mu diverged for boundary players"


def test_calibration_is_deterministic():
    # Common random numbers mean the whole loop is reproducible. Without that,
    # two runs would differ and the trace would be measuring its own noise.
    config = make_config()
    adp, stdev, pos_index = make_pool(60, seed=5)

    mu_a, sd_a, trace_a = calibrate_sampler(adp, stdev, pos_index, config,
                                            n_iterations=3, n_sims=200, verbose=False)
    mu_b, sd_b, trace_b = calibrate_sampler(adp, stdev, pos_index, config,
                                            n_iterations=3, n_sims=200, verbose=False)
    assert np.array_equal(mu_a, mu_b)
    assert np.array_equal(sd_a, sd_b)
    assert trace_a == trace_b


def test_calibration_keeps_sd_positive():
    # A zero or negative width would make a player deterministic, or crash the
    # sampler outright.
    config = make_config()
    adp, stdev, pos_index = make_pool(60, seed=7)

    _, sd, _ = calibrate_sampler(adp, stdev, pos_index, config,
                                 n_iterations=5, n_sims=200, verbose=False)
    assert (sd > 0).all()
    assert np.isfinite(sd).all()


def test_calibration_leaves_unmeasurable_players_alone():
    # Players the simulation rarely drafts have truncated statistics. Their `sd`
    # must not be chased -- feeding a truncated ratio back inflates it without
    # bound. Deep players here are far beyond the last pick.
    config = make_config(num_teams=6, num_rounds=3)   # only 18 picks
    adp, stdev, pos_index = make_pool(60, seed=9)

    _, sd, _ = calibrate_sampler(adp, stdev, pos_index, config,
                                 n_iterations=5, n_sims=200, verbose=False)
    # The deepest players are never drafted, so their width should be untouched.
    assert sd[-10:] == pytest.approx(stdev[-10:])


# --------------------------------------------------------------------------
# validation -- must actually fail on bad input
# --------------------------------------------------------------------------

def test_validate_passes_a_healthy_run():
    config = make_config()
    adp, stdev, pos_index = make_pool(60, seed=11)
    mu, sd, _ = calibrate_sampler(adp, stdev, pos_index, config,
                                  n_iterations=5, n_sims=400, verbose=False)
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=400)

    results = validate_sim(picks, adp, stdev, config, raise_on_failure=False)
    assert all(check["passed"] for check in results.values()), results


def test_validate_catches_wrong_pick_count():
    # A short draft means the pool ran dry or hard limits locked the board.
    config = make_config()
    adp, stdev, pos_index = make_pool(60, seed=13)
    picks = monte_carlo_sim(adp, stdev, pos_index, config, n_sims=50)

    picks[7, picks[7] == 1] = UNDRAFTED          # erase one selection
    results = validate_sim(picks, adp, stdev, config, raise_on_failure=False)
    assert not results["pick_count"]["passed"]


def test_validate_catches_off_by_one_pick_numbering():
    # The counting identity's real job: catching a whole-matrix numbering error.
    # Simulate the classic one -- picks recorded 0-indexed instead of 1-indexed.
    # Every other property still holds (right count, no duplicates), which is
    # exactly why this check is worth having as a separate smoke detector.
    config = make_config()
    adp, stdev, pos_index = make_pool(60, seed=15)
    picks = monte_carlo_sim(adp, stdev, pos_index, config, n_sims=50)

    shifted = np.where(picks < UNDRAFTED, picks - 1, UNDRAFTED).astype(np.int16)
    results = validate_sim(picks=shifted, adp_target=adp, stdev_target=stdev,
                           config=config, raise_on_failure=False)

    assert not results["counting_identity"]["passed"]
    assert results["unique_picks"]["passed"]      # still no repeats
    assert results["pick_count"]["passed"]        # still the right number drafted


def test_validate_catches_duplicate_pick_numbers():
    config = make_config()
    adp, stdev, pos_index = make_pool(60, seed=15)
    picks = monte_carlo_sim(adp, stdev, pos_index, config, n_sims=50)

    picks[3, picks[3] == 2] = 3                   # two players now at pick 3
    results = validate_sim(picks, adp, stdev, config, raise_on_failure=False)
    assert not results["unique_picks"]["passed"]


def test_validate_raises_by_default():
    # Default behaviour must be to stop, not to return a report nobody reads.
    config = make_config()
    adp, stdev, pos_index = make_pool(60, seed=17)
    picks = monte_carlo_sim(adp, stdev, pos_index, config, n_sims=50)
    picks[0, picks[0] == 1] = UNDRAFTED

    with pytest.raises(AssertionError):
        validate_sim(picks, adp, stdev, config)
