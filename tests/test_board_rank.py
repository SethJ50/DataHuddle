"""Tests for folding a platform's published board order into the ADP blend.

ESPN and Yahoo both publish one; Sleeper does not. The property most worth
locking in is that a board applies INSIDE its own platform's existing share.
Promoting boards to extra sources would quietly raise the platforms that HAVE
one above the ones that do not -- a platform-balance change wearing a rank
feature's clothes, and one nothing else in the pipeline would catch.
"""

import pandas as pd
import pytest

from services.draft_sim_service import DraftSimService, blend_weights
from scoring import ScoringFormat


class FakeComparison:
    """Stands in for AdpComparisonService with hand-written platform numbers."""

    def __init__(self, frame, boards=None):
        self._frame = frame
        self._boards = boards or {}

    def compare(self, fmt):
        return self._frame

    def board_rank(self, source):
        return self._boards.get(source, pd.Series(dtype="float64"))


def service(frame, boards=None):
    return DraftSimService(ffc_service=None,
                           adp_comparison_service=FakeComparison(frame, boards),
                           projections_service=None)


def make_frame(**columns):
    """One row per player, keyed by canonical_id, one column per platform."""
    return pd.DataFrame({"canonical_id": list(columns.pop("ids")), **columns})


def test_rank_weight_zero_is_an_exact_no_op():
    # The escape hatch has to be exact, not approximately exact -- it is what
    # reproduces every artifact built before the board existed.
    frame = make_frame(ids=["p1", "p2"], espn_adp=[10.0, 20.0],
                       yahoo_adp=[12.0, 18.0], sleeper_adp=[11.0, 19.0])
    boards = {"yahoo": pd.Series({"p1": 2.0, "p2": 1.0})}   # disagrees with ADP

    without = service(frame, boards).platform_blend(ScoringFormat.FULL_PPR, "yahoo", 0.0)
    baseline = service(frame).platform_blend(ScoringFormat.FULL_PPR, "yahoo")
    pd.testing.assert_series_equal(without, baseline)


def test_board_moves_a_player_the_board_and_drafters_disagree_on():
    # Yahoo's ADP says p1 goes first, its board says p2 does. With the board
    # weighted in, Yahoo's contribution for p2 must move EARLIER.
    frame = make_frame(ids=["p1", "p2"], espn_adp=[10.0, 20.0],
                       yahoo_adp=[10.0, 20.0], sleeper_adp=[10.0, 20.0])
    boards = {"yahoo": pd.Series({"p1": 2.0, "p2": 1.0})}

    svc = service(frame, boards)
    before = svc.platform_blend(ScoringFormat.FULL_PPR, "yahoo", 0.0)
    after = svc.platform_blend(ScoringFormat.FULL_PPR, "yahoo", 0.5)

    assert after["p2"] < before["p2"]
    assert after["p1"] > before["p1"]


def test_the_board_stays_inside_yahoos_share():
    # THE DESIGN PROPERTY. Yahoo drafts here, so weights are 0.5 yahoo and 0.25
    # each for espn/sleeper. Whatever the board does to Yahoo's own number, the
    # outer weights must still be exactly those.
    frame = make_frame(ids=["p1", "p2"], espn_adp=[10.0, 40.0],
                       yahoo_adp=[20.0, 30.0], sleeper_adp=[10.0, 40.0])
    boards = {"yahoo": pd.Series({"p1": 2.0, "p2": 1.0})}
    rank_weight = 0.5

    blended = service(frame, boards).platform_blend(
        ScoringFormat.FULL_PPR, "yahoo", rank_weight)

    # rank_to_pick_scale hands out Yahoo's own ADP values in board order, so
    # p2 (board rank 1) takes 20.0 and p1 takes 30.0.
    yahoo_p1 = (1 - rank_weight) * 20.0 + rank_weight * 30.0
    expected_p1 = 0.25 * 10.0 + 0.25 * 10.0 + 0.5 * yahoo_p1
    assert blended["p1"] == pytest.approx(expected_p1)


def test_a_player_with_no_board_entry_keeps_his_plain_adp():
    # blend_adp renormalizes per player, so an unranked player must not come out
    # diluted toward zero just because the board has nothing to say about him.
    frame = make_frame(ids=["ranked", "unranked"], espn_adp=[10.0, 50.0],
                       yahoo_adp=[10.0, 50.0], sleeper_adp=[10.0, 50.0])
    boards = {"yahoo": pd.Series({"ranked": 1.0})}

    blended = service(frame, boards).platform_blend(
        ScoringFormat.FULL_PPR, "yahoo", 0.5)
    assert blended["unranked"] == pytest.approx(50.0)


def test_an_empty_board_falls_back_to_pure_adp():
    # Yahoo's rank column going missing must degrade to the old behaviour rather
    # than emptying the blend.
    frame = make_frame(ids=["p1"], espn_adp=[10.0], yahoo_adp=[20.0],
                       sleeper_adp=[30.0])
    blended = service(frame, {}).platform_blend(
        ScoringFormat.FULL_PPR, "yahoo", 0.5)
    assert blended["p1"] == pytest.approx(0.25 * 10.0 + 0.5 * 20.0 + 0.25 * 30.0)


# --------------------------------------------------------------------------
# every platform with a board, not just Yahoo
# --------------------------------------------------------------------------

def test_espn_board_applies_the_same_way_yahoo_s_does():
    # The point of scraping ESPN's board: an ESPN league should now get the same
    # treatment its own platform's list deserves.
    frame = make_frame(ids=["p1", "p2"], espn_adp=[10.0, 20.0],
                       yahoo_adp=[10.0, 20.0], sleeper_adp=[10.0, 20.0])
    boards = {"espn": pd.Series({"p1": 2.0, "p2": 1.0})}

    svc = service(frame, boards)
    before = svc.platform_blend(ScoringFormat.FULL_PPR, "espn", 0.0)
    after = svc.platform_blend(ScoringFormat.FULL_PPR, "espn", 0.5)

    assert after["p2"] < before["p2"]
    assert after["p1"] > before["p1"]


def test_each_platforms_board_only_touches_its_own_share():
    # ESPN's board must move ESPN's number and nothing else. With ESPN at 0.7
    # and no board anywhere else, the other two contribute their raw ADP.
    frame = make_frame(ids=["p1", "p2"], espn_adp=[20.0, 30.0],
                       yahoo_adp=[40.0, 50.0], sleeper_adp=[60.0, 70.0])
    boards = {"espn": pd.Series({"p1": 2.0, "p2": 1.0})}

    blended = service(frame, boards).platform_blend(
        ScoringFormat.FULL_PPR, "espn", 0.5, 0.7)

    # rank_to_pick_scale hands out ESPN's own ADP values in board order, so
    # p1 (board rank 2) takes 30.0 and p2 takes 20.0.
    espn_p1 = 0.5 * 20.0 + 0.5 * 30.0
    expected = 0.7 * espn_p1 + 0.15 * 40.0 + 0.15 * 60.0
    assert blended["p1"] == pytest.approx(expected)


def test_a_platform_with_no_board_is_untouched():
    # Sleeper publishes none, so its contribution must be its plain ADP however
    # high the board weight goes.
    frame = make_frame(ids=["p1", "p2"], espn_adp=[10.0, 20.0],
                       yahoo_adp=[10.0, 20.0], sleeper_adp=[10.0, 20.0])
    svc = service(frame, {})   # nobody has a board

    off = svc.platform_blend(ScoringFormat.FULL_PPR, "sleeper", 0.0)
    on = svc.platform_blend(ScoringFormat.FULL_PPR, "sleeper", 1.0)
    pd.testing.assert_series_equal(off, on)


def test_two_boards_at_once_each_stay_in_their_own_lane():
    # Both ESPN and Yahoo have boards now. Each must reshape only its own
    # number; neither may leak into the other's share.
    frame = make_frame(ids=["p1", "p2"], espn_adp=[20.0, 30.0],
                       yahoo_adp=[40.0, 50.0], sleeper_adp=[60.0, 70.0])
    boards = {"espn": pd.Series({"p1": 2.0, "p2": 1.0}),
              "yahoo": pd.Series({"p1": 2.0, "p2": 1.0})}

    blended = service(frame, boards).platform_blend(
        ScoringFormat.FULL_PPR, "espn", 1.0, 0.7)

    # At weight 1.0 each platform's number IS its board order over its own
    # values: ESPN p1 -> 30.0, Yahoo p1 -> 50.0. Sleeper has no board.
    expected = 0.7 * 30.0 + 0.15 * 50.0 + 0.15 * 60.0
    assert blended["p1"] == pytest.approx(expected)


# --------------------------------------------------------------------------
# blend_weights
# --------------------------------------------------------------------------

def test_blend_weights_always_sum_to_one():
    # The previous pair of constants could express a set summing to 0.75, which
    # blend_adp then renormalized away silently. Deriving them cannot.
    for platform in ["espn", "yahoo", "sleeper", "fantrax"]:
        for weight in [0.0, 0.33, 0.5, 0.7, 1.0]:
            total = sum(blend_weights(platform, weight).values())
            assert total == pytest.approx(1.0), (platform, weight)


def test_blend_weights_gives_the_drafting_platform_its_share():
    weights = blend_weights("espn", 0.7)
    assert weights["espn"] == pytest.approx(0.7)
    assert weights["yahoo"] == pytest.approx(0.15)
    assert weights["sleeper"] == pytest.approx(0.15)


def test_blend_weights_falls_back_to_an_even_split_off_platform():
    # A Fantrax league has no "own" platform among the three sources.
    weights = blend_weights("fantrax", 0.7)
    for share in weights.values():
        assert share == pytest.approx(1 / 3)


def test_blend_weights_at_one_silences_the_other_platforms():
    weights = blend_weights("yahoo", 1.0)
    assert weights["yahoo"] == pytest.approx(1.0)
    assert weights["espn"] == pytest.approx(0.0)
