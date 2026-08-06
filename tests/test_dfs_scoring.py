"""Tests for restating fantasy points in FanDuel's scoring.

The source data is full PPR; FanDuel is not. The gap is big enough to reorder
players, so this conversion sits underneath every DFS number in the app.

The two things most worth pinning:

1. THE INTERCEPTION RULE. The obvious half of this conversion is receptions, and
   for backs, receivers and tight ends that really is all of it. Quarterbacks are
   the exception -- FanDuel charges one point for an interception where the
   source charges two -- and a conversion that forgets it is wrong only for
   quarterbacks, only sometimes, and never by enough to look obviously broken.
2. BOTH SIDES CONVERT IDENTICALLY. Everything these pages show is a difference
   between actual and expected points. An inconsistency between the two would
   hide inside that difference where nobody would find it.
"""

import numpy as np
import pandas as pd
import pytest

from scoring import SCORING_RULES, ScoringFormat
from services.dfs_scoring import (
    DFS_SCORING_RULES, SOURCE_SCORING, DfsScoring, points_delta, rescore,
)


def opportunity_frame(**overrides):
    """One row of `ff_opportunity` data, with every column `rescore` needs.

    Defaults to a clean receiver line -- catches but no interceptions -- so a
    test only has to state the part it cares about.
    """
    row = {
        "pass_fantasy_points": 0.0, "pass_fantasy_points_exp": 0.0,
        "rec_fantasy_points": 20.0, "rec_fantasy_points_exp": 16.0,
        "rush_fantasy_points": 0.0, "rush_fantasy_points_exp": 0.0,
        "total_fantasy_points": 20.0, "total_fantasy_points_exp": 16.0,
        "receptions": 8.0, "receptions_exp": 6.0,
        "pass_interception": 0.0, "pass_interception_exp": 0.0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


def test_a_catch_is_worth_half_a_point_less_on_fanduel():
    assert points_delta(DfsScoring.FANDUEL, receptions=8) == pytest.approx(-4.0)


def test_an_interception_costs_a_point_less_on_fanduel():
    # The half people forget. The source charges -2, FanDuel charges -1, so
    # converting GIVES A POINT BACK for every interception thrown.
    assert points_delta(DfsScoring.FANDUEL, interceptions=2) == pytest.approx(+2.0)


def test_the_two_adjustments_add_together():
    # A quarterback who caught a pass, which happens on trick plays.
    assert points_delta(DfsScoring.FANDUEL, receptions=1,
                        interceptions=1) == pytest.approx(0.5)


def test_converting_to_the_source_scoring_changes_nothing():
    assert points_delta(SOURCE_SCORING, receptions=9, interceptions=3) == 0.0


def test_fractional_receptions_are_fine():
    # Expected receptions are averages, so 6.4 catches is a normal input.
    assert points_delta(DfsScoring.FANDUEL,
                        receptions=6.4) == pytest.approx(-3.2)


def test_it_works_on_a_whole_column_at_once():
    catches = pd.Series([0.0, 4.0, 10.0])
    shifted = points_delta(DfsScoring.FANDUEL, receptions=catches)
    assert list(shifted) == [0.0, -2.0, -5.0]


def test_an_unknown_scoring_system_raises():
    with pytest.raises(KeyError):
        points_delta("DraftKings", receptions=1)


def test_the_half_point_is_borrowed_from_the_season_long_rules():
    # Written once, in scoring.py, so the two halves of the app cannot drift
    # apart on what half-PPR means.
    assert (DFS_SCORING_RULES[DfsScoring.FANDUEL].reception_points
            == SCORING_RULES[ScoringFormat.HALF_PPR].reception_points)


def test_fanduel_is_not_simply_the_season_long_half_ppr_format():
    # It would be convenient if it were, and the interception rule is exactly
    # why it is not. All three season-long formats charge -2.
    assert (DFS_SCORING_RULES[DfsScoring.FANDUEL].interception_points
            != SCORING_RULES[ScoringFormat.HALF_PPR].interception_points)


# ---------------------------------------------------------------------------
# Rescoring a table
# ---------------------------------------------------------------------------


def test_a_receiver_loses_half_a_point_per_catch():
    frame = rescore(opportunity_frame(), DfsScoring.FANDUEL)
    assert frame["total_fantasy_points"].iloc[0] == pytest.approx(16.0)   # 20 - 8*0.5
    assert frame["rec_fantasy_points"].iloc[0] == pytest.approx(16.0)


def test_expected_points_convert_by_expected_catches():
    # Not by actual catches. Using the wrong one would make the gap between
    # actual and expected wrong by half a point per catch of difference.
    frame = rescore(opportunity_frame(), DfsScoring.FANDUEL)
    assert frame["total_fantasy_points_exp"].iloc[0] == pytest.approx(13.0)  # 16 - 6*0.5


def test_a_quarterback_gains_a_point_per_interception():
    frame = rescore(
        opportunity_frame(pass_fantasy_points=18.0, pass_fantasy_points_exp=20.0,
                          rec_fantasy_points=0.0, rec_fantasy_points_exp=0.0,
                          total_fantasy_points=18.0, total_fantasy_points_exp=20.0,
                          receptions=0.0, receptions_exp=0.0,
                          pass_interception=2.0, pass_interception_exp=1.5),
        DfsScoring.FANDUEL)

    assert frame["total_fantasy_points"].iloc[0] == pytest.approx(20.0)      # 18 + 2
    assert frame["total_fantasy_points_exp"].iloc[0] == pytest.approx(21.5)  # 20 + 1.5


def test_rushing_points_are_never_touched():
    # Rushing involves neither catches nor interceptions. Easy to assume, worth
    # asserting, because a blanket adjustment applied to every column would pass
    # every other test in this file.
    frame = rescore(opportunity_frame(rush_fantasy_points=12.0,
                                      rush_fantasy_points_exp=9.0,
                                      total_fantasy_points=32.0,
                                      total_fantasy_points_exp=25.0),
                    DfsScoring.FANDUEL)
    assert frame["rush_fantasy_points"].iloc[0] == 12.0
    assert frame["rush_fantasy_points_exp"].iloc[0] == 9.0


def test_the_total_always_equals_its_parts():
    # The source guarantees this and the conversion must preserve it, or a page
    # showing the breakdown would disagree with the page showing the headline.
    frame = rescore(
        opportunity_frame(pass_fantasy_points=14.0, pass_fantasy_points_exp=11.0,
                          rush_fantasy_points=7.0, rush_fantasy_points_exp=6.0,
                          total_fantasy_points=41.0, total_fantasy_points_exp=33.0,
                          pass_interception=1.0, pass_interception_exp=0.8),
        DfsScoring.FANDUEL)

    for suffix in ("", "_exp"):
        parts = sum(frame[f"{part}_fantasy_points{suffix}"].iloc[0]
                    for part in ("pass", "rec", "rush"))
        assert frame[f"total_fantasy_points{suffix}"].iloc[0] == pytest.approx(parts)


def test_both_sides_move_by_the_same_rule():
    # The reason actual and expected are converted in one function. A player
    # whose actual catches equal his expected catches must see both sides shift
    # by exactly the same amount, leaving the gap between them unchanged.
    before = opportunity_frame(receptions=6.0, receptions_exp=6.0)
    after = rescore(before, DfsScoring.FANDUEL)

    gap_before = (before["total_fantasy_points"] - before["total_fantasy_points_exp"]).iloc[0]
    gap_after = (after["total_fantasy_points"] - after["total_fantasy_points_exp"]).iloc[0]
    assert gap_after == pytest.approx(gap_before)


def test_asking_for_the_source_scoring_returns_the_numbers_untouched():
    before = opportunity_frame()
    after = rescore(before, DfsScoring.PPR)
    pd.testing.assert_frame_equal(before, after)


def test_the_original_table_is_never_edited():
    # The repository hands out one cached copy of this table. Editing it in
    # place would poison every later read, and the corruption would depend on
    # which page you happened to open first.
    before = opportunity_frame()
    rescore(before, DfsScoring.FANDUEL)
    assert before["total_fantasy_points"].iloc[0] == 20.0


def test_missing_data_counts_as_none_rather_than_breaking_the_row():
    frame = rescore(opportunity_frame(receptions=np.nan, receptions_exp=np.nan),
                    DfsScoring.FANDUEL)
    assert frame["rec_fantasy_points"].iloc[0] == 20.0


def test_a_missing_column_is_refused_loudly():
    # The alternative is points that are wrong in a way nobody would notice.
    frame = opportunity_frame().drop(columns=["pass_interception_exp"])
    with pytest.raises(KeyError, match="pass_interception_exp"):
        rescore(frame, DfsScoring.FANDUEL)


def test_other_columns_are_carried_through_untouched():
    frame = opportunity_frame()
    frame["full_name"] = "Player One"
    frame["position"] = "WR"

    out = rescore(frame, DfsScoring.FANDUEL)
    assert out["full_name"].iloc[0] == "Player One"
    assert out["position"].iloc[0] == "WR"


def test_the_shift_is_exactly_the_two_rules_and_nothing_else():
    # Stated as the property the whole module promises, over a spread of lines
    # rather than one hand-worked example.
    rng = np.random.default_rng(0)
    rows = pd.DataFrame({
        "pass_fantasy_points": rng.uniform(0, 30, 200),
        "rec_fantasy_points": rng.uniform(0, 30, 200),
        "rush_fantasy_points": rng.uniform(0, 20, 200),
        "receptions": rng.integers(0, 12, 200).astype(float),
        "pass_interception": rng.integers(0, 4, 200).astype(float),
        "receptions_exp": rng.uniform(0, 12, 200),
        "pass_interception_exp": rng.uniform(0, 4, 200),
    })
    for suffix in ("_exp",):
        for part in ("pass", "rec", "rush"):
            rows[f"{part}_fantasy_points{suffix}"] = rng.uniform(0, 30, 200)
    for suffix in ("", "_exp"):
        rows[f"total_fantasy_points{suffix}"] = sum(
            rows[f"{part}_fantasy_points{suffix}"] for part in ("pass", "rec", "rush"))

    out = rescore(rows, DfsScoring.FANDUEL)

    for suffix, catches, picks in (("", "receptions", "pass_interception"),
                                   ("_exp", "receptions_exp", "pass_interception_exp")):
        shift = out[f"total_fantasy_points{suffix}"] - rows[f"total_fantasy_points{suffix}"]
        promised = -0.5 * rows[catches] + 1.0 * rows[picks]
        assert np.allclose(shift, promised)
