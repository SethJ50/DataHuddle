"""Calibration and validation: making the simulator reproduce its own inputs.

THE PROBLEM
-----------
Feeding ADP straight into the sampler does not produce a simulation whose average
pick equals that ADP. A player goes to whichever of twelve managers rates him
highest, so his draft position is driven by an extreme of twelve opinions rather
than their centre. Left uncorrected, the simulator would answer availability
questions about a subtly different league than the one you are in.

Calibration is a fixed-point loop: simulate, measure how far the result sits from
the target, nudge the sampler parameters, repeat. What comes out are `mu` and
`sd` -- the values the sampler should actually be fed so that the DRAFT it
produces matches the ADP and spread that were measured in the real world.

MEASURED REALITY (2026-07-31, and it differs from the theory)
-------------------------------------------------------------
The min-of-twelve argument predicts large early drift that scales with spread.
On the real 237-player pool it does not appear: mean |drift| is ~6.6 picks,
median ~0, and the top 100 players are already within about 2 picks before any
calibration at all. The fixed pick count binds hard -- exactly 180 players go, so
the aggregate cannot drift far -- and `rho > 0` further reduces disagreement.

The residual is concentrated at the deep end and is a truncation effect: 237
players compete for 180 picks, so anyone with ADP past ~180 can only be drafted
EARLIER than his ADP. Calibration therefore has an easy problem here, not a hard
one, and a large stubborn error is evidence of a mechanics bug rather than a
reason to iterate harder.

See draft_model/DESIGN.md sections 7.1 through 7.3.
"""

import warnings

import numpy as np

from draft_model.config import ALPHA, MIN_STDEV, RHO, UNDRAFTED
from draft_model.engine import monte_carlo_sim
from draft_model.mechanics import snake_order


# ---------------------------------------------------------------------------
# Summary statistics over a picks matrix
# ---------------------------------------------------------------------------


def simulated_mean_pick(picks) -> np.ndarray:
    """
    Purpose: Average pick number per player, ignoring drafts where he went undrafted.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.

    Returns:
        np.ndarray float, (n_players,). NaN for a player never drafted in any sim.

    Notes:
        CONDITIONAL ON BEING DRAFTED, deliberately -- that is how vendors compute
        ADP. A player taken in 10% of drafts at pick 175 has an ADP of 175, not
        some blend of 175 and "never". Calibration compares this against vendor
        ADP, so the two must be defined identically or it would be chasing a
        difference in definitions rather than a difference in behaviour.
    """
    masked = np.where(picks < UNDRAFTED, picks, np.nan).astype(float)
    # A player drafted in no simulation has no mean pick. NaN is the correct
    # answer; numpy's "mean of empty slice" notice about it is just noise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(masked, axis=0)


def simulated_stdev_pick(picks) -> np.ndarray:
    """
    Purpose: Spread of pick number per player, ignoring undrafted outcomes.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.

    Returns:
        np.ndarray float, (n_players,). NaN for players drafted fewer than twice.

    Notes:
        This is TRUNCATED for deep players, and the truncation biases it DOWNWARD.
        Someone drafted in 20% of simulations only contributes his 20% of
        outcomes, all of which sit in a narrow late window -- the drafts where he
        would have gone even later simply don't record a number. So his simulated
        spread reads artificially narrow.

        That is why calibrate_sampler only trusts this for players drafted in most
        simulations. Feeding a truncated ratio back would inflate `sd` without
        bound, chasing a target the statistic cannot represent.
    """
    masked = np.where(picks < UNDRAFTED, picks, np.nan).astype(float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        spread = np.nanstd(masked, axis=0)

    # np.nanstd returns 0.0 for a player drafted exactly once. That reads as
    # "zero spread" when it means "unmeasurable" -- the same trap as FFC's
    # stdev=0, and it would make calibration chase a target of 0. NaN forces the
    # caller to skip him instead, which is what the reliability gate does.
    observations = (picks < UNDRAFTED).sum(axis=0)
    return np.where(observations >= 2, spread, np.nan)


def prob_undrafted(picks) -> np.ndarray:
    """
    Purpose: Fraction of simulations in which each player went undrafted.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.

    Returns:
        np.ndarray float, (n_players,) in [0, 1].

    Notes:
        Free, because undrafted players need no special handling anywhere: boards
        are drawn for the whole pool, the draft consumes total_picks of them, and
        the rest are simply never taken.
    """
    return (picks == UNDRAFTED).mean(axis=0)


def draft_rate(picks) -> np.ndarray:
    """Fraction of simulations in which each player WAS drafted. The complement of
    prob_undrafted, named separately because it reads better as a reliability
    weight."""
    return 1.0 - prob_undrafted(picks)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def calibrate_sampler(adp_target, stdev_target, pos_index, config,
                      n_iterations=8, n_sims=2_000, sd_clip=(0.8, 1.25),
                      alpha=ALPHA, reliability=0.8, rho=RHO, verbose=True):
    """
    Purpose: Solve for the sampler parameters that make the simulation reproduce
        the ADP and spread it was given.

    Parameters:
        adp_target (np.ndarray): Vendor ADP per player -- what the simulation's
            mean pick should equal.
        stdev_target (np.ndarray): Vendor spread per player.
        pos_index (np.ndarray): Position ordinals per player.
        config (DraftConfig): League settings; supplies the seed.
        n_iterations (int): Fixed-point passes. Converges in well under ten.
        n_sims (int): Simulations per pass. Kept low -- precision is not needed
            while tuning, only a consistent signal.
        sd_clip (tuple): Floor and ceiling on the per-iteration spread multiplier,
            so one noisy player cannot swing wildly between passes.
        alpha (float): Damping on the centre update.
        reliability (float): Only update `sd` for players drafted in at least this
            fraction of simulations.
        rho (float): Manager agreement. Held FIXED -- see Notes.
        verbose (bool): Print the error trace as it runs.

    Returns:
        tuple (mu, sd, trace):
            mu, sd -- calibrated sampler parameters. From here on these are the
                ONLY values passed to the sampler; adp_target and stdev_target
                become validation references only (invariant 4).
            trace -- list of per-iteration dicts with the errors, for inspection
                and for storing in the artifact.

    Notes:
        THREE DETAILS THAT MATTER, each of which is easy to omit and quietly
        degrades the result:

        1. COMMON RANDOM NUMBERS. Every pass re-simulates with the same seed, so
           the underlying normal draws are identical and only mu/sd differ. Without
           this, the change between two passes is mostly Monte Carlo noise, and
           the loop chases its own variance instead of converging. This comes free
           from monte_carlo_sim seeding per (config.random_seed, sim index).

        2. DAMPING. Moving the full distance to the target each pass overshoots
           and oscillates, because the target moves when you move. alpha ~0.7
           costs a pass or two and buys monotone convergence.

        3. GATING THE `sd` UPDATE. simulated_stdev_pick is truncated downward for
           players who often go undrafted (see its Notes). Feeding that ratio back
           would inflate their `sd` without bound. Players below the reliability
           threshold keep their input `sd` untouched -- FFC observed those spreads
           in real drafts, which is better evidence than a statistic this
           simulation cannot measure.

        `rho` is NOT fitted. It is confounded with `sd` -- any value produces a
        fit, because the loop simply refits `sd` around it. Fitting it would
        produce a confident-looking number that means nothing.

        WHAT A HEALTHY TRACE LOOKS LIKE: both errors fall and flatten within a few
        passes. A plateau well above the tolerance means the draft MECHANICS are
        wrong -- most often snake_order or a mutated board -- and no amount of
        further iteration will fix it. Given the measured baseline (top-100 within
        ~2 picks uncalibrated), a large stubborn error is a bug signal.
    """
    mu = np.asarray(adp_target, dtype=float).copy()
    sd = np.asarray(stdev_target, dtype=float).copy()
    adp_target = np.asarray(adp_target, dtype=float)
    stdev_target = np.asarray(stdev_target, dtype=float)

    # FIXED reference population for the error metric, chosen once: players the
    # market expects to be drafted in a league this size. The per-pass "reliable"
    # set cannot be used for this, because its MEMBERSHIP changes as calibration
    # moves players in and out of the draft -- the trace would then compare
    # different populations from pass to pass and be unreadable.
    core = adp_target <= config.total_picks

    trace = []
    if verbose:
        print(f"{'pass':>4s} {'|adp err|':>10s} {'|sd err|':>10s} {'measurable':>11s}")

    for iteration in range(n_iterations):
        picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=n_sims, rho=rho)

        sim_adp = simulated_mean_pick(picks)
        sim_sd = simulated_stdev_pick(picks)
        reliable = draft_rate(picks) >= reliability

        scored = core & np.isfinite(sim_adp)
        adp_error = float(np.mean(np.abs(adp_target[scored] - sim_adp[scored])))
        sd_scored = core & np.isfinite(sim_sd)
        sd_error = float(np.mean(np.abs(stdev_target[sd_scored] - sim_sd[sd_scored])))

        trace.append({
            "iteration": iteration,
            "adp_error": adp_error,
            "sd_error": sd_error,
            "n_measurable": int(reliable.sum()),
        })
        if verbose:
            print(f"{iteration:>4d} {adp_error:>10.3f} {sd_error:>10.3f} "
                  f"{int(reliable.sum()):>11d}")

        # --- centre: damped step, ONLY where the measurement is trustworthy ---
        # Gating `mu` on reliability is essential rather than tidy. For a player
        # drafted in half the simulations, sim_adp is conditional on the drafts
        # where he went EARLY -- the ones where he would have gone later record
        # nothing at all. So it reads low, the residual reads high, and pushing
        # mu later makes him drafted even LESS often, which biases the
        # measurement further. That is a positive feedback loop and it diverges:
        # before this gate, one boundary player's mu climbed 36.9 -> 42.4 -> 47.3
        # over three passes while his true target stayed at 31.
        #
        # Players below the threshold keep mu = adp_target. That is the honest
        # answer: their target is unreachable in a draft this size, and the
        # vendor's own measurement is better evidence than a statistic this
        # simulation cannot compute.
        adjustable = reliable & np.isfinite(sim_adp)
        residual = np.where(adjustable, adp_target - sim_adp, 0.0)
        mu = mu + alpha * residual

        # --- width: same gate, same reason ---
        measurable = reliable & np.isfinite(sim_sd) & (sim_sd > 0)
        ratio = np.ones_like(sd)
        ratio[measurable] = stdev_target[measurable] / sim_sd[measurable]
        sd = np.clip(sd * np.clip(ratio, *sd_clip), MIN_STDEV, None)

    return mu, sd, trace


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_sim(picks, adp_target, stdev_target, config, adp_tolerance=2.0,
                 reliability=0.8, checkpoints=(13, 25, 50, 100, 150),
                 raise_on_failure=True) -> dict:
    """
    Purpose: Assert a completed run is internally consistent and properly calibrated.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        adp_target, stdev_target (np.ndarray): The vendor references.
        config (DraftConfig): League settings.
        adp_tolerance (float): Maximum acceptable mean absolute ADP error, picks.
        reliability (float): Players drafted below this rate are excluded from the
            calibration check, since their statistics are truncated.
        checkpoints (tuple): Pick numbers at which to test the counting identity.
        raise_on_failure (bool): Raise on the first failure. Set False to collect
            every result for display.

    Returns:
        dict: check name -> {"passed": bool, "detail": str}.

    Raises:
        AssertionError: If any check fails and raise_on_failure is True.

    Notes:
        Run this before any output reaches a page. These checks are the difference
        between wrong numbers caught in seconds and wrong numbers discovered
        during a live draft.

        Check 4 is the valuable one and it costs nothing: by pick k, exactly k-1
        players have been taken. That is arithmetic, not a modelling assumption,
        and it requires no data at all -- which makes it catch sign flips,
        off-by-ones and unit mixups that calibration alone would happily absorb.

        Check 2 tests EVERY simulation, not the maximum. A single short draft
        means the pool ran dry or hard limits locked out the board, and averaging
        it away would hide exactly the run you need to know about.
    """
    results = {}

    def record(name, passed, detail):
        results[name] = {"passed": bool(passed), "detail": detail}
        if raise_on_failure and not passed:
            raise AssertionError(f"{name}: {detail}")

    n_sims, _ = picks.shape

    # --- 1. calibration ---
    # Reported over TWO populations, deliberately, so the gate can't be mistaken
    # for the whole story:
    #   reliable -- players drafted in most simulations. These are the ones whose
    #       availability numbers anyone actually reads, and the only ones whose
    #       simulated mean pick is an unbiased measurement. This is the gate.
    #   core -- everyone the market expects to be drafted in a league this size.
    #       Always the worse number, because it includes boundary players whose
    #       ADP sits near or past the final pick. Their targets are unreachable
    #       by construction: with more players than picks, someone with ADP past
    #       the end can only ever be drafted EARLIER than his ADP. Shown anyway,
    #       so a genuinely bad run can't hide behind a favourable subset.
    adp_target = np.asarray(adp_target)
    sim_adp = simulated_mean_pick(picks)
    reliable = draft_rate(picks) >= reliability
    core = (adp_target <= config.total_picks) & np.isfinite(sim_adp)

    error = float(np.mean(np.abs(adp_target[reliable] - sim_adp[reliable])))
    core_error = float(np.mean(np.abs(adp_target[core] - sim_adp[core])))
    record("calibration", error < adp_tolerance,
           f"mean |simulated ADP - target| = {error:.2f} picks over "
           f"{int(reliable.sum())} reliably-drafted players (tolerance {adp_tolerance}); "
           f"{core_error:.2f} over all {int(core.sum())} expected-drafted, which "
           f"includes boundary players whose targets are unreachable")

    # --- 2. every simulation drafts exactly the right number ---
    drafted = (picks < UNDRAFTED).sum(axis=1)
    ok = bool((drafted == config.total_picks).all())
    record("pick_count", ok,
           f"expected {config.total_picks} per simulation; observed "
           f"{drafted.min()}-{drafted.max()} across {n_sims} runs")

    # --- 3. no pick number reused inside one simulation ---
    # Checked on a sample: it is O(n_sims * total_picks) and a violation would be
    # systematic rather than appearing in one unlucky run.
    sample = range(0, n_sims, max(1, n_sims // 200))
    duplicates = []
    for s in sample:
        used = picks[s][picks[s] < UNDRAFTED]
        if len(set(used.tolist())) != len(used):
            duplicates.append(s)
    record("unique_picks", not duplicates,
           f"no repeated pick numbers in {len(list(sample))} sampled simulations"
           if not duplicates else f"repeats found in simulations {duplicates[:5]}")

    # --- 4. counting identity ---
    failures = []
    for k in checkpoints:
        if k > config.total_picks:
            continue
        gone = (picks < k).sum(axis=1)
        if not bool((gone == k - 1).all()):
            failures.append(f"pick {k}: saw {gone.min()}-{gone.max()}, expected {k - 1}")
    record("counting_identity", not failures,
           f"exactly k-1 players gone by pick k at {list(checkpoints)}"
           if not failures else "; ".join(failures))

    # --- 5. snake order is structurally sound ---
    # Cheap re-check of the thing that silently corrupts everything if wrong:
    # every team must own exactly one pick per round.
    order = [snake_order(p, config.num_teams, config.third_round_reversal)
             for p in range(1, config.total_picks + 1)]
    rounds_ok = all(
        sorted(order[i:i + config.num_teams]) == list(range(config.num_teams))
        for i in range(0, len(order), config.num_teams)
    )
    record("snake_order", rounds_ok,
           "every team owns exactly one pick per round"
           if rounds_ok else "a round does not contain each team exactly once")

    return results
