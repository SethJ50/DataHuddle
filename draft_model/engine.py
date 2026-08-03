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
    """
    Purpose: Convert position strings into the small integers the simulator uses.

    Parameters:
        positions (sequence[str]): One position per player, in table row order.

    Returns:
        np.ndarray of int8, shape (n_players,). Each value is an index into
        config.POSITIONS.

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
    """
    Purpose: Repackage the per-position constants as arrays the loop can index.

    Returns:
        tuple (hard_limit, deadline), each an int array of shape (len(POSITIONS),)
        aligned to config.POSITIONS order.

    Notes:
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
    """
    Purpose: Draw each simulated manager's private valuation of every player.

    Parameters:
        mu (np.ndarray): Calibrated centre per player, shape (n_players,).
        sd (np.ndarray): Calibrated width per player, shape (n_players,).
        num_teams (int): How many independent boards per draft.
        rng (np.random.Generator): Seeded, passed in so a whole run shares one
            reproducible stream.
        n_sims (int): Drafts in this batch.
        rho (float): How much managers agree WITHIN a draft. 0 = independent.

    Returns:
        np.ndarray float32, shape (n_sims, n_players, num_teams).
        LOWER IS BETTER -- these behave like pick numbers, so a manager's
        favourite player has his smallest value.

    Notes:
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
              already_drafted=None, roster_counts=None) -> np.ndarray:
    """
    Purpose: Run a whole batch of drafts at once and record where each player went.

    Parameters:
        boards (np.ndarray): (n_sims, n_players, num_teams) from draw_boards.
            Taken as an argument rather than drawn here so tests can hand the
            same boards to a slow reference implementation and demand identical
            drafts.
        pos_index (np.ndarray): (n_players,) position ordinals.
        config (DraftConfig): League settings.
        start_pick (int): First pick to simulate, 1-indexed.
        end_pick (int | None): Last pick, inclusive. Defaults to the full draft.
            A near horizon is what makes the live in-draft tool fast enough.
        already_drafted (np.ndarray | None): (n_players,) bool, or
            (n_sims, n_players) for per-sim state. True = off the board. Use for
            keepers, or to resume a real draft in progress.
        roster_counts (np.ndarray | None): (n_sims, num_teams, n_positions) int
            counts already rostered. Seed from a real draft log when resuming;
            defaults to empty.

    Returns:
        np.ndarray int16, (n_sims, n_players). Entry [s, i] is the pick number
        player i went at in simulation s, or UNDRAFTED (999) if never taken.

    Raises:
        ValueError: If the pool is too small to fill the requested picks. That
            situation would otherwise silently corrupt results -- argmin over an
            all-infinite row returns index 0, quietly "drafting" someone already
            taken.

    Notes:
        THE LOOP, one pick at a time but every simulation at once:

          1. snake_order says which team is on the clock -- the same team in every
             simulation, which is what makes this work.
          2. Take that team's board values for every player: (n_sims, n_players).
          3. Apply positional need AT READ TIME (never written back into the
             board; adjustments would compound over sixteen rounds into nonsense).
          4. Mask out players already gone.
          5. argmin picks each simulation's choice in one vectorized call.

        WHY BLOCK IS FINITE AND `taken` IS INFINITE: a full position gets +BLOCK,
        which pushes a player below every realistic alternative but still leaves
        him selectable if literally everything else is blocked. Already-drafted
        players get +inf, because taking them twice is never acceptable. If BLOCK
        were infinite too, a fully-rostered team could deadlock the draft.

        POSITIONAL RUNS EMERGE from step 3; they are not programmed anywhere. Once
        two managers take tight ends, the remaining TE-less managers start
        applying NEED_BONUS and reaching, and the clustering appears on its own.
    """
    n_sims, n_players, num_teams = boards.shape
    end_pick = config.total_picks if end_pick is None else end_pick
    hard_limit, deadline = position_limit_arrays()

    # --- state, one row per simulation ---
    taken = np.zeros((n_sims, n_players), dtype=bool)
    if already_drafted is not None:
        taken |= np.asarray(already_drafted, dtype=bool)

    counts = (np.zeros((n_sims, num_teams, len(POSITIONS)), dtype=np.int16)
              if roster_counts is None
              else np.asarray(roster_counts, dtype=np.int16).copy())

    picks = np.full((n_sims, n_players), UNDRAFTED, dtype=np.int16)

    # Guard the one failure mode that corrupts silently rather than crashing.
    needed = end_pick - start_pick + 1
    available = n_players - int(taken[0].sum())
    if available < needed:
        raise ValueError(
            f"pool too small: {available} players available but {needed} picks to make"
        )

    rows = np.arange(n_sims)

    for pick in range(start_pick, end_pick + 1):
        team = snake_order(pick, config.num_teams, config.third_round_reversal)

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
    """
    Purpose: Draw boards for specific simulations, reproducibly and independently
        of how those simulations happen to be grouped into batches.

    Parameters:
        mu, sd (np.ndarray): Sampler parameters, shape (n_players,).
        num_teams (int): Boards per draft.
        seed (int): The run's base seed, from DraftConfig.random_seed.
        sim_indices (sequence[int]): ABSOLUTE simulation numbers, e.g. range(250, 500).
        rho (float): Manager agreement.

    Returns:
        np.ndarray float32, (len(sim_indices), n_players, num_teams).

    Notes:
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
    """
    Purpose: Run many independent drafts and stack the results into one matrix.

    Parameters:
        mu, sd (np.ndarray): Calibrated sampler parameters, shape (n_players,).
        pos_index (np.ndarray): Position ordinals per player.
        config (DraftConfig): League settings; supplies the random seed.
        n_sims (int): 10,000 for production; 2,000 is plenty while calibrating.
        rng (np.random.Generator | None): Created from config.random_seed if omitted.
        batch_size (int): Simulations per batch. This is a MEMORY dial, not a
            speed one -- see below.
        rho (float): Manager agreement, passed to draw_boards.
        **kwargs: Forwarded to sim_batch (start_pick, already_drafted, ...).

    Returns:
        np.ndarray int16, (n_sims, n_players). picks[s, i] is where player i went
        in simulation s, or UNDRAFTED.

    Notes:
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
        picks[start:start + size] = sim_batch(boards, pos_index, config, **kwargs)

    return picks

# ---------------------------------------------------------------------------
# Reference implementation -- correctness, not speed
# ---------------------------------------------------------------------------

def sim_one_draft_reference(board, pos_index, config, start_pick=1, end_pick=None):
    """
    Purpose: Simulate ONE draft the slow, obvious way, for tests to check against.

    Parameters:
        board (np.ndarray): (n_players, num_teams) -- a single draft's boards.
        pos_index (np.ndarray): Position ordinals per player.
        config (DraftConfig): League settings.
        start_pick, end_pick (int): Range to simulate.

    Returns:
        np.ndarray int16, (n_players,) of pick numbers / UNDRAFTED.

    Notes:
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

    taken = [False] * n_players
    counts = [dict() for _ in range(num_teams)]
    picks = np.full(n_players, UNDRAFTED, dtype=np.int16)

    for pick in range(start_pick, end_pick + 1):
        team = snake_order(pick, config.num_teams, config.third_round_reversal)

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
