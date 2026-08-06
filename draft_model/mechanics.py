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
    """Work out which team owns a given pick.

    The foundation everything else rests on. Getting this wrong does not raise an
    error, it produces a completely plausible draft in which the wrong teams own
    the picks, so it is kept as small and testable as possible.

    Steps:
        1. Convert the pick number into a round number. Subtracting 1 first makes
           the division work from zero, which is what integer division needs.
        2. Work out the slot within that round the same way, using the remainder.
        3. If third-round reversal is on and we are at round 3 or later, pretend
           the round number is one higher. That single trick flips the direction
           of every round from there on.
        4. Odd rounds run forward, so the slot is the team id directly.
        5. Even rounds run backward, so count in from the other end instead.

    Args:
        pick_num: Absolute pick number, 1-indexed, so pick 1 is the first
            selection of the whole draft.
        num_teams: League size.
        third_round_reversal: If True, round 3 repeats round 2's order instead of
            reverting to round 1's, and later rounds alternate from there. Some
            platforms use this to soften the first-round advantage.

    Returns:
        int: The team id, 0-indexed, so a 12-team league returns 0 through 11.
            Note this is one less than the draft slot a person would say out
            loud: slot 1 is team id 0.

    Note:
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
    """List every pick number belonging to one draft slot.

    This is what turns "I pick 5th" into the concrete set of pick numbers a
    Draft Plan is evaluated at. DraftConfig.my_picks is just a cached call to
    this.

    Steps:
        1. Convert the 1-indexed draft slot into the 0-indexed team id that
           `snake_order` speaks in.
        2. Walk every pick number in the draft, from 1 to teams times rounds.
        3. Call `snake_order` above for each and keep the ones that come back as
           this team.

    Args:
        draft_position: The slot, 1-indexed, where 1 means the first overall
            pick.
        num_teams: League size.
        num_rounds: How many rounds are drafted.
        third_round_reversal: Passed straight through to `snake_order`.

    Returns:
        tuple: One pick number per round, ascending — for example
            (5, 20, 29, 44, ...) for slot 5 in a 12-team draft.

    Note:
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
    """Adjust a manager's value for a player based on what he already rostered.

    This is what makes simulated managers behave like people rather than like a
    ranking list: they stop taking a position once they have enough, and they
    reach for one they still lack. Positional runs fall out of these two rules on
    their own.

    Steps:
        1. Look up how many players this manager already has at this position,
           treating an absent entry as zero.
        2. If he is at the HARD_LIMIT for the position, add BLOCK to his value.
           Since lower is better, adding a huge number pushes the player below
           every real candidate, which makes him effectively unpickable without a
           separate legality check anywhere.
        3. Otherwise, if he has none at this position and the draft is already
           past that position's STARTER_DEADLINE, subtract NEED_BONUS, which
           makes him reach.
        4. If neither applies, hand back the value untouched.

    Args:
        base_value: This manager's drawn board value for the player. LOWER IS
            BETTER — the value behaves like a pick number, so the best player has
            the smallest number.
        position: The player's position, such as "RB".
        roster_counts: Maps a position to how many players this manager already
            has there. Positions he has none of may be absent entirely.
        pick_num: The current absolute pick number, used to tell whether a
            starter deadline has passed.

    Returns:
        float: The adjusted value, on the same lower-is-better scale as the
            input.

    Note:
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
