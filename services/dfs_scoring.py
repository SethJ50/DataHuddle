"""Restates fantasy points in the scoring the contest you are entering uses.

`ff_opportunity`, the source of every expected-points number in this app, scores
everything in full PPR. FanDuel does not use full PPR. So a page that shows those
numbers unchanged is showing you points nobody is paying out, and the difference
is large enough to reorder players.

This module converts between the two. It is arithmetic rather than a scoring
engine: the source has already done the hard part of turning yards and
touchdowns into points, and all that is left is to adjust the handful of events
the two formats disagree about.
"""

from dataclasses import dataclass
from enum import Enum

from scoring import SCORING_RULES, ScoringFormat


class DfsScoring(str, Enum):
    """The scoring systems the Daily Fantasy pages can display.

    Inherits from `str`, like `ScoringFormat` in scoring.py, so a member
    compares equal to its own text and can be dropped straight into a Streamlit
    widget without unwrapping.

    Attributes:
        FANDUEL: FanDuel's scoring. The default, since it is the site these pages
            are built for.
        DRAFTKINGS: DraftKings' scoring. Full points per reception like the
            source, but more forgiving about turnovers and it pays yardage
            bonuses -- see `YARDAGE_BONUSES` below.
        PPR: Full points-per-reception, which is what the underlying data arrives
            in. Kept because it is the untouched source, and because comparing
            against it is how you sanity-check the conversion.
    """

    FANDUEL = "FanDuel"
    DRAFTKINGS = "DraftKings"
    PPR = "PPR"

    # From Python 3.11, printing a str-based enum member gives "DfsScoring.
    # FANDUEL" rather than "FanDuel". These members go straight into widget
    # labels and captions, so they have to read as their own value.
    __str__ = str.__str__


@dataclass(frozen=True)
class DfsScoringRules:
    """The two values the supported scoring systems disagree about.

    Deliberately small. Passing yards, rushing yards, touchdowns, fumbles and
    two-point conversions are scored identically by FanDuel and by the source
    data, so listing them here would invite the idea that changing one would do
    something.

    Attributes:
        reception_points: Points per catch. 1.0 in the source data, 0.5 on
            FanDuel.
        interception_points: Points per interception thrown, negative. -2.0 in
            the source data, -1.0 on FanDuel and DraftKings. THIS IS THE ONE
            PEOPLE FORGET -- see the module note in `rescore` below.
        fumble_points: Points per fumble lost, negative. -2.0 in the source data
            and on FanDuel, -1.0 on DraftKings.
        pays_yardage_bonuses: Whether the site pays a flat bonus for a big
            yardage game. DraftKings does; nobody else here does.
    """

    reception_points: float
    interception_points: float
    fumble_points: float = -2.0
    pays_yardage_bonuses: bool = False


DFS_SCORING_RULES: dict[DfsScoring, DfsScoringRules] = {
    DfsScoring.PPR: DfsScoringRules(
        reception_points=SCORING_RULES[ScoringFormat.FULL_PPR].reception_points,
        interception_points=SCORING_RULES[ScoringFormat.FULL_PPR].interception_points,
    ),
    DfsScoring.DRAFTKINGS: DfsScoringRules(
        # A catch is worth the same full point the source already gives.
        reception_points=SCORING_RULES[ScoringFormat.FULL_PPR].reception_points,
        # Both sites are gentler on turnovers than the standard formats.
        interception_points=-1.0,
        fumble_points=-1.0,
        pays_yardage_bonuses=True,
    ),
    DfsScoring.FANDUEL: DfsScoringRules(
        # Half a point per catch, the same value the season-long half-PPR format
        # uses. Read from there rather than written as 0.5 so the two cannot
        # drift apart.
        reception_points=SCORING_RULES[ScoringFormat.HALF_PPR].reception_points,
        # FanDuel is more forgiving about interceptions than the standard
        # formats, which is why this cannot be borrowed from scoring.py -- all
        # three season-long formats use -2 there.
        interception_points=-1.0,
    ),
}
"""What each supported system pays for the two events they disagree about."""


YARDAGE_BONUSES = (
    ("rush_yards_gained", 100, 3.0),
    ("rec_yards_gained", 100, 3.0),
    ("pass_yards_gained", 300, 3.0),
)
"""DraftKings' flat bonuses: what yardage column, what threshold, how many points.

Applied to ACTUAL points only, and deliberately not to expected ones. A bonus is
all-or-nothing at a threshold, so its expected value is three points times the
CHANCE of clearing it -- and no column here carries that chance. Estimating it
would put the only modelled number in the app inside a figure everything else is
compared against.

The cost of leaving it out is stated where it shows: a heavy-yardage player's gap
between actual and expected runs a point or two high. See `rescore` below.
"""


SOURCE_SCORING = DfsScoring.PPR
"""The scoring the raw data arrives in.

`ff_opportunity` scores in full PPR, verified by solving for its own
coefficients: passing yards 0.04, passing touchdowns 4, interceptions -2,
receptions 1. Every conversion below is expressed as a difference from this, so
if the source ever changes format only this constant and its rules need to move.
"""


# The point columns `ff_opportunity` provides, and what moves each one. Rushing
# points involve neither catches nor interceptions, so they pass through
# untouched -- which is worth stating, because "rushing is unaffected" is easy to
# assume and easy to get wrong.
RECEPTION_ADJUSTED = "rec"
INTERCEPTION_ADJUSTED = "pass"
UNADJUSTED = "rush"


def points_delta(scoring, receptions=0.0, interceptions=0.0, fumbles=0.0):
    """Work out how far one player's points move when the scoring changes.

    The single place the conversion arithmetic lives, so the actual and expected
    numbers can never be converted by two subtly different rules.

    Steps:
        1. Look up what the target system pays per catch, per interception and
           per lost fumble.
        2. Look up the same three values for the scoring the data arrives in.
        3. Multiply each difference by how many of that event happened, and add
           them together.

    Args:
        scoring: Which system to convert to, a `DfsScoring` member.
        receptions: How many catches. May be a number or a whole column of them.
            Expected receptions are fractional, which is fine -- nothing here
            assumes a whole number.
        interceptions: How many interceptions were thrown. Same again.
        fumbles: How many fumbles were lost. Same again.

    Returns:
        The amount to ADD to the source's points. Negative when converting to
        FanDuel for a receiver, since half a point per catch is being taken away.
        Matches the type it was given: a number in, a number out; a column in, a
        column out.

    Raises:
        KeyError: If `scoring` is not one of the supported systems.
    """
    target = DFS_SCORING_RULES[scoring]
    source = DFS_SCORING_RULES[SOURCE_SCORING]

    per_reception = target.reception_points - source.reception_points
    per_interception = target.interception_points - source.interception_points
    per_fumble = target.fumble_points - source.fumble_points

    return (per_reception * receptions
            + per_interception * interceptions
            + per_fumble * fumbles)


def rescore(frame, scoring=DfsScoring.FANDUEL):
    """Restate a table of expected-points data in the chosen scoring system.

    Takes the frame `DfsReadRepo.ff_opportunity` returns and rewrites its eight
    points columns -- actual and expected, for passing, rushing, receiving and
    the total -- so they say what the player was worth under the scoring you
    care about.

    Steps:
        1. Copy the frame, so the caller's cached table is never edited
           underneath it.
        2. Return it unchanged if the target is the scoring the data already
           arrives in, which saves doing arithmetic that adds up to zero.
        3. Shift the receiving points by the reception difference, using actual
           catches for the actual points and expected catches for the expected
           points.
        4. Shift the passing points by the interception difference, and the
           rushing and receiving points by the fumble difference, the same way.
        5. Add DraftKings' yardage bonuses to the ACTUAL points only -- see the
           note, and `YARDAGE_BONUSES` above.
        6. Rebuild each total by adding its three parts back together.

    Args:
        frame: An `ff_opportunity` weekly table. Needs the eight `*_fantasy_points`
            and `*_fantasy_points_exp` columns plus `receptions`,
            `receptions_exp`, `pass_interception` and `pass_interception_exp`.
        scoring: Which system to convert to. Defaults to FanDuel.

    Returns:
        pd.DataFrame: A copy with the points columns restated. Every other column
            is untouched, so this can be dropped into a pipeline anywhere.

    Raises:
        KeyError: If a needed column is missing, rather than silently producing
            points that are wrong in a way nobody would notice.

    Note:
        BOTH SIDES ARE CONVERTED BY THE SAME RULE, which is the whole reason this
        is one function rather than two. Everything these pages show is really a
        DIFFERENCE -- what a player scored against what his opportunities were
        worth -- and if the two sides were converted even slightly differently
        the error would live inside that difference, where nobody would spot it.

        THE INTERCEPTION ADJUSTMENT IS EASY TO MISS. The obvious half of this
        conversion is receptions, and for backs, receivers and tight ends that is
        genuinely all of it. Quarterbacks are the exception: FanDuel charges one
        point for an interception where the source data charges two, so a
        quarterback converted on catches alone comes out too low by one point per
        interception.

        Totals are REBUILT from their parts rather than adjusted directly. The
        source guarantees the parts sum to the total exactly, so rebuilding keeps
        that true instead of hoping two separate adjustments stay in step.

        DRAFTKINGS' YARDAGE BONUSES GO ON ACTUAL POINTS ONLY, and this is a known
        asymmetry rather than an oversight. A bonus is three points for clearing
        a threshold, so its expected value is three times the CHANCE of clearing
        it -- a number nothing in this data carries. The consequence is that a
        heavy-yardage player's actual-minus-expected gap reads a point or two
        high on DraftKings, which the pages say where they show it. The
        alternative was to model the chance, which would have put the app's only
        estimated figure inside the number everything else is measured against.
    """
    frame = frame.copy()

    if scoring == SOURCE_SCORING:
        return frame

    for suffix, catches, picks in (("", "receptions", "pass_interception"),
                                   ("_exp", "receptions_exp",
                                    "pass_interception_exp")):
        receiving = f"{RECEPTION_ADJUSTED}_fantasy_points{suffix}"
        passing = f"{INTERCEPTION_ADJUSTED}_fantasy_points{suffix}"
        rushing = f"{UNADJUSTED}_fantasy_points{suffix}"
        total = f"total_fantasy_points{suffix}"

        missing = [name for name in (receiving, passing, rushing, total,
                                     catches, picks) if name not in frame.columns]
        if missing:
            raise KeyError(f"cannot rescore without {missing}")

        frame[receiving] = frame[receiving] + points_delta(
            scoring, receptions=frame[catches].fillna(0))
        frame[passing] = frame[passing] + points_delta(
            scoring, interceptions=frame[picks].fillna(0))

        # Fumbles are charged where they happened. The suffix is dropped for the
        # expected side because the source has no expected-fumble column -- a
        # fumble is not an opportunity, so nothing projects one.
        if suffix == "":
            for column, part in (("rush_fumble_lost", rushing),
                                 ("rec_fumble_lost", receiving)):
                if column in frame.columns:
                    frame[part] = frame[part] + points_delta(
                        scoring, fumbles=frame[column].fillna(0))

            if DFS_SCORING_RULES[scoring].pays_yardage_bonuses:
                for column, threshold, bonus in YARDAGE_BONUSES:
                    if column not in frame.columns:
                        continue
                    part = {"rush_yards_gained": rushing,
                            "rec_yards_gained": receiving,
                            "pass_yards_gained": passing}[column]
                    cleared = frame[column].fillna(0) >= threshold
                    frame[part] = frame[part] + cleared * bonus

        frame[total] = frame[passing] + frame[receiving] + frame[rushing]

    return frame
