"""Draft mechanics: whose pick is it, and how does need distort a manager's board.

Two small functions, one of which is disproportionately dangerous.

snake_order() decides which team owns each pick. Getting it wrong does not raise
an error -- it produces a completely plausible draft in which the wrong teams own
the picks, and every probability computed on top of it is quietly wrong. That is
why its tests live beside it and were written before anything else depends on it.

effective_value() is the SCALAR REFERENCE implementation of positional need. The
real simulator does this vectorized across thousands of drafts at once
(DESIGN.md 3.1); this version exists so the tests can assert the fast path agrees
with an obviously-correct slow one.
"""

from draft_model import config as cfg


def snake_order(pick_num: int, num_teams: int, third_round_reversal: bool = False) -> int:
    """
    Purpose: Work out which team owns a given pick.

    Parameters:
        pick_num (int): Absolute pick number, 1-indexed (pick 1 = first overall).
        num_teams (int): League size.
        third_round_reversal (bool): If True, round 3 repeats round 2's order
            instead of reverting to round 1's, and later rounds alternate from
            there. Some platforms use this to soften the first-round advantage.

    Returns:
        int: Team id, 0-indexed (0 .. num_teams - 1).

    Notes:
        A snake draft alternates direction each round: round 1 runs 0,1,2..,
        round 2 runs ..2,1,0, round 3 forward again. So odd rounds go forward and
        even rounds go backward.

        Third-round reversal is handled by pretending the round number is one
        higher from round 3 onward, which flips the parity for every round after
        it. Round 3 then inherits round 2's backward order, round 4 goes forward,
        and so on.

        For 4 teams and 3 rounds the sequence must be:
            [0, 1, 2, 3, 3, 2, 1, 0, 0, 1, 2, 3]
        and with reversal, round 3 becomes [3, 2, 1, 0].
    """
    round_num = (pick_num - 1) // num_teams + 1
    slot = (pick_num - 1) % num_teams

    effective_round = round_num + 1 if (third_round_reversal and round_num >= 3) else round_num

    if effective_round % 2 == 1:      # odd round -> forward
        return slot
    return num_teams - 1 - slot        # even round -> backward


def picks_for_slot(draft_position: int, num_teams: int, num_rounds: int,
                   third_round_reversal: bool = False) -> tuple:
    """
    Purpose: Every absolute pick number belonging to one draft slot.

    Parameters:
        draft_position (int): The slot, 1-indexed (1 = first overall pick).
        num_teams (int), num_rounds (int): League shape.
        third_round_reversal (bool): Passed through to snake_order.

    Returns:
        tuple[int]: One pick per round, ascending -- e.g. (5, 20, 29, 44, ...).

    Notes:
        Derived from snake_order rather than computed with its own arithmetic,
        deliberately. Two independent formulas for the same thing is how they
        drift apart; this way a snake_order fix automatically corrects this too.

        Note the 1-indexed slot / 0-indexed team id conversion -- draft_position 1
        is team id 0. Mixing those up is the single easiest mistake here.
    """
    team_id = draft_position - 1
    return tuple(
        pick for pick in range(1, num_teams * num_rounds + 1)
        if snake_order(pick, num_teams, third_round_reversal) == team_id
    )


def effective_value(base_value: float, position: str, roster_counts: dict,
                    pick_num: int) -> float:
    """
    Purpose: Adjust one manager's private value for a player, given what that
        manager has already rostered.

    Parameters:
        base_value (float): This manager's drawn board value. LOWER IS BETTER --
            the value behaves like a pick number, so the best player has the
            smallest number.
        position (str): The player's position.
        roster_counts (dict): Position -> how many this manager already has.
        pick_num (int): Current absolute pick, used for starter deadlines.

    Returns:
        float: The adjusted value, same lower-is-better convention.

    Notes:
        APPLIED AT READ TIME, EVERY TIME. The result must never be written back
        into the stored board -- adjustments would compound over sixteen rounds
        into nonsense.

        SCALAR REFERENCE IMPLEMENTATION. The simulator does this vectorized
        across a whole batch of drafts (DESIGN.md 3.1). This version is kept
        because it is obviously correct, and the tests assert the fast path
        matches it.

        Positional runs are an EMERGENT property of these two rules, not
        something programmed anywhere.
    """
    have = roster_counts.get(position, 0)

    # Position already full: push him below everyone, rather than special-casing
    # legality somewhere else.
    if have >= cfg.HARD_LIMIT.get(position, 99):
        return base_value + cfg.BLOCK

    # No starter at this position and it's getting late: reach.
    if have == 0 and pick_num > cfg.STARTER_DEADLINE.get(position, 999):
        return base_value - cfg.NEED_BONUS

    return base_value
