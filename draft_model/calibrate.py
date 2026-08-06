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
    """Work out each player's average pick number across the simulations.

    This is the simulated equivalent of vendor ADP, and calibration's whole job
    is to make the two agree. Drafts where a player went untaken are left out
    entirely rather than counted as a very late pick.

    Steps:
        1. Replace every UNDRAFTED entry with NaN, so it is treated as "no
           observation" rather than as the number 999.
        2. Average down each player's column, ignoring those NaNs.
        3. Silence numpy's "mean of empty slice" warning, which fires for a
           player drafted in no simulation at all. NaN is the correct answer
           there, so the warning is just noise.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator, where
            each entry is a pick number or UNDRAFTED.

    Returns:
        np.ndarray: One average pick number per player. NaN for a player never
            drafted in any simulation.

    Note:
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
    """Measure how much each player's pick number varies across the simulations.

    The simulated counterpart to FFC's `stdev`. Calibration compares the two and
    adjusts the sampler's width until they line up, but only for players it can
    measure reliably.

    Steps:
        1. Replace every UNDRAFTED entry with NaN, exactly as
           `simulated_mean_pick` above does.
        2. Compute the standard deviation down each player's column, ignoring
           those NaNs and silencing the empty-slice warning.
        3. Count how many simulations actually drafted each player.
        4. Return the spread only for players drafted at least twice, and NaN for
           the rest — see the inline comment for why 0 would be a trap.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.

    Returns:
        np.ndarray: One spread per player, in pick numbers. NaN for players
            drafted fewer than twice, since one observation cannot have a spread.

    Note:
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
    """Work out how often each player went undrafted across the simulations.

    A high value means the player is usually still available at the end of the
    draft. It is also the basis of the reliability weight calibration uses to
    decide whose statistics it can trust.

    Steps:
        1. Compare every entry against UNDRAFTED, giving a True/False grid.
        2. Average down each player's column. Since True counts as 1 and False as
           0, that average is exactly the fraction of simulations he went
           untaken.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.

    Returns:
        np.ndarray: One value per player between 0 and 1, where 0 means always
            drafted and 1 means never drafted.

    Note:
        Free, because undrafted players need no special handling anywhere: boards
        are drawn for the whole pool, the draft consumes total_picks of them, and
        the rest are simply never taken.
    """
    return (picks == UNDRAFTED).mean(axis=0)


def draft_rate(picks) -> np.ndarray:
    """Work out how often each player WAS drafted across the simulations.

    The exact complement of `prob_undrafted` above, given its own name because
    it reads better where it is actually used: as a reliability weight deciding
    whose simulated statistics are trustworthy enough to calibrate against.

    Steps:
        1. Call `prob_undrafted` above for the fraction never taken.
        2. Subtract that from 1.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.

    Returns:
        np.ndarray: One value per player between 0 and 1, where 1 means drafted
            in every simulation.
    """
    return 1.0 - prob_undrafted(picks)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def calibrate_sampler(adp_target, stdev_target, pos_index, config,
                      n_iterations=8, n_sims=2_000, sd_clip=(0.8, 1.25),
                      alpha=ALPHA, reliability=0.8, rho=RHO, verbose=True,
                      keeper_picks=None):
    """Solve for the sampler settings that make the simulation reproduce its inputs.

    Feeding ADP straight into the sampler does not produce drafts whose average
    pick equals that ADP, because a player goes to whoever rates him highest.
    This repeatedly simulates, measures the gap, and nudges the settings until
    the gap closes — a technique called a fixed-point loop.

    Steps:
        1. Start `mu` and `sd` at the vendor targets, copying so the caller's
           arrays are not modified.
        2. Pick the fixed "core" population used for the error report: players
           the market expects to be drafted in a league this size. Fixed, because
           a set that changed between passes would make the trace unreadable.
           Kept players are excluded from it — see the note below.
        3. For each pass, run `monte_carlo_sim` from draft_model/engine.py with
           the current settings and the same keeper picks the real run will use,
           so calibration is measuring the draft it is actually tuning.
        4. Measure the results with `simulated_mean_pick`, `simulated_stdev_pick`,
           and `draft_rate` above.
        5. Compute the average absolute error over the core population and record
           it in the trace, printing it too when verbose.
        6. Update the centre: take the gap between target and simulated ADP, but
           only for players whose measurement is trustworthy, and move `alpha` of
           the way there rather than all of it.
        7. Update the width: compute the ratio of target spread to simulated
           spread for measurable players, clamp it so one noisy player cannot
           swing wildly, and apply it with MIN_STDEV as a floor.

    Args:
        adp_target: Vendor ADP per player — what the simulation's mean pick
            should end up equalling.
        stdev_target: Vendor spread per player.
        pos_index: One position number per player.
        config: The league settings, which also supply the random seed.
        n_iterations: How many passes to run. Converges in well under ten.
        n_sims: Simulations per pass. Kept low — precision is not needed while
            tuning, only a consistent signal.
        sd_clip: A (floor, ceiling) pair limiting the per-pass width multiplier.
        alpha: How far to move toward the target each pass, between 0 and 1.
        reliability: Only update a player's settings if he was drafted in at
            least this fraction of simulations.
        rho: How much managers agree within a draft. Held FIXED — see the note.
        verbose: Print the error trace as it runs.
        keeper_picks: Which picks are spent on keepers, as a mapping from an
            overall pick number to the kept player's column index. Passed
            straight through to the simulator. Defaults to no keepers.

    Returns:
        tuple: `(mu, sd, trace)`. `mu` and `sd` are the calibrated sampler
            settings — from here on these are the ONLY values passed to the
            sampler, and adp_target/stdev_target become validation references
            only. `trace` is a list of one dictionary per pass holding
            "iteration", "adp_error", "sd_error", and "n_measurable", for
            inspection and for storing in the artifact.

    Note:
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

    # Keepers are excluded from BOTH the error metric and the updates below.
    # A kept player goes at exactly his keeper pick in every simulation, so his
    # simulated ADP is that pick number and his simulated spread is zero -- not
    # measurements of anything, just the arithmetic of a fixed assignment.
    # Scoring him would report a large error nobody can act on, and adjusting his
    # mu would tune a number the sampler never reads, since he is never selected.
    keeper_picks = dict(keeper_picks or {})
    kept = np.zeros_like(core, dtype=bool)
    if keeper_picks:
        kept[list(keeper_picks.values())] = True
    core = core & ~kept

    trace = []
    if verbose:
        print(f"{'pass':>4s} {'|adp err|':>10s} {'|sd err|':>10s} {'measurable':>11s}")

    for iteration in range(n_iterations):
        picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=n_sims, rho=rho,
                                keeper_picks=keeper_picks)

        sim_adp = simulated_mean_pick(picks)
        sim_sd = simulated_stdev_pick(picks)
        # Parentheses matter: `&` binds tighter than `>=` in Python, so without
        # them this would compare the draft rate against `reliability & ~kept`.
        reliable = (draft_rate(picks) >= reliability) & ~kept

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
                 raise_on_failure=True, keeper_picks=None) -> dict:
    """Check that a finished simulation run is consistent and properly calibrated.

    Five independent checks, run before any output reaches a page. They are the
    difference between wrong numbers caught in seconds and wrong numbers
    discovered during a live draft.

    Steps:
        1. Define a small `record` helper that stores each result and, unless
           told otherwise, raises on the first failure.
        2. Check 1, calibration: compare simulated ADP against the target over
           two populations — the reliably-drafted players, which is the actual
           gate, and the wider expected-drafted set, reported so a bad run cannot
           hide behind a favourable subset.
        3. Check 2, pick count: every single simulation must have drafted exactly
           `total_picks` players.
        4. Check 3, unique picks: within a simulation no pick number may repeat.
           Sampled rather than exhaustive, since a violation would be systematic.
        5. Check 4, counting identity: by pick k, exactly k-1 players must be
           gone. Pure arithmetic, and the cheapest real check here.
        6. Check 5, snake order: rebuild the pick order via `snake_order` from
           draft_model/mechanics.py and confirm every team owns exactly one pick
           per round.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        adp_target: The vendor ADP reference, one per player.
        stdev_target: The vendor spread reference, one per player.
        config: The league settings.
        adp_tolerance: The largest acceptable mean absolute ADP error, in picks.
        reliability: Players drafted below this rate are excluded from the
            calibration check, since their statistics are truncated.
        checkpoints: The pick numbers at which to test the counting identity.
        raise_on_failure: Raise on the first failure. Set False to collect every
            result for display instead.
        keeper_picks: Which picks were spent on keepers, as a mapping from an
            overall pick number to the kept player's column index. Used only to
            exclude those players from the calibration check, since their pick
            was assigned rather than simulated. The counting checks need no
            adjustment — see the note.

    Returns:
        dict: Maps each check name to a dictionary with "passed" (a boolean) and
            "detail" (a readable explanation, present whether it passed or not).

    Raises:
        AssertionError: If any check fails and `raise_on_failure` is True. The
            message names the failing check and its detail.

    Note:
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

        KEEPERS NEED NO SPECIAL HANDLING IN CHECKS 2, 3 AND 4, and that is by
        design rather than by luck. The simulator records a kept player at the
        pick his team spent on him, so every pick number in the draft is still
        used exactly once by exactly one player. Had keepers instead been marked
        with a "gone before the draft" sentinel, all three counting identities
        would have needed a keeper-aware correction term, and each would have
        been a place for the arithmetic to go quietly wrong.
    """
    results = {}

    def record(name, passed, detail):
        """Store one check's outcome, and raise immediately if it failed.

        Defined inside `validate_sim` so it can write into the `results`
        dictionary and read `raise_on_failure` without either being passed in.

        Steps:
            1. Store the outcome and its explanation under the check's name.
            2. If failures are meant to raise and this one failed, raise now,
               skipping the remaining checks.

        Args:
            name: The check's short name, used as the dictionary key.
            passed: Whether the check succeeded.
            detail: A readable explanation, recorded whether it passed or not.

        Raises:
            AssertionError: If the check failed and `raise_on_failure` is True.
        """
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

    # Kept players are dropped from both populations. Their pick was assigned,
    # not simulated, so scoring them measures the keeper assignment rather than
    # the model -- a round-1 keeper whose ADP is 40 would report a 39-pick
    # "error" that no amount of calibration could or should remove.
    kept = np.zeros(picks.shape[1], dtype=bool)
    if keeper_picks:
        kept[list(keeper_picks.values())] = True

    reliable = (draft_rate(picks) >= reliability) & ~kept
    core = (adp_target <= config.total_picks) & np.isfinite(sim_adp) & ~kept

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
