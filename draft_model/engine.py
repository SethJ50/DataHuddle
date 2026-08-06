"""The simulator: plays out thousands of fake drafts and records where everyone went.

HOW THIS IS FAST, AND WHY IT MATTERS
------------------------------------
The obvious implementation simulates one draft as a Python loop over ~180 picks,
then runs that loop 10,000 times. That is 1.8 million iterations of interpreted
Python, and it is slow enough that people start trading away correctness to
rescue it -- pre-sorting boards and walking pointers, or only considering the
next ~15 players instead of the whole pool.

None of that is necessary, because of one observation:

    Snake order is deterministic. At pick 47, team 3 is on the clock in EVERY
    simulation.

So all simulations can advance one pick TOGETHER, as a single numpy operation.
The loop runs 180 times, not 1.8 million, and each step is vectorized across the
whole batch. Same arithmetic, roughly a thousand times faster, and no accuracy
compromises anywhere.

BOARDS ARE DRAWN ONCE PER DRAFT, NEVER PER PICK
-----------------------------------------------
Each simulated manager forms an opinion of every player at the start of a draft
and keeps it. Re-drawing mid-draft would make each pick forget what the last one
believed, so a player could be nearly taken at pick 10 and inexplicably survive
to pick 60. Drawing up front gives each simulated draft a coherent personality.

See draft_model/DESIGN.md sections 3.1 and 3.2.
"""

import numpy as np

from draft_model.config import (
    BLOCK, HARD_LIMIT, NEED_BONUS, POSITIONS, RHO, STARTER_DEADLINE, UNDRAFTED,
)
from draft_model.mechanics import snake_order

# ---------------------------------------------------------------------------
# Turning positions into numbers
# ---------------------------------------------------------------------------

def position_index(positions) -> np.ndarray:
    """Convert position names into the small integers the simulator works with.

    numpy has no notion of a "QB", so every position becomes its index in
    config.POSITIONS. That lets each per-position constant become an array the
    simulation can look up with the same numbers.

    Steps:
        1. Build a lookup from each position name to its slot in POSITIONS.
        2. Collect any position not in that lookup, sorted so the error message
           is stable.
        3. If any were found, raise rather than continuing.
        4. Otherwise translate each position to its number and pack them into an
           int8 array, the smallest type that fits six positions.

    Args:
        positions: One position name per player, in table row order. The order
            matters: the result lines up with the player table row for row.

    Returns:
        np.ndarray: One int8 per player, each an index into config.POSITIONS.

    Raises:
        ValueError: If a position isn't recognized. Failing here is deliberate --
            an unknown position silently mapped to 0 would make every kicker
            behave like a quarterback, which no test would obviously catch.
    """
    lookup = {name: i for i, name in enumerate(POSITIONS)}
    unknown = sorted({p for p in positions if p not in lookup})
    if unknown:
        raise ValueError(f"unknown positions {unknown}; expected some of {POSITIONS}")
    return np.array([lookup[p] for p in positions], dtype=np.int8)

def position_limit_arrays():
    """Repackage the per-position roster rules as arrays the pick loop can index.

    The simulation identifies positions by number, so the constants that are
    written as dictionaries in config.py have to be converted into arrays lined
    up with POSITIONS before the loop starts.

    Steps:
        1. Walk POSITIONS in order and read each position's HARD_LIMIT, using 99
           for anything not listed, which means "effectively no limit".
        2. Do the same for STARTER_DEADLINE, using 9999 as the never-reached
           default.
        3. Return both arrays.

    Returns:
        tuple: Two int arrays, `(hard_limit, deadline)`, each with one entry per
            position in config.POSITIONS order, so `hard_limit[2]` is the limit
            for whatever position sits at index 2.

    Note:
        Built once per run rather than inside the pick loop -- a dict lookup per
        player per pick per simulation would undo the whole point of vectorizing.
    """
    hard_limit = np.array([HARD_LIMIT.get(p, 99) for p in POSITIONS], dtype=np.int16)
    deadline = np.array([STARTER_DEADLINE.get(p, 9999) for p in POSITIONS], dtype=np.int16)
    return hard_limit, deadline

# ---------------------------------------------------------------------------
# Drawing manager opinions
# ---------------------------------------------------------------------------

def draw_boards(mu, sd, num_teams, rng, n_sims=1, rho=RHO) -> np.ndarray:
    """Draw each simulated manager's private opinion of every player.

    Managers do not all rank players identically, and that disagreement is what
    makes a player's draft position uncertain. Each manager gets a random draw
    around the player's calibrated centre, and these opinions are fixed for the
    whole draft.

    Steps:
        1. Convert the centre and width inputs to float arrays and note how many
           players there are.
        2. Draw one "shared shock" per player per draft — the surprise everyone
           in that draft reacts to together.
        3. Draw one private deviation per player per manager.
        4. Blend the two using rho, with coefficients chosen so the total spread
           stays exactly `sd` whatever rho is (see the note below).
        5. Scale by `sd`, shift by `mu`, and convert to float32 to halve the
           memory this uses.

    Args:
        mu: The calibrated centre for each player, one entry per player.
        sd: The calibrated width for each player, one entry per player.
        num_teams: How many independent boards to draw per draft.
        rng: A seeded numpy random generator, passed in so a whole run shares one
            reproducible stream of numbers.
        n_sims: How many drafts are in this batch.
        rho: How much managers agree WITHIN a draft. 0 means fully independent
            opinions, 1 means identical boards.

    Returns:
        np.ndarray: A float32 array shaped (n_sims, n_players, num_teams), so
            entry [s, i, t] is what manager t in simulation s thinks of player i.
            LOWER IS BETTER — these behave like pick numbers, so a manager's
            favourite player has his smallest value.

    Note:
        These are BOARD VALUES, not pick numbers. A player's actual draft position
        emerges from the mechanics and is systematically earlier than his drawn
        value, because he goes to whichever of twelve managers rates him highest.
        Correcting for that is calibration's whole job.

        THE SHARED SHOCK: each player gets one draft-wide surprise plus a private
        deviation per manager:

            value = mu + sd * (rho * shared + sqrt(1 - rho^2) * private)

        The coefficients are chosen so the total variance stays exactly sd^2 for
        ANY rho -- so each manager's marginal opinion is always N(mu, sd), and rho
        only changes how much they agree with each other. rho = 0 reproduces the
        fully independent model exactly, which makes this a strict generalization.
    """
    mu = np.asarray(mu, dtype=np.float64)
    sd = np.asarray(sd, dtype=np.float64)
    n_players = mu.shape[0]

    shared = rng.standard_normal((n_sims, n_players, 1))          # one per player per draft
    private = rng.standard_normal((n_sims, n_players, num_teams))  # one per manager

    combined = rho * shared + np.sqrt(1.0 - rho ** 2) * private
    boards = mu[None, :, None] + sd[None, :, None] * combined
    return boards.astype(np.float32)

# ---------------------------------------------------------------------------
# The simulation itself
# ---------------------------------------------------------------------------

def sim_batch(boards, pos_index, config, *, start_pick=1, end_pick=None,
              already_drafted=None, roster_counts=None, keeper_picks=None) -> np.ndarray:
    """Run a whole batch of drafts at once and record where each player went.

    The heart of the simulator. Because snake order is deterministic, the same
    team is on the clock at pick 47 in every simulation, so all simulations can
    advance one pick together as a single numpy operation.

    Steps:
        1. Read the batch shape from the boards, default the end pick to the full
           draft, and fetch the per-position limit arrays via
           `position_limit_arrays` above.
        2. Set up the state: a "taken" flag per player per simulation, seeded
           with any already-drafted players; a roster count per team per
           position; and a picks matrix filled with UNDRAFTED.
        3. Mark every kept player taken before pick 1, so no simulated manager
           can select someone another team is keeping.
        4. Check the pool is big enough for the picks requested, and raise if not
           — see Raises for why this cannot be left to fail on its own.
        5. For each pick in range, ask `snake_order` from draft_model/mechanics.py
           which team is on the clock.
        6. If a keeper occupies this pick, record him as going here and bump his
           team's roster count, then move on. No selection is made: the pick is
           spent on the keeper, which is the whole point of keeping him.
        7. Otherwise copy that team's opinion of every player, in every
           simulation at once.
        8. Look up how many players that team already holds at each player's
           position.
        9. Add BLOCK where the position is full, and subtract NEED_BONUS where
           the team has none and the deadline has passed. This is applied at read
           time and never written back into the board.
       10. Set already-taken players to infinity so they can never be chosen.
       11. Take the smallest value in each simulation's row with `argmin`, which
           is every simulation's pick resolved in one call.
       12. Record the pick number, mark the player taken, and bump the roster
           count.

    Args:
        boards: The manager opinions, shaped (n_sims, n_players, num_teams),
            from `draw_boards` above. Taken as an argument rather than drawn here
            so tests can hand the same boards to the slow reference
            implementation and demand identical drafts.
        pos_index: One position number per player, from `position_index` above.
        config: The league settings.
        start_pick: The first pick to simulate, 1-indexed.
        end_pick: The last pick, inclusive. Defaults to the full draft. Stopping
            early is what makes the live in-draft tool fast enough.
        already_drafted: Which players are off the board before this starts,
            either one flag per player or a full (n_sims, n_players) grid for
            per-simulation state. Use for keepers, or to resume a real draft in
            progress.
        roster_counts: How many players each team already holds at each position,
            shaped (n_sims, num_teams, n_positions). Seed this from a real draft
            log when resuming; defaults to everyone holding nothing.
        keeper_picks: Which picks are spent on keepers rather than used to
            select, as a mapping from an overall pick number to the COLUMN INDEX
            of the player kept there. Resolving a player id to a column is the
            caller's job, exactly as it is everywhere else in this module.
            Defaults to no keepers.

    Returns:
        np.ndarray: An int16 array shaped (n_sims, n_players). Entry [s, i] is
            the pick number player i went at in simulation s, or UNDRAFTED (999)
            if he was never taken. A kept player carries the pick his team spent
            on him, so every pick number in the draft still appears exactly once
            and the counting checks in draft_model/calibrate.py keep working
            unchanged.

    Raises:
        ValueError: If the pool is too small to fill the requested picks. That
            situation would otherwise silently corrupt results -- argmin over an
            all-infinite row returns index 0, quietly "drafting" someone already
            taken.

    Note:
        Step 7 applies need AT READ TIME and never writes it back into the board.
        Written back, the adjustments would compound over sixteen rounds into
        nonsense.

        WHY BLOCK IS FINITE AND `taken` IS INFINITE: a full position gets +BLOCK,
        which pushes a player below every realistic alternative but still leaves
        him selectable if literally everything else is blocked. Already-drafted
        players get +inf, because taking them twice is never acceptable. If BLOCK
        were infinite too, a fully-rostered team could deadlock the draft.

        POSITIONAL RUNS EMERGE from step 7; they are not programmed anywhere. Once
        two managers take tight ends, the remaining TE-less managers start
        applying NEED_BONUS and reaching, and the clustering appears on its own.
    """
    n_sims, n_players, num_teams = boards.shape
    end_pick = config.total_picks if end_pick is None else end_pick
    hard_limit, deadline = position_limit_arrays()
    keeper_picks = dict(keeper_picks or {})

    # --- state, one row per simulation ---
    taken = np.zeros((n_sims, n_players), dtype=bool)
    if already_drafted is not None:
        taken |= np.asarray(already_drafted, dtype=bool)

    # A kept player is off the board from the very first pick. Marking him here
    # rather than only at his own keeper pick is what stops another manager
    # taking someone who was never actually available.
    for keeper_column in keeper_picks.values():
        taken[:, keeper_column] = True

    counts = (np.zeros((n_sims, num_teams, len(POSITIONS)), dtype=np.int16)
              if roster_counts is None
              else np.asarray(roster_counts, dtype=np.int16).copy())

    picks = np.full((n_sims, n_players), UNDRAFTED, dtype=np.int16)

    # Guard the one failure mode that corrupts silently rather than crashing.
    # Picks spent on keepers make no selection, so they need no player: counting
    # them here would demand a bigger pool than the draft actually consumes.
    reserved = sum(1 for pick in keeper_picks if start_pick <= pick <= end_pick)
    needed = (end_pick - start_pick + 1) - reserved
    available = n_players - int(taken[0].sum())
    if available < needed:
        raise ValueError(
            f"pool too small: {available} players available but {needed} picks to make"
        )

    rows = np.arange(n_sims)

    for pick in range(start_pick, end_pick + 1):
        team = snake_order(pick, config.num_teams, config.third_round_reversal)

        # A pick spent on a keeper: the player is fixed, so there is nothing to
        # choose. He is already flagged taken, so only the pick number and the
        # roster count need recording.
        keeper_column = keeper_picks.get(pick)
        if keeper_column is not None:
            picks[:, keeper_column] = pick
            counts[:, team, pos_index[keeper_column]] += 1
            continue

        # This team's opinion of every player, in every simulation.
        values = boards[:, :, team].astype(np.float32, copy=True)

        # How many at each player's position this team already holds: (n_sims, n_players).
        have = counts[:, team, :][:, pos_index]

        # Roster full at that position -> effectively unpickable.
        values += BLOCK * (have >= hard_limit[pos_index])

        # No starter there yet and it's getting late -> reach for one.
        values -= NEED_BONUS * ((have == 0) & (pick > deadline[pos_index]))

        # Already gone -> never selectable.
        values[taken] = np.inf

        choice = values.argmin(axis=1)

        picks[rows, choice] = pick
        taken[rows, choice] = True
        counts[rows, team, pos_index[choice]] += 1

    return picks

def draw_boards_for_sims(mu, sd, num_teams, seed, sim_indices, rho=RHO) -> np.ndarray:
    """Draw boards for specific simulations, unaffected by how they are batched.

    Like `draw_boards` above, but each simulation gets its own private stream of
    random numbers derived from its absolute index. That makes the result depend
    only on the seed and the simulation number, never on batch size.

    Steps:
        1. Convert the centre and width inputs to float arrays and precompute the
           private-deviation coefficient, which is the same for every simulation.
        2. Allocate the output array up front.
        3. For each requested simulation, build a generator from a SeedSequence
           mixing the run's seed with that simulation's absolute index. Two
           different indices cannot collide or overlap.
        4. Draw the shared shock and the private deviations from that generator
           and combine them exactly as `draw_boards` does.

    Args:
        mu: The calibrated centre for each player, one entry per player.
        sd: The calibrated width for each player, one entry per player.
        num_teams: How many boards to draw per draft.
        seed: The run's base seed, from DraftConfig.random_seed.
        sim_indices: The ABSOLUTE simulation numbers to draw, for example
            `range(250, 500)`. Absolute is the important word — using a
            within-batch index would defeat the whole purpose.
        rho: How much managers agree within a draft.

    Returns:
        np.ndarray: A float32 array shaped
            (len(sim_indices), n_players, num_teams), same meaning as
            `draw_boards`'s output.

    Note:
        WHY THIS EXISTS RATHER THAN JUST CALLING draw_boards ONCE PER BATCH:

        A single shared generator hands out numbers in the order they are asked
        for. Drawing one batch of 24 consumes shared-then-private for all 24 at
        once; drawing four batches of 6 interleaves them differently. Same seed,
        different assignment of numbers to players -- so batch_size would silently
        change the answer.

        That is unacceptable given random_seed exists precisely so a surprising
        result can be reproduced. Somebody lowering batch_size to fit a smaller
        machine would change their draft advice with no indication.

        Giving simulation s its own stream, derived from (seed, s), makes the
        boards a pure function of those two things. batch_size becomes what it
        claims to be: a memory dial with no effect on results.

        A useful side effect: any single simulation can be regenerated in
        isolation, which is how you investigate "why did simulation 4,732 do
        that?" without re-running the other 9,999.
    """
    mu = np.asarray(mu, dtype=np.float64)
    sd = np.asarray(sd, dtype=np.float64)
    n_players = mu.shape[0]
    scale = np.sqrt(1.0 - rho ** 2)

    boards = np.empty((len(sim_indices), n_players, num_teams), dtype=np.float32)
    for row, sim_index in enumerate(sim_indices):
        # SeedSequence mixes the two numbers into an independent stream. Two
        # different sim indices cannot collide or overlap.
        rng = np.random.default_rng(np.random.SeedSequence([seed, int(sim_index)]))
        shared = rng.standard_normal((n_players, 1))
        private = rng.standard_normal((n_players, num_teams))
        boards[row] = mu[:, None] + sd[:, None] * (rho * shared + scale * private)

    return boards


def monte_carlo_sim(mu, sd, pos_index, config, n_sims=10_000, rng=None,
                    batch_size=250, rho=RHO, **kwargs) -> np.ndarray:
    """Run many independent drafts and stack the results into one matrix.

    The top-level entry point for simulating. It exists mostly to work in
    batches: drawing every board at once would need more memory than any machine
    has, so it draws and simulates a slice at a time.

    Steps:
        1. Note the player count and settle the seed, taking it from the config
           unless a generator was passed in to derive one from.
        2. Allocate the full picks matrix, pre-filled with UNDRAFTED.
        3. Walk the simulations in slices of `batch_size`.
        4. For each slice, call `draw_boards_for_sims` above with the ABSOLUTE
           simulation indices, so regrouping the batches cannot change any
           individual draft.
        5. Narrow any per-simulation argument to this batch's rows with
           `_rows_for_batch` below, so a batch is never handed state belonging to
           simulations it is not running.
        6. Hand those boards to `sim_batch` above and write the results into the
           matching rows of the output.

    Args:
        mu: The calibrated centre for each player, one entry per player.
        sd: The calibrated width for each player, one entry per player.
        pos_index: One position number per player, from `position_index` above.
        config: The league settings, which also supply the random seed.
        n_sims: How many drafts to run. 10,000 for production; 2,000 is plenty
            while calibrating.
        rng: An optional generator used to derive a seed. When omitted the seed
            comes straight from config.random_seed.
        batch_size: How many simulations to draw boards for at a time. This is a
            MEMORY dial, not a speed one — see the note below.
        rho: How much managers agree within a draft, passed through to the board
            drawing.
        **kwargs: Anything else is forwarded to `sim_batch`, such as
            `start_pick`, `already_drafted`, `roster_counts`, or `keeper_picks`.
            An argument carrying a row per simulation may be given either
            `n_sims` rows or a single row to apply to all of them.

    Returns:
        np.ndarray: An int16 array shaped (n_sims, n_players), where [s, i] is
            where player i went in simulation s, or UNDRAFTED.

    Note:
        WHY BATCHING EXISTS: boards are (n_sims, n_players, num_teams) float32. At
        10,000 x 250 x 12 that would be 144 GB. At a batch of 250 it is about
        4 MB. Batching is what makes the vectorized approach possible at all.

        DO NOT collapse the result into a per-player summary table. That table is
        tiny and extremely tempting, and it destroys joint queries -- "will at
        least one of these four backs last until my pick" cannot be reconstructed
        from individual probabilities, because the players compete for the same
        picks and their fates are correlated. Joint queries are the entire payoff
        of simulating rather than using a closed-form curve.

        UNDRAFTED PLAYERS ARE FREE. Boards are drawn for the whole pool, the draft
        consumes total_picks of them, and everyone else is simply never taken. No
        mixture model, no special-casing.
    """
    n_players = len(mu)
    seed = config.random_seed if rng is None else int(rng.integers(0, 2 ** 31 - 1))

    picks = np.full((n_sims, n_players), UNDRAFTED, dtype=np.int16)

    for start in range(0, n_sims, batch_size):
        size = min(batch_size, n_sims - start)
        # Per-simulation streams keyed by ABSOLUTE index, so regrouping these
        # batches differently cannot change any individual draft.
        boards = draw_boards_for_sims(
            mu, sd, config.num_teams, seed, range(start, start + size), rho=rho
        )
        # Arguments that carry one row PER SIMULATION have to be cut down to this
        # batch's rows, or sim_batch is handed state for simulations it is not
        # running. Anything shared by every simulation passes through untouched.
        batch_kwargs = {
            name: _rows_for_batch(name, value, start, size, n_sims)
            for name, value in kwargs.items()
        }
        picks[start:start + size] = sim_batch(boards, pos_index, config, **batch_kwargs)

    return picks


def _rows_for_batch(name, value, start, size, n_sims):
    """Cut a per-simulation argument down to the rows one batch needs.

    `monte_carlo_sim` runs the draft in batches, but some of its arguments carry
    a row per simulation -- `already_drafted` as a grid, and `roster_counts`.
    Passing those through whole would hand a batch of 250 simulations the state
    of all 10,000, so they have to be sliced alongside the boards.

    Steps:
        1. Pass None straight through, since an absent argument needs no slicing.
        2. Pass anything one-dimensional through untouched. A flat
           `already_drafted` describes one starting board shared by every
           simulation, so there are no per-simulation rows to cut.
        3. If the first axis has one row per simulation, take just this batch's
           slice of it.
        4. If it has exactly one row, repeat that row for the batch. This is the
           convenient case for the in-draft tool, where one real board state is
           applied to every simulation.
        5. Otherwise refuse, naming the argument and both shapes -- see Raises.

    Args:
        name: The argument's name, used only to make an error message readable.
        value: The argument as the caller supplied it.
        start: Index of the first simulation in this batch.
        size: How many simulations are in this batch.
        n_sims: How many simulations the whole run covers.

    Returns:
        The argument narrowed to this batch: a slice, a repeated single row, or
            the original object when it is shared across simulations.

    Raises:
        ValueError: If the first axis is neither 1 nor `n_sims`. Left alone this
            surfaces much later as a numpy broadcasting error inside the pick
            loop, which says nothing about which argument was wrong.
    """
    if value is None:
        return None

    array = np.asarray(value)
    if array.ndim < 2:
        return value

    rows = array.shape[0]
    if rows == n_sims:
        return array[start:start + size]
    if rows == 1:
        return np.repeat(array, size, axis=0)

    raise ValueError(
        f"{name} has {rows} rows but the run covers {n_sims} simulations. "
        f"Per-simulation arguments need either one row per simulation or a "
        f"single row to apply to all of them."
    )

# ---------------------------------------------------------------------------
# Reference implementation -- correctness, not speed
# ---------------------------------------------------------------------------

def sim_one_draft_reference(board, pos_index, config, start_pick=1, end_pick=None,
                            keeper_picks=None):
    """Simulate ONE draft the slow, obvious way, for tests to check against.

    Does exactly what `sim_batch` above does, but as a plain Python loop that
    can be read and verified by eye. Nothing in the app calls this; its only job
    is to be the thing the fast version is proven equal to.

    Steps:
        1. Import `effective_value` from draft_model/mechanics.py here rather
           than at the top, keeping the reference path's dependencies local.
        2. Read the shape and default the end pick to the full draft.
        3. Set up plain Python state: a taken list, a roster-count dictionary per
           team, and a picks array full of UNDRAFTED.
        4. Mark every kept player taken before the first pick.
        5. For each pick, ask `snake_order` which team is on the clock.
        6. If a keeper occupies this pick, record him and bump his team's count,
           then move on without selecting anybody.
        7. Walk every player one at a time, skip the ones already taken, and run
           each through `effective_value` to apply positional need.
        8. Track the smallest adjusted value seen, which is this team's choice.
        9. Mark the player taken, bump that team's count for his position, and
           record the pick number.

    Args:
        board: A single draft's manager opinions, shaped
            (n_players, num_teams) — one draft, not a batch.
        pos_index: One position number per player.
        config: The league settings.
        start_pick: The first pick to simulate, 1-indexed.
        end_pick: The last pick, inclusive. Defaults to the full draft.
        keeper_picks: Which picks are spent on keepers, as a mapping from an
            overall pick number to the kept player's column index. Same meaning
            as in `sim_batch` above, so the two can be compared directly.

    Returns:
        np.ndarray: An int16 array with one entry per player, holding the pick
            number he went at or UNDRAFTED.

    Note:
        A plain Python loop using mechanics.effective_value -- transparently
        correct and far too slow for real use. Its only job is to be the thing
        sim_batch is proven equal to.

        Keeping it matters: the vectorized version is compact and clever, and
        clever code drifts. This is what makes such a drift fail a test rather
        than quietly change your draft advice.
    """
    from draft_model.mechanics import effective_value

    n_players, num_teams = board.shape
    end_pick = config.total_picks if end_pick is None else end_pick
    keeper_picks = dict(keeper_picks or {})

    taken = [False] * n_players
    counts = [dict() for _ in range(num_teams)]
    picks = np.full(n_players, UNDRAFTED, dtype=np.int16)

    for keeper_column in keeper_picks.values():
        taken[keeper_column] = True

    for pick in range(start_pick, end_pick + 1):
        team = snake_order(pick, config.num_teams, config.third_round_reversal)

        keeper_column = keeper_picks.get(pick)
        if keeper_column is not None:
            position = POSITIONS[pos_index[keeper_column]]
            counts[team][position] = counts[team].get(position, 0) + 1
            picks[keeper_column] = pick
            continue

        best_index, best_value = None, None
        for i in range(n_players):
            if taken[i]:
                continue
            value = effective_value(
                float(board[i, team]), POSITIONS[pos_index[i]], counts[team], pick
            )
            if best_value is None or value < best_value:
                best_index, best_value = i, value

        position = POSITIONS[pos_index[best_index]]
        taken[best_index] = True
        counts[team][position] = counts[team].get(position, 0) + 1
        picks[best_index] = pick

    return picks
