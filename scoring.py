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
    REGULAR = "regular"
    HALF_PPR = "half_ppr"
    FULL_PPR = "full_ppr"


@dataclass(frozen=True)
class ScoringRules:
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
    """Season-total fantasy points for one scoring format.

    `stats` must have all of STAT_KEYS. Term order matches the original
    formula exactly (kept stable on purpose, for reproducible output).
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
    """Season-total fantasy points for every ScoringFormat at once."""
    return {fmt: fantasy_points(stats, fmt) for fmt in ScoringFormat}


def per_game(points: T, games: int = GAMES_PER_SEASON) -> T:
    return points / games
