"""Shared fantasy-point scoring rules.

The single source of truth for the scoring formula, imported by both the app
(via services/projections_service.py, once built) and the ingestion scripts
(scripts/scrape_espn_projections.py, scripts/scrape_sleeper_projections.py) so
the formula and its constants are defined exactly once.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, TypeVar

T = TypeVar("T")

GAMES_PER_SEASON = 17

PASSING_YARDS_PER_POINT = 25
RUSHING_YARDS_PER_POINT = 10
RECEIVING_YARDS_PER_POINT = 10

PASSING_TD_POINTS = 4
RUSHING_TD_POINTS = 6
RECEIVING_TD_POINTS = 6

INTERCEPTION_POINTS = -2
FUMBLE_LOST_POINTS = -2

# Canonical stat keys every caller must provide in `stats` — plain numbers for
# a single player, or pandas Series to vectorize over a DataFrame.
STAT_KEYS = (
    "passing_yards", "passing_tds", "interceptions",
    "rushing_yards", "rushing_tds",
    "receiving_yards", "receiving_tds", "receptions",
    "fumbles_lost",
)


class ScoringFormat(str, Enum):
    """The three scoring systems this app supports.

    They differ in exactly one thing: how much a catch is worth. Standard gives
    nothing, half-PPR half a point, full-PPR a whole point — which is enough to
    change how running backs and receivers rank against each other.

    Inheriting from `str` as well as `Enum` means each value behaves like its own
    text, so `ScoringFormat.HALF_PPR == "half_ppr"` is True. That is what lets
    these be stored in MongoDB and read back without conversion.
    """

    REGULAR = "regular"
    HALF_PPR = "half_ppr"
    FULL_PPR = "full_ppr"


@dataclass(frozen=True)
class ScoringRules:
    """Every number the scoring formula needs, for one scoring format.

    Bundled into one object so `fantasy_points` takes a single rules argument
    rather than a dozen loose numbers. Frozen, meaning the values cannot be
    changed after it is built, since these are league rules rather than state.

    Every field except `reception_points` has a default taken from the constants
    above, so defining a format is usually a one-argument job.

    Attributes:
        reception_points: Points per catch. The only field that actually differs
            between the three formats.
        passing_yards_per_point: How many passing yards earn one point.
        rushing_yards_per_point: How many rushing yards earn one point.
        receiving_yards_per_point: How many receiving yards earn one point.
        passing_td_points: Points per passing touchdown.
        rushing_td_points: Points per rushing touchdown.
        receiving_td_points: Points per receiving touchdown.
        interception_points: Points per interception thrown. Negative.
        fumble_lost_points: Points per fumble lost. Negative.
        games_per_season: Games used to convert a season total to per-game.
    """

    reception_points: float
    passing_yards_per_point: float = PASSING_YARDS_PER_POINT
    rushing_yards_per_point: float = RUSHING_YARDS_PER_POINT
    receiving_yards_per_point: float = RECEIVING_YARDS_PER_POINT
    passing_td_points: float = PASSING_TD_POINTS
    rushing_td_points: float = RUSHING_TD_POINTS
    receiving_td_points: float = RECEIVING_TD_POINTS
    interception_points: float = INTERCEPTION_POINTS
    fumble_lost_points: float = FUMBLE_LOST_POINTS
    games_per_season: int = GAMES_PER_SEASON


SCORING_RULES: dict[ScoringFormat, ScoringRules] = {
    ScoringFormat.REGULAR: ScoringRules(reception_points=0),
    ScoringFormat.HALF_PPR: ScoringRules(reception_points=0.5),
    ScoringFormat.FULL_PPR: ScoringRules(reception_points=1),
}


def fantasy_points(stats: Mapping[str, T], fmt: ScoringFormat) -> T:
    """Convert a stat line into season-total fantasy points.

    The single source of truth for the scoring formula. Both the app and the
    ingestion scripts call this, so they cannot drift apart on what a touchdown
    or a reception is worth.

    Steps:
        1. Look up the rules for the requested format, which mainly differ in how
           much a reception is worth.
        2. Add up each stat's contribution: yardage stats are divided by a
           yards-per-point rate, and everything else is multiplied by a
           per-event value.

    Args:
        stats: The player's stat line, which must contain every key in
            STAT_KEYS. Values may be plain numbers for a single player, or pandas
            Series to compute a whole column at once.
        fmt: Which scoring format to score under.

    Returns:
        The season point total, of the same kind as the values passed in — a
            number in, a number out; a Series in, a Series out.

    Raises:
        KeyError: If any of STAT_KEYS is missing from `stats`.

    Note:
        The term order matches the original formula exactly, kept stable on
        purpose. Floating-point addition is not perfectly associative, so
        reordering these terms could shift results in the last decimal place and
        make stored output stop matching.
    """
    rules = SCORING_RULES[fmt]
    return (
        stats["passing_yards"] / rules.passing_yards_per_point
        + stats["passing_tds"] * rules.passing_td_points
        + stats["interceptions"] * rules.interception_points
        + stats["rushing_yards"] / rules.rushing_yards_per_point
        + stats["rushing_tds"] * rules.rushing_td_points
        + stats["receiving_yards"] / rules.receiving_yards_per_point
        + stats["receiving_tds"] * rules.receiving_td_points
        + stats["fumbles_lost"] * rules.fumble_lost_points
        + stats["receptions"] * rules.reception_points
    )


def fantasy_points_all_formats(stats: Mapping[str, T]) -> dict:
    """Score one stat line under every scoring format at once.

    A convenience for the common case: the projections service stores all three
    formats for every player, since scoring format is a per-draft setting and any
    of them might be asked for later.

    Steps:
        1. Walk every value of ScoringFormat.
        2. Call `fantasy_points` above once per format, collecting the results.

    Args:
        stats: The player's stat line, which must contain every key in
            STAT_KEYS. Plain numbers or pandas Series both work.

    Returns:
        dict: Maps each ScoringFormat to that format's season point total, in the
            same form as the values passed in.

    Raises:
        KeyError: If any of STAT_KEYS is missing from `stats`.
    """
    return {fmt: fantasy_points(stats, fmt) for fmt in ScoringFormat}


def per_game(points: T, games: int = GAMES_PER_SEASON) -> T:
    """Convert a season point total into an average per game.

    Per-game numbers are the fairer comparison when players are projected for
    different workloads, and they are what most rankings quote.

    Steps:
        1. Divide the season total by the number of games.

    Args:
        points: A season point total, either a plain number or a pandas Series.
        games: How many games to divide across. Defaults to GAMES_PER_SEASON.

    Returns:
        The average points per game, of the same kind as `points`.

    Raises:
        ZeroDivisionError: If `games` is 0 and `points` is a plain number. With a
            pandas Series you get infinity instead of an error.
    """
    return points / games
