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
    """Work out the chance a player is still available at a given pick.

    The most basic availability question, and the building block for most of the
    others here. It is answered by counting simulations rather than by any
    formula.

    Steps:
        1. Take the player's column out of the picks matrix, which holds the pick
           he went at in each simulation.
        2. Compare every entry against the target pick. Going at or after the
           target means he was still there when the target came around.
        3. Average those True/False values, which gives the fraction of
           simulations in which he survived.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        player_idx: The player's column index in that matrix.
        target_pick: The absolute pick number to evaluate at.

    Returns:
        float: A probability between 0 and 1, where 1 means he was still
            available in every simulation.

    Note:
        Undrafted counts as available, which is correct and automatic -- the
        UNDRAFTED sentinel is larger than any real pick number, so `>= target`
        is true for a player nobody took.
    """
    return float((picks[:, player_idx] >= target_pick).mean())


def availability_matrix(picks, target_picks) -> np.ndarray:
    """Compute availability for every player at every pick you own, in one pass.

    The bulk version of `prob_available_at_pick` above. Computing the whole grid
    at once is what lets the Draft Plan page render without running a separate
    query for each row.

    Steps:
        1. For each pick number you own, compare the whole matrix against it and
           average down the simulations, giving one probability per player.
        2. Stack those columns side by side into a single grid.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        target_picks: The pick numbers to evaluate at, normally
            `config.my_picks`.

    Returns:
        np.ndarray: A grid with one row per player and one column per target
            pick, so entry [i, j] is the chance player i survives to
            `target_picks[j]`.

    Note:
        In a snake draft you only ever need availability at your OWN picks --
        about fifteen numbers, not two hundred. Computing the whole grid at once
        is what lets the Draft Plan page render without re-querying per row.
    """
    return np.column_stack([(picks >= k).mean(axis=0) for k in target_picks])


def prob_any_available(picks, player_idxs, target_pick) -> float:
    """Work out the chance AT LEAST ONE of a group is still available at a pick.

    This is the tier question — "will one of these four backs get back to me?" —
    and it is genuinely different from four separate percentages, because the
    players compete for the same picks.

    Steps:
        1. Pull out the columns for the whole group at once.
        2. Take the LARGEST pick number in each simulation's row, which is
           whichever group member lasted longest.
        3. Compare that against the target pick and average across simulations.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        player_idxs: The column indices of the group's members.
        target_pick: The absolute pick number to evaluate at.

    Returns:
        float: A probability between 0 and 1 that at least one member survives.

    Note:
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
    """Work out the chance EVERY member of a group is still available at a pick.

    The "can I still get both of these?" question. Exists partly so the `min`
    form has an honest name and cannot be mistaken for the `any` case, which is
    the mistake `prob_any_available` above warns about.

    Steps:
        1. Pull out the columns for the whole group at once.
        2. Take the SMALLEST pick number in each simulation's row, which is
           whichever group member went earliest.
        3. Compare that against the target pick and average across simulations.
           If even the earliest-drafted member lasted, they all did.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        player_idxs: The column indices of the group's members.
        target_pick: The absolute pick number to evaluate at.

    Returns:
        float: A probability between 0 and 1 that all members survive. Always at
            most `prob_any_available` for the same group, usually far lower.
    """
    return float((picks[:, list(player_idxs)].min(axis=1) >= target_pick).mean())


def simulated_pick_distribution(picks, player_idx) -> np.ndarray:
    """Collect every pick number one player went at, across all simulations.

    Feeds the histogram of a player's range of outcomes, which communicates far
    more than a single ADP number does.

    Steps:
        1. Take the player's column out of the picks matrix.
        2. Keep only the entries below UNDRAFTED, dropping the simulations in
           which he was never taken.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        player_idx: The player's column index in that matrix.

    Returns:
        np.ndarray: The pick numbers he went at, one per simulation in which he
            was drafted, in simulation order rather than sorted. Empty if he was
            never drafted at all.

    Note:
        For plotting a player's range of outcomes, which communicates far more
        than a single ADP number -- two players with identical ADP can have
        completely different shapes.
    """
    column = picks[:, player_idx]
    return column[column < UNDRAFTED]


def pick_percentiles(picks, percentiles=(5.0, 95.0)) -> np.ndarray:
    """Find the best-case and worst-case pick numbers for every player at once.

    A percentile is the value below which a given share of observations fall, so
    the 5th percentile pick is the number he beat in only 5% of drafts. This
    gives each player a realistic range rather than a single number.

    Steps:
        1. Replace every UNDRAFTED entry with NaN so undrafted outcomes are
           excluded rather than counted as a very late pick.
        2. Compute the requested percentiles down each player's column, ignoring
           those NaNs, and silence numpy's all-NaN warning for players never
           drafted.
        3. Transpose the result so players are rows, matching the rest of this
           module.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        percentiles: Which percentiles to take, on a 0-100 scale. The default
            pair reads as "he went this early in his best 5% of drafts, and this
            late in his worst 5%".

    Returns:
        np.ndarray: A grid with one row per player and one column per requested
            percentile, so column j holds `percentiles[j]` for every player. NaN
            for anyone never drafted.

    Note:
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
    """Replay ONE simulated draft in the order the picks were actually made.

    Turns a stored simulation back into something that reads like a draft board,
    which is what the sim viewer page displays.

    Steps:
        1. Take the row for the requested simulation, which holds one pick number
           per player.
        2. Find the positions of every player who was actually drafted, dropping
           the undrafted ones.
        3. Sort those positions by the pick number they hold. A stable sort keeps
           the original relative order for any ties, though ties should not occur.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        sim_index: Which simulation to replay, counting from 0.

    Returns:
        tuple: `(pick_numbers, columns)`, two arrays of the same length sorted by
            pick number. `pick_numbers` holds the absolute pick numbers, 1-indexed
            so 1 is the first overall selection. `columns` holds the
            picks-matrix column of the player taken at each of those picks.

    Note:
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
    """Find the projected points of the last startable player at each position.

    "Replacement level" is the score you could get for free at a position — the
    worst player who would still be in someone's starting lineup. It is the
    baseline every player's value is measured against.

    Steps:
        1. Convert the inputs to arrays and work out which players are usable:
           those with a real projection, narrowed to the available ones if a mask
           was supplied.
        2. Handle the flex-eligible positions as one pool. Count how many players
           the whole league starts across RB, WR, TE, and FLEX.
        3. Rank every usable flex-eligible player by projection and take that many
           off the top as the startable set.
        4. For each of RB, WR, and TE, the replacement level is the worst
           startable player at that position. Which positions fill the flex slots
           therefore falls out of the projections rather than a fixed assumption.
        5. Handle the remaining positions separately: rank the usable players at
           each, take the top `num_teams * starters`, and use the worst of those.

    Args:
        projections: Projected season points per player. NaN is allowed and marks
            a player with no projection.
        positions: The position name for each player.
        starting_slots: Maps a position to how many each team starts, for example
            `{"QB": 1, "RB": 2, ...}`. A "FLEX" key is drawn from FLEX_POSITIONS.
        num_teams: League size.
        available_mask: True for players still on the board. Omit for the
            pre-draft baseline.

    Returns:
        dict: Maps a position to its replacement points. A position with no
            projected players — K and DST, which have no projections at all in
            this app — is absent rather than present with a fake number, so
            callers must use `.get` rather than square brackets.

    Note:
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


def positional_cliffs(positions, projections, window=8, minimum_players=2):
    """Find where each position's next big drop in value is, and how far away.

    Turns scarcity into something countable. Instead of "running back is thinning"
    you get "3 running backs left before a 46-point drop", which you can weigh
    directly against how many picks happen before your turn.

    Deliberately reads the PROJECTIONS rather than the simulation. Every
    simulation-based number in this app follows ADP -- the market's opinion of
    when a player goes -- so a genuine drop in value that the market has not yet
    priced in is invisible to all of them. This is the one thing that sees it.

    Steps:
        1. Group the players by position, ignoring anyone without a projection.
        2. Within each position, sort best first and keep the top `window`. The
           cap matters -- see the note.
        3. Measure the drop between each player and the next one down.
        4. Take the largest of those drops as the cliff.
        5. Work out how that drop compares to the typical step at that position,
           so a normal gap in a thin position is not mistaken for a cliff.
        6. Sort so the most urgent position comes first: fewest players left,
           then biggest drop.

    Args:
        positions: The position name for each available player.
        projections: Projected season points for each available player. NaN is
            allowed and those players are skipped, which is what excludes K and
            DST automatically.
        window: How many players deep to look at each position. Beyond this the
            drops are real but irrelevant -- nobody is deciding between the 40th
            and 41st receiver.
        minimum_players: Skip a position with fewer than this many available.
            With one player there is no gap to measure.

    Returns:
        list: One dictionary per position, most urgent first, each with
            `position`, `players_before` (how many are left before the drop),
            `drop` (its size in projected points), `steepness` (the drop divided
            by the typical step at that position), and `before`/`after` (the
            projections either side of it). Empty if no position has enough
            players left.

            A plain list rather than a DataFrame so this module stays free of
            pandas, as every other function here is. The caller builds a frame
            if it wants one.

    Note:
        THE WINDOW IS WHAT MAKES THIS USEFUL. Over a whole position the biggest
        drop is often somewhere in the eighties, between two players nobody will
        draft. Capping the search to the top few keeps the answer about players
        actually under consideration.

        `steepness` exists because a "cliff" of two points is not a cliff. A
        value near 1 means the drop is just the position's normal step; a value
        of 3 means it is three times the usual gap and worth acting on. Reporting
        the raw drop alone would call every position a cliff.
    """
    positions = np.asarray(positions)
    projections = np.asarray(projections, dtype=float)

    rows = []
    usable = np.isfinite(projections)
    for position in sorted(set(positions[usable])):
        values = projections[usable & (positions == position)]
        if values.size < minimum_players:
            continue

        top = np.sort(values)[::-1][:window]
        gaps = -np.diff(top)                       # positive: each drop to the next
        if not gaps.size:
            continue

        biggest = int(np.argmax(gaps))
        typical = float(np.median(gaps))
        rows.append({
            "position": position,
            "players_before": biggest + 1,          # how many sit above the drop
            "drop": float(gaps[biggest]),
            # Guard the divide: a position where every gap is identical has a
            # median of zero, and there is no cliff to report there anyway.
            "steepness": float(gaps[biggest] / typical) if typical > 0 else 1.0,
            "before": float(top[biggest]),
            "after": float(top[biggest + 1]),
        })

    # Most urgent first: fewest players left, and among equals the bigger drop.
    # Negating the drop turns "descending" into a plain ascending sort key.
    rows.sort(key=lambda row: (row["players_before"], -row["drop"]))
    return rows


def compute_vorp(projections, positions, replacement) -> np.ndarray:
    """Convert raw projections into value over replacement, or VORP.

    VORP is how many points a player is worth ABOVE the freely available
    alternative at his position. That subtraction is what makes players at
    different positions comparable.

    Steps:
        1. Convert the projections and positions to arrays.
        2. Build a baseline array by looking up each player's position in the
           replacement dictionary, using NaN for a position with no baseline.
        3. Subtract the baseline from the projection. NaN on either side gives
           NaN, which is the wanted behavior.

    Args:
        projections: Projected season points per player.
        positions: The position name for each player.
        replacement: The dictionary produced by `replacement_value` above.

    Returns:
        np.ndarray: One VORP value per player. NaN where the player has no
            projection or his position has no replacement baseline.

    Note:
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


def best_available_by_position(picks, target_pick, vorp, positions,
                               available_mask=None) -> dict:
    """Find the best value expected to survive at EACH position, at a future pick.

    Answers "if I skip this position now, what is the best I can realistically
    get at it next time?" -- one number per position. That is the baseline a
    player is worth comparing against when deciding whether to spend this pick on
    him.

    Steps:
        1. Narrow to players with a usable value, and to those still on the board
           if a mask was supplied.
        2. For each position present, gather its columns.
        3. Ask `expected_best_at_pick` below what the best survivor at that
           position is worth on average at the target pick.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        target_pick: The pick to look ahead to -- normally your next turn.
        vorp: Value over replacement per player. NaN where undefined.
        positions: The position name for each player.
        available_mask: True for players still on the board. Omit pre-draft.

    Returns:
        dict: Maps a position to the average value of the best player at it who
            survives to `target_pick`. A position with nobody left is absent, so
            callers should use `.get` rather than square brackets.

    Note:
        The player being compared IS included in his own position's pool, and
        that is deliberate. The question is "what is the best available at this
        position next round", and if he lasts, he is the answer -- which
        correctly makes his cost of waiting near zero. It also means the baseline
        is the same for every player at a position, so this is one calculation
        per position rather than one per player.
    """
    vorp = np.asarray(vorp, dtype=float)
    positions = np.asarray(positions)

    usable = np.isfinite(vorp)
    if available_mask is not None:
        usable = usable & np.asarray(available_mask, dtype=bool)

    best = {}
    for position in np.unique(positions[usable]):
        columns = np.flatnonzero(usable & (positions == position))
        if columns.size:
            best[position] = expected_best_at_pick(picks, columns, vorp[columns],
                                                   target_pick)
    return best


def expected_best_at_pick(picks, columns, vorp_values, target_pick) -> float:
    """Find the average VORP of the best group member who survives to a pick.

    In plain terms: "if I wait until this pick, how good is the best player from
    this group I can still expect to get?" This is the shared core of both
    cost-of-waiting metrics below.

    Steps:
        1. Work out, for each simulation, which group members were still
           available at the target pick.
        2. Build a grid holding each survivor's VORP and negative infinity for
           everyone already taken, so the taken ones can never win a maximum.
        3. Take the best value in each simulation's row.
        4. Replace any row that stayed at negative infinity — meaning nobody
           survived — with 0.0, then average across simulations.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        columns: The column indices of the group's members.
        vorp_values: The VORP for those columns, in the same order.
        target_pick: The pick number to evaluate at.

    Returns:
        float: The average across simulations of the best surviving member's
            VORP, in projected points.

    Note:
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
    """Work out the expected value you give up by NOT taking this player now.

    The "should I spend this pick on him" number. It combines how likely he is to
    be gone with how much worse off you would actually be, which is what stops a
    scary-looking availability percentage from misleading you.

    Steps:
        1. Convert the positions and VORP inputs to arrays.
        2. If the player has no usable projection, return 0.0 — there is nothing
           to measure.
        3. Compute the chance he is gone by your next pick, using
           `prob_available_at_pick` above and subtracting from 1.
        4. Build the set of alternatives: everyone else at his position with a
           usable projection, narrowed to the available ones if a mask was given.
        5. Ask `expected_best_at_pick` above what the best of those alternatives
           who actually survives to your next pick is worth, or 0.0 if there are
           no alternatives at all.
        6. Multiply the chance he is gone by how much better he is than that
           fallback, with a floor of 0 so a worse-than-fallback player never
           reports a negative cost.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        player_idx: The column index of the player under consideration.
        my_next_pick: The pick number of your following turn.
        vorp: Value over replacement per player, from `compute_vorp` above.
        positions: The position name for each player.
        available_mask: True for players still on the board. Omit pre-draft.

    Returns:
        float: The expected VORP lost, in projected points. 0.0 when he has no
            projection, or when losing him costs nothing.

    Note:
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
    """Work out the expected value you give up by waiting on a whole POSITION.

    This is the number that decides BETWEEN positions, and it is not the sum of
    the per-player costs — it prices the entire tier at once, which is something
    individual probabilities cannot do.

    Steps:
        1. Convert the positions and VORP inputs to arrays.
        2. Gather everyone at this position with a usable projection, narrowed to
           the available ones if a mask was given.
        3. If nobody qualifies, return 0.0.
        4. Work out the best value available NOW. With a real board that is a
           fact, so take the maximum directly. Without one it is a prediction, so
           ask `expected_best_at_pick` above.
        5. Ask `expected_best_at_pick` again for the value expected to survive to
           your NEXT pick.
        6. Return the drop between the two, floored at 0 so waiting never appears
           to gain you value.

    Args:
        picks: The (n_sims, n_players) results matrix from the simulator.
        position: The position being considered, such as "RB".
        at_pick: The pick you are making NOW.
        my_next_pick: The pick number of your following turn.
        vorp: Value over replacement per player, from `compute_vorp` above.
        positions: The position name for each player.
        available_mask: True for players still on the board. Supply it mid-draft,
            when "best available now" is known rather than predicted.

    Returns:
        float: The expected VORP lost, in projected points. 0.0 if nobody at that
            position has a usable projection.

    Note:
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


# ---------------------------------------------------------------------------
# Rosters: turning a picks matrix into "what does this team end up with?"
# ---------------------------------------------------------------------------


def roster_from_picks(picks, owned_picks) -> np.ndarray:
    """Work out which players a team ends up with, in every simulation at once.

    Every question about how a team's season is shaping up -- how strong their
    starters are, where they are thin, whether they still need a tight end --
    starts here. The simulator says WHERE each player went; this says WHO that
    means each team got.

    Steps:
        1. Handle the case of a team with no picks left, which happens at the end
           of a draft, by returning an all-False mask rather than an error.
        2. Compare the whole picks matrix against the team's pick numbers at
           once. `np.isin` marks every cell whose value is one of those numbers,
           which is exactly "this player went to this team".

    Args:
        picks: The (n_sims, n_players) matrix from the simulator. Each cell holds
            the pick number a player went at in that simulation, or `UNDRAFTED`
            if he was never taken.
        owned_picks: The absolute pick numbers this team owns, such as
            (4, 21, 28, ...). Get them from `DraftConfig.my_picks` for yourself
            or `picks_for_slot` in draft_model/mechanics.py for anyone else.

    Returns:
        np.ndarray: An (n_sims, n_players) array of True/False. True means "in
            that simulation, this team got this player".

    Note:
        THIS ONLY COVERS PICKS THE SIMULATION MADE. A live re-simulation starts
        at the current pick, so players already taken sit at `UNDRAFTED` in the
        matrix and show up here as False for everybody. To get a team's FULL
        final roster mid-draft you must OR this together with the players they
        already hold -- which is what `projected_roster` in
        services/draft_runner_service.py does.

        A player nobody took holds `UNDRAFTED` (999), which sits above any
        realistic draft -- the biggest this app is used for is 12 teams by 15
        rounds, or 180 picks -- so he matches nobody's pick numbers and lands on
        no roster. Nothing actually ENFORCES that: `DraftConfig` bounds league
        size from below but not above, so a hypothetical 999-pick draft would
        confuse a real pick with "undrafted". That assumption is shared by every
        function in this module that compares against `UNDRAFTED`, and is called
        out here only because this one turns it into a roster.
    """
    picks = np.asarray(picks)
    owned = np.asarray(tuple(owned_picks), dtype=picks.dtype)
    if owned.size == 0:
        return np.zeros(picks.shape, dtype=bool)
    return np.isin(picks, owned)


def lineup_slot_masks(roster, projections, positions, starting_slots,
                      flex_positions=FLEX_POSITIONS) -> dict:
    """Fill each team's lineup slot by slot, and say WHO filled WHICH slot.

    The detailed version of `starting_lineup_mask` below. Keeping the slots
    apart is what lets a caller ask "how strong is my FLEX" rather than only
    "how strong is my lineup" -- and the flex in particular cannot be recovered
    afterwards, since a running back in the flex looks identical to one in RB2
    once the masks are merged.

    Steps:
        1. Score every player, treating a missing projection as very low so a
           player we cannot value never displaces one we can. Players not on the
           roster are pushed to negative infinity so they can never be chosen.
        2. Fill the dedicated slots first (QB, RB, WR, TE, K, DST), taking the
           top few at each position with `_take_best`, the helper below.
        3. Fill the FLEX from whoever is LEFT at the flex-eligible positions.
           Doing it last is what makes it a flex: the dedicated starters are
           already spoken for, so it takes the best remaining rather than
           competing for the same player.

    Args:
        roster: The (n_sims, n_players) True/False mask from `roster_from_picks`
            above -- who this team has.
        projections: Projected fantasy points per player. NaN is allowed, and is
            what kickers and defenses have in this app.
        positions: The position name for each player.
        starting_slots: How many start at each position, such as
            `{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}`.
        flex_positions: Which positions may fill a FLEX slot. Defaults to running
            back, receiver and tight end.

    Returns:
        dict: Maps each slot name to its own (n_sims, n_players) True/False
            mask. Slots with a count of zero are absent, so use `.get`.

    Note:
        The masks never overlap: a player fills at most one slot. That is what
        makes it safe to add their points up across slots without
        double-counting anyone.
    """
    roster = np.asarray(roster, dtype=bool)
    positions = np.asarray(positions)

    # NaN would sort unpredictably, so a player without a projection is scored
    # far below any real one. He stays SELECTABLE -- a kicker is still startable,
    # and any kicker is as good as any other by the data we have.
    scores = np.nan_to_num(np.asarray(projections, dtype=float), nan=-1e9)

    filled = np.zeros(roster.shape, dtype=bool)
    masks = {}

    for slot, count in starting_slots.items():
        if slot == "FLEX" or count <= 0:
            continue
        chosen = np.zeros(roster.shape, dtype=bool)
        _take_best(chosen, roster & (positions == slot), scores, count)
        masks[slot] = chosen
        filled |= chosen

    flex_count = starting_slots.get("FLEX", 0)
    if flex_count > 0:
        # `& ~filled` is the whole trick: the dedicated starters are already
        # marked, so what is left here is genuinely the leftovers.
        chosen = np.zeros(roster.shape, dtype=bool)
        eligible = roster & ~filled & np.isin(positions, flex_positions)
        _take_best(chosen, eligible, scores, flex_count)
        masks["FLEX"] = chosen

    return masks


def starting_lineup_mask(roster, projections, positions, starting_slots,
                         flex_positions=FLEX_POSITIONS) -> np.ndarray:
    """Pick each team's best starting lineup out of its roster, in every simulation.

    A roster is not a lineup. Six good receivers only score for you twice over,
    so measuring a team by its whole roster overrates hoarding one position. This
    fills the lineup the way you would on a Sunday -- best available into each
    slot -- so what comes out is what the team actually scores.

    Steps:
        1. Call `lineup_slot_masks` above to fill the lineup slot by slot.
        2. Merge every slot's mask into one, since callers who want a single
           "is he starting?" answer do not care which slot he filled.

    Args:
        roster: The (n_sims, n_players) True/False mask from `roster_from_picks`
            above -- who this team has.
        projections: Projected fantasy points per player. NaN allowed.
        positions: The position name for each player.
        starting_slots: How many start at each position. Read it from
            `DraftConfig.starting_slots`.
        flex_positions: Which positions may fill a FLEX slot.

    Returns:
        np.ndarray: An (n_sims, n_players) array of True/False. True means "this
            player is in that team's starting lineup in that simulation". Anyone
            on the roster but not marked here is on the bench.

    Note:
        A slot goes UNFILLED rather than borrowing from elsewhere if the team has
        nobody at that position. That is the honest answer -- a team with no
        tight end really does start one fewer player -- and it is what makes a
        hole show up as weakness instead of being quietly papered over.
    """
    masks = lineup_slot_masks(roster, projections, positions, starting_slots,
                              flex_positions)
    starters = np.zeros(np.shape(roster), dtype=bool)
    for mask in masks.values():
        starters |= mask
    return starters


def _take_best(starters, eligible, scores, count):
    """Mark the highest-scoring few of an eligible group, in every simulation.

    The shared body of the slot-filling loop above. Written as one array
    operation across all simulations rather than a loop over them, because at a
    thousand simulations a per-simulation loop is the difference between a panel
    that redraws instantly and one you wait on.

    Steps:
        1. Score the eligible players, pushing everyone else to negative infinity
           so they sort to the back and can never be selected.
        2. Sort each simulation's row by score, best first, and keep the first
           `count` columns -- that is the top few per simulation.
        3. Drop any that are not actually eligible. This matters when a team has
           FEWER players at a position than there are slots: the sort still
           returns `count` columns, and the extras are negative infinity
           placeholders that must not be marked as starters.
        4. Write the survivors into the starters mask, in place.

    Args:
        starters: The (n_sims, n_players) mask being filled. MODIFIED IN PLACE.
        eligible: A same-shaped mask of who may fill this slot.
        scores: Projected points per player, with NaN already replaced.
        count: How many slots to fill.

    Returns:
        None. The work is the in-place edit to `starters`.
    """
    values = np.where(eligible, scores, -np.inf)

    # Negating turns numpy's ascending sort into a descending one.
    chosen = np.argsort(-values, axis=1)[:, :count]

    rows = np.repeat(np.arange(values.shape[0]), chosen.shape[1])
    columns = chosen.ravel()
    real = np.isfinite(values[rows, columns])
    starters[rows[real], columns[real]] = True


def lineup_points(starters, projections, positions, exclude=("K", "DST")) -> np.ndarray:
    """Add up what a starting lineup is projected to score, in every simulation.

    The headline number for "how good is this team". Comes out as one value per
    simulation, so the caller can take an average for the typical case or
    percentiles for the range.

    Steps:
        1. Blank out the positions being excluded, so they contribute nothing.
        2. Treat a missing projection as zero -- otherwise one unprojected player
           would turn the whole team's total into NaN.
        3. Add up the projections of the marked starters, one total per
           simulation.

    Args:
        starters: The (n_sims, n_players) mask from `starting_lineup_mask` above.
        projections: Projected fantasy points per player. NaN allowed.
        positions: The position name for each player.
        exclude: Positions to leave out of the total. Defaults to kickers and
            defenses, because neither is projected in this app and neither is
            something you draft on purpose early -- counting them would add noise
            to a number that is meant to compare draft decisions. Pass an empty
            tuple to count everything.

    Returns:
        np.ndarray: One total per simulation, shape (n_sims,).
    """
    starters = np.asarray(starters, dtype=bool)
    values = np.nan_to_num(np.asarray(projections, dtype=float), nan=0.0)

    if exclude:
        counted = ~np.isin(np.asarray(positions), tuple(exclude))
        starters = starters & counted

    return (starters * values).sum(axis=1)


def adjust_within_position(values, projections, positions, minimum_players=6):
    """Strip out the part of a rating that merely restates the projection.

    Raw upside is almost a copy of the projection -- at running back the two
    agree 93% of the time -- so a column showing it would be a second, blurrier
    `Proj`. This removes the overlap and keeps only what is genuinely new: how a
    player compares to OTHER PLAYERS PROJECTED LIKE HIM.

    Read the result as: positive upside means more explosive than his projection
    suggests; positive risk means shakier than his projection suggests.

    Steps:
        1. Start everything at NaN, so a player who cannot be adjusted stays
           visibly unrated rather than quietly scoring zero.
        2. For each position, take the players with both a rating and a
           projection.
        3. Fit the straight line that best relates projection to rating, and keep
           only what the line does not explain -- the leftover, or "residual".
        4. Fall back to just centring on the average when there is too little to
           fit a line from, or when every projection at that position is the same.

    Args:
        values: The raw rating per player, such as UDK's risk or upside. NaN
            where a player has no rating, which is every kicker and defense.
        projections: Projected fantasy points per player. NaN allowed.
        positions: The position name for each player.
        minimum_players: How many rated players a position needs before a line is
            fitted rather than a simple average subtracted. Six is enough to keep
            a handful of points from setting the slope.

    Returns:
        np.ndarray: The adjusted rating per player, centred on zero WITHIN each
            position. NaN where the player had no rating to adjust.

    Note:
        CENTRED PER POSITION, WHICH MAKES THE POSITIONS COMPARABLE. Zero means
        "typical for his position", so a tight end and a receiver at +1.5 are
        making the same claim. Comparing raw ratings across positions does not
        work, because the vendor grades each position on its own scale.

        Fit this over the WHOLE player pool, not just who is left on the board.
        A player's rating should not drift as other people get drafted, and it
        would if the line were refitted against a shrinking pool.
    """
    values = np.asarray(values, dtype=float)
    projections = np.asarray(projections, dtype=float)
    positions = np.asarray(positions)

    adjusted = np.full(values.shape, np.nan)
    usable = np.isfinite(values) & np.isfinite(projections)

    for position in np.unique(positions[usable]):
        rows = np.flatnonzero(usable & (positions == position))
        if rows.size < 2:
            continue

        rating = values[rows]
        projection = projections[rows]

        # Centring both first is what lets the line be described by a slope
        # alone -- a line through the origin, in the recentred numbers.
        spread = projection - projection.mean()
        centred = rating - rating.mean()

        denominator = (spread * spread).sum()
        if rows.size < minimum_players or denominator <= 0:
            # Not enough to trust a slope from, so remove the position's average
            # level and leave it there. Still comparable across positions, just
            # not cleaned of the projection relationship.
            adjusted[rows] = centred
            continue

        slope = (spread * centred).sum() / denominator
        adjusted[rows] = centred - slope * spread

    return adjusted
