"""Turning a picks matrix into numbers that answer draft-day questions.

Everything here reads a completed simulation. Nothing simulates.

THE TWO AXES, KEPT SEPARATE
---------------------------
Availability comes from ADP -- WHEN a player goes. Value comes from projections --
WHAT HE IS WORTH. A probability alone cannot drive a decision: a 70% chance of
losing someone barely better than his replacement is a shrug, while a 15% chance
of losing someone with a cliff behind him is an emergency. The cost-of-waiting
functions are where the two axes finally meet.

COLUMN INDICES, NOT PLAYER IDS
------------------------------
Every function here takes integer column indices into the picks matrix. Resolving
a player id to a column is the artifact's job (SimArtifact.column_for), and doing
it there rather than here is what keeps the invariant enforceable: the matrix
means nothing without the ordering it was saved with.

See draft_model/DESIGN.md section 9.
"""

import warnings

import numpy as np

from draft_model.config import UNDRAFTED

# Positions that share the FLEX slot. Used to work out replacement level, since
# a flex spot is filled by whichever of these projects best rather than by a
# fixed share of each.
FLEX_POSITIONS = ("RB", "WR", "TE")


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def prob_available_at_pick(picks, player_idx, target_pick) -> float:
    """
    Purpose: Probability a player is still on the board at a given pick.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        player_idx (int): Column index of the player.
        target_pick (int): Absolute pick number to evaluate at.

    Returns:
        float in [0, 1].

    Notes:
        Undrafted counts as available, which is correct and automatic -- the
        UNDRAFTED sentinel is larger than any real pick number, so `>= target`
        is true for a player nobody took.
    """
    return float((picks[:, player_idx] >= target_pick).mean())


def availability_matrix(picks, target_picks) -> np.ndarray:
    """
    Purpose: Availability for every player at every pick you own, in one pass.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        target_picks (sequence[int]): Pick numbers, normally config.my_picks.

    Returns:
        np.ndarray float, (n_players, len(target_picks)).

    Notes:
        In a snake draft you only ever need availability at your OWN picks --
        about fifteen numbers, not two hundred. Computing the whole grid at once
        is what lets the Draft Plan page render without re-querying per row.
    """
    return np.column_stack([(picks >= k).mean(axis=0) for k in target_picks])


def prob_any_available(picks, player_idxs, target_pick) -> float:
    """
    Purpose: Probability AT LEAST ONE of a group is still available at a pick.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        player_idxs (sequence[int]): Column indices of the group.
        target_pick (int): Absolute pick number.

    Returns:
        float in [0, 1].

    Notes:
        USES max, NOT min. This is worth stating loudly because the reverse is a
        natural-looking mistake that produces plausible numbers:

            max(picks) >= k   ->  at least one survived   <- what we want
            min(picks) >= k   ->  EVERY one survived      <- a much rarer event

        `min >= k` says the earliest-drafted member of the group went at or after
        k, which can only happen if none of them went before k. For any tier
        containing an early-round player it returns 0% at every pick you care
        about, while looking entirely reasonable.

        This is the tier question -- "will one of these four backs get back to
        me?" -- and it is a genuinely different quantity from four separate
        percentages. Measured on the real pool, treating the players as
        independent is usually within 2 percentage points but can be off by 9,
        always understating the true chance. The players compete for the same
        picks, so one going early is precisely what lets another survive, and
        independence cannot represent that.
    """
    return float((picks[:, list(player_idxs)].max(axis=1) >= target_pick).mean())


def prob_all_available(picks, player_idxs, target_pick) -> float:
    """
    Purpose: Probability EVERY member of a group is still available at a pick.

    Parameters: As prob_any_available.
    Returns: float in [0, 1].

    Notes:
        Exists mostly so the `min` form has an honest name and cannot be mistaken
        for the `any` case. Genuinely useful for "can I still get both of these?"
    """
    return float((picks[:, list(player_idxs)].min(axis=1) >= target_pick).mean())


def simulated_pick_distribution(picks, player_idx) -> np.ndarray:
    """
    Purpose: Every pick number a player went at, across simulations.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        player_idx (int): Column index.

    Returns:
        np.ndarray of pick numbers, excluding simulations where he went undrafted.
        Empty if he was never drafted.

    Notes:
        For plotting a player's range of outcomes, which communicates far more
        than a single ADP number -- two players with identical ADP can have
        completely different shapes.
    """
    column = picks[:, player_idx]
    return column[column < UNDRAFTED]


def pick_percentiles(picks, percentiles=(5.0, 95.0)) -> np.ndarray:
    """
    Purpose: Best-case and worst-case pick numbers for every player at once.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        percentiles (sequence[float]): Which percentiles to take, 0-100.
            The default pair reads as "he went this early in his best 5% of
            drafts, and this late in his worst 5%".

    Returns:
        np.ndarray float, (n_players, len(percentiles)). Column j holds
        percentiles[j] for every player. NaN for anyone never drafted.

    Notes:
        PERCENTILES RATHER THAN min/max, deliberately. Over 10,000 simulations
        the extremes are single outliers -- almost every player in the first few
        rounds shows a min of 1 -- so they compress toward the same numbers and
        stop distinguishing anyone. The 5th/95th pair moves with the actual
        shape of the distribution.

        CONDITIONAL ON BEING DRAFTED, matching simulated_mean_pick in
        calibrate.py. Undrafted outcomes are excluded rather than treated as a
        very late pick, so all three statistics describe the same population and
        can be read side by side.

        Remember these are pick numbers, so SMALLER IS BETTER: the 5th percentile
        is the player's "high" (earliest) and the 95th is his "low" (latest),
        which is the same convention FFC uses for its high/low columns.
    """
    masked = np.where(np.asarray(picks) < UNDRAFTED, picks, np.nan).astype(float)

    # A player drafted in no simulation has no percentile. NaN is the right
    # answer; numpy's "all-NaN slice" notice about it is just noise.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanpercentile(masked, list(percentiles), axis=0).T


def sim_draft_order(picks, sim_index) -> tuple:
    """
    Purpose: Replay ONE simulated draft in the order the picks were made.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        sim_index (int): Which simulation to replay, 0-indexed.

    Returns:
        tuple (pick_numbers, columns), both 1-D arrays of the same length and
        sorted by pick number:
            pick_numbers -- absolute pick numbers, 1-indexed (1 = first overall)
            columns      -- the picks-matrix column of the player taken there

    Notes:
        The matrix is stored the "wrong" way round for this question. It answers
        "where did player i go?", and a draft board asks the inverse, "who went
        at pick k?". Sorting the row by pick number inverts it in one pass.

        Undrafted players are dropped, so the result has exactly as many entries
        as picks that were actually made. Keepers count as already-drafted before
        the simulation starts, so they never appear here either.

        Returns COLUMN INDICES, not names -- resolving a column to a player is
        the caller's job, using the table the artifact was saved alongside
        (invariant 1). Doing the lookup here would need a table this module has
        deliberately never been given.
    """
    row = np.asarray(picks[sim_index])
    drafted = np.flatnonzero(row < UNDRAFTED)
    order = drafted[np.argsort(row[drafted], kind="stable")]
    return row[order].astype(int), order


# ---------------------------------------------------------------------------
# Value: replacement level and VORP
# ---------------------------------------------------------------------------


def replacement_value(projections, positions, starting_slots, num_teams,
                      available_mask=None) -> dict:
    """
    Purpose: Projected points of the last startable player at each position.

    Parameters:
        projections (np.ndarray): Projected season points per player. NaN allowed.
        positions (np.ndarray): Position string per player.
        starting_slots (dict): Position -> starters, e.g. {"QB": 1, "RB": 2, ...}.
            A "FLEX" key is drawn from FLEX_POSITIONS.
        num_teams (int): League size.
        available_mask (np.ndarray | None): Boolean, True for players still on the
            board. Omit for the pre-draft baseline.

    Returns:
        dict: position -> replacement points. A position with no projected
        players (K and DST, which have no projections at all) is absent rather
        than present with a fake number.

    Notes:
        WHY THIS EXISTS: raw projections say a quarterback is worth more than a
        running back simply because quarterbacks score more points. What matters
        is the gap to the player you could have instead, and that gap depends
        entirely on how many of each position get started league-wide.

        FLEX IS DERIVED, NOT SPLIT BY A CONSTANT. Rather than assuming a flex is
        45% RB / 45% WR / 10% TE, take the top
        `num_teams x (RB + WR + TE + FLEX)` flex-eligible players by projection
        as the startable set, and let each position's replacement be the worst
        startable player AT that position. The allocation then falls out of the
        projections, and it adapts automatically to league size and roster shape
        instead of needing a new constant per league.

        PASSING available_mask MID-DRAFT gives a LIVE replacement level, which
        rises as a position thins out. That is what makes positional runs
        genuinely costly rather than merely alarming.
    """
    projections = np.asarray(projections, dtype=float)
    positions = np.asarray(positions)

    usable = np.isfinite(projections)
    if available_mask is not None:
        usable = usable & np.asarray(available_mask, dtype=bool)

    replacement = {}

    # --- flex-eligible positions, pooled ---
    flex_starters = sum(starting_slots.get(p, 0) for p in FLEX_POSITIONS)
    flex_slots = starting_slots.get("FLEX", 0)
    startable_count = num_teams * (flex_starters + flex_slots)

    in_flex = usable & np.isin(positions, FLEX_POSITIONS)
    if in_flex.any() and startable_count > 0:
        flex_idx = np.flatnonzero(in_flex)
        order = flex_idx[np.argsort(-projections[flex_idx])]
        startable = order[:startable_count]
        for position in FLEX_POSITIONS:
            at_position = startable[positions[startable] == position]
            if at_position.size:
                replacement[position] = float(projections[at_position].min())

    # --- positions with no flex involvement ---
    for position, starters in starting_slots.items():
        if position in FLEX_POSITIONS or position == "FLEX" or not starters:
            continue
        at_position = np.flatnonzero(usable & (positions == position))
        if not at_position.size:
            continue
        ranked = at_position[np.argsort(-projections[at_position])]
        cutoff = ranked[:num_teams * starters]
        replacement[position] = float(projections[cutoff].min())

    return replacement


def compute_vorp(projections, positions, replacement) -> np.ndarray:
    """
    Purpose: Convert projections into value over replacement.

    Parameters:
        projections (np.ndarray): Projected season points per player.
        positions (np.ndarray): Position per player.
        replacement (dict): Output of replacement_value.

    Returns:
        np.ndarray float, (n_players,). NaN where the player has no projection or
        his position has no replacement baseline.

    Notes:
        Comparable ACROSS positions, unlike raw projections -- which is the whole
        point. A 300-point quarterback and a 250-point running back are not
        directly comparable until both are measured against what you could get
        instead at their own position.

        NaN propagates deliberately. Kickers and defenses have no projections in
        this app, so their VORP is genuinely undefined; inventing a zero would
        make them look exactly replacement-level rather than unknown.
    """
    projections = np.asarray(projections, dtype=float)
    positions = np.asarray(positions)

    baseline = np.array([replacement.get(p, np.nan) for p in positions], dtype=float)
    return projections - baseline


# ---------------------------------------------------------------------------
# Cost of waiting
# ---------------------------------------------------------------------------


def expected_best_at_pick(picks, columns, vorp_values, target_pick) -> float:
    """
    Purpose: Expected VORP of the best player from a group who survives to a pick.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        columns (np.ndarray): Column indices of the group.
        vorp_values (np.ndarray): VORP for those columns, same order.
        target_pick (int): The pick to evaluate at.

    Returns:
        float: Mean over simulations of the best surviving member's VORP.

    Notes:
        The shared core of both cost-of-waiting metrics, and the reason the full
        picks matrix is worth keeping: it is an expectation over a MAXIMUM over
        survivors, which cannot be reconstructed from per-player probabilities.

        A simulation where nobody survives contributes 0.0. That is the correct
        floor rather than a fudge -- VORP is measured against replacement, so
        "you end up with a replacement-level player" is exactly zero by definition.
    """
    survives = picks[:, columns] >= target_pick
    later = np.where(survives, np.asarray(vorp_values, dtype=float)[None, :], -np.inf)
    best = later.max(axis=1)
    return float(np.where(np.isneginf(best), 0.0, best).mean())


def cost_of_waiting(picks, player_idx, my_next_pick, vorp, positions,
                    available_mask=None) -> float:
    """
    Purpose: Expected value surrendered by not taking THIS player now.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        player_idx (int): Column index of the player under consideration.
        my_next_pick (int): Your following pick.
        vorp (np.ndarray): Value over replacement per player.
        positions (np.ndarray): Position per player.
        available_mask (np.ndarray | None): Who is still on the board.

    Returns:
        float: Expected VORP lost, in projected points. 0.0 when he has no
        projection.

    Notes:
        P(gone by my_next_pick) x (his VORP - what you'd actually end up with
        at his position instead).

        THE FALLBACK COMES FROM THE SIMULATION, not from "the best other player
        on the board right now". That distinction matters enormously and an
        earlier version of this got it wrong:

            Pre-draft, every player is nominally available, so Bijan Robinson's
            "best other available RB" was Jahmyr Gibbs -- who is equally certain
            to be gone. The metric returned 0.0 for a 155-VORP back with a 4%
            chance of lasting to your pick, and only the single best-VORP player
            at each position ever scored above zero.

        Asking the simulation "what is the best player at this position who
        actually survives to my next pick" fixes that, and works identically
        pre-draft and mid-draft rather than only being meaningful in one of them.

        This is the "should I spend THIS pick on him" number, and it is what
        stops a raw availability percentage from misleading. A player who is
        barely better than what you'd get anyway costs little to lose, however
        likely losing him is.
    """
    positions = np.asarray(positions)
    vorp = np.asarray(vorp, dtype=float)

    player_vorp = vorp[player_idx]
    if not np.isfinite(player_vorp):
        return 0.0

    p_gone = 1.0 - prob_available_at_pick(picks, player_idx, my_next_pick)

    # The alternatives: everyone else at his position with a usable projection.
    candidates = (positions == positions[player_idx]) & np.isfinite(vorp)
    candidates[player_idx] = False
    if available_mask is not None:
        candidates &= np.asarray(available_mask, dtype=bool)

    columns = np.flatnonzero(candidates)
    fallback = (expected_best_at_pick(picks, columns, vorp[columns], my_next_pick)
                if columns.size else 0.0)

    return float(p_gone * max(player_vorp - fallback, 0.0))


def positional_cost_of_waiting(picks, position, at_pick, my_next_pick, vorp, positions,
                               available_mask=None) -> float:
    """
    Purpose: Expected value surrendered by not addressing a POSITION until your
        next turn.

    Parameters:
        picks (np.ndarray): (n_sims, n_players) matrix.
        position (str): The position being considered.
        at_pick (int): The pick you are making NOW.
        my_next_pick (int): Your following pick.
        vorp (np.ndarray): Value over replacement per player.
        positions (np.ndarray): Position per player.
        available_mask (np.ndarray | None): Who is still on the board. Supply it
            mid-draft, when "best available now" is known rather than predicted.

    Returns:
        float: Expected VORP lost, in projected points. 0.0 if nobody at that
        position has a usable projection.

    Notes:
        E[ best available at at_pick ]  -  E[ best available at my_next_pick ]

        BOTH SIDES COME FROM THE SIMULATION when there is no available_mask. An
        earlier version used the global best VORP at the position as "best
        available now", which is wrong for any pick after the first: evaluating
        round 3, it reported the cost of losing Christian McCaffrey, who is
        certain to be gone by then. You cannot wait on a player you could never
        have had.

        Mid-draft, available_mask makes the first term exact -- you can see who
        is left -- while the second stays a prediction.

        THIS is the number that decides BETWEEN positions, and it is not a sum of
        the per-player costs:

        - It prices the whole tier. Eight interchangeable running backs produce
          near-zero cost even though each individually is probably gone, because
          losing any one of them barely matters. The per-player metric cannot see
          that and would show eight separate alarming numbers.
        - Conversely one elite back with a cliff behind him produces a large cost
          even at only 40% odds of being taken.

        It is an expectation over a MAXIMUM over survivors, which is exactly the
        kind of quantity marginal probabilities cannot reconstruct -- and the
        strongest reason to keep the full picks matrix rather than a summary.

        VORP is held at its pre-draft baseline here. Replacement level does rise
        as a position thins, but that effect is already captured by WHICH players
        survive; re-baselining inside the calculation would double-count it.
    """
    positions = np.asarray(positions)
    vorp = np.asarray(vorp, dtype=float)

    at_position = (positions == position) & np.isfinite(vorp)
    if available_mask is not None:
        at_position &= np.asarray(available_mask, dtype=bool)

    columns = np.flatnonzero(at_position)
    if columns.size == 0:
        return 0.0

    # With a real board, "best available now" is a fact. Without one, it is a
    # prediction, and must come from the simulation like everything else.
    if available_mask is not None:
        best_now = float(vorp[columns].max())
    else:
        best_now = expected_best_at_pick(picks, columns, vorp[columns], at_pick)

    best_later = expected_best_at_pick(picks, columns, vorp[columns], my_next_pick)

    return float(max(best_now - best_later, 0.0))
