"""Tests for the model input table.

The two things most worth locking in: per-player weight renormalization in
blend_adp (easy to "simplify" into a plain weighted sum, which is wrong), and the
stdev fallback chain (which must never emit a zero, and must never require a
trained model).
"""

import numpy as np
import pandas as pd
import pytest

from draft_model.config import MIN_STDEV, DraftConfig
from draft_model.table import (
    adjust_for_keepers, apply_platform_shift, blend_adp, build_table,
    fill_missing_stdev, fit_to_pick_space, rank_to_pick_scale,
)
from scoring import ScoringFormat


def make_config(**overrides):
    values = dict(year=2026, num_teams=12, num_rounds=15, draft_position=5,
                  scoring_format=ScoringFormat.FULL_PPR)
    values.update(overrides)
    return DraftConfig(**values)


def make_ffc(rows):
    """Minimal FFC-shaped frame. Columns match FfcAdapter.normalize_players."""
    return pd.DataFrame(rows, columns=[
        "ffc_player_id", "canonical_id", "name", "position", "team",
        "adp", "stdev", "high", "low", "times_drafted",
    ])


# --------------------------------------------------------------------------
# blend_adp
# --------------------------------------------------------------------------

def test_blend_adp_weights_are_renormalized_per_player():
    # "solo" appears ONLY in the low-weight source. He must get that source's
    # ADP unchanged -- not scaled down by its fractional weight. This is the
    # bug a plain weighted sum would introduce.
    sources = {
        "espn": pd.Series({"both": 10.0}),
        "sleeper": pd.Series({"both": 20.0, "solo": 50.0}),
    }
    blended = blend_adp(sources, {"espn": 0.8, "sleeper": 0.2})

    assert blended["solo"] == pytest.approx(50.0)
    assert blended["both"] == pytest.approx(0.8 * 10.0 + 0.2 * 20.0)


def test_blend_adp_handles_no_sources():
    assert blend_adp({}, {}).empty


# --------------------------------------------------------------------------
# apply_platform_shift
# --------------------------------------------------------------------------

def test_platform_shift_moves_halfway_by_default():
    ffc = pd.Series({"a": 10.0})
    platform = pd.Series({"a": 20.0})
    assert apply_platform_shift(ffc, platform, 0.5)["a"] == pytest.approx(15.0)


def test_platform_shift_weight_zero_is_pure_ffc():
    ffc = pd.Series({"a": 10.0})
    platform = pd.Series({"a": 20.0})
    assert apply_platform_shift(ffc, platform, 0.0)["a"] == pytest.approx(10.0)


def test_platform_shift_never_leaves_an_unmatched_player_nan():
    # Whatever else happens to a player the platform has never heard of, he must
    # come out as a real number -- a NaN here poisons the sampler.
    ffc = pd.Series({"a": 10.0, "missing": 99.0})
    platform = pd.Series({"a": 20.0})
    assert apply_platform_shift(ffc, platform, 0.5).notna().all()


def test_platform_shift_can_opt_out_of_imputing():
    # The pre-imputation behaviour, kept reachable: an unmatched player stays
    # exactly where FFC put him.
    ffc = pd.Series({"a": 10.0, "missing": 99.0})
    platform = pd.Series({"a": 20.0})
    shifted = apply_platform_shift(ffc, platform, 0.5, impute_missing=False)
    assert shifted["missing"] == pytest.approx(99.0)


def test_platform_shift_imputes_unmatched_from_neighbours():
    # Every measured player is drafted 10 picks later by the platforms, so the
    # unranked one should move by that same typical gap rather than sit still
    # while everyone around him slides. At weight 0.5 that is +5.
    ffc = pd.Series({"a": 10.0, "b": 12.0, "c": 14.0, "missing": 13.0})
    platform = pd.Series({"a": 20.0, "b": 22.0, "c": 24.0})
    shifted = apply_platform_shift(ffc, platform, 0.5)
    assert shifted["missing"] == pytest.approx(18.0)


def test_platform_shift_imputation_uses_the_median_not_the_mean():
    # One source disagreeing wildly about one player must not drag everybody
    # else's imputed gap with it. Gaps are +2, +2, +2 and +200; the median is 2,
    # so at weight 1.0 the unranked player moves by 2 and not by 51.5.
    ffc = pd.Series({"a": 10.0, "b": 11.0, "c": 12.0, "d": 13.0, "missing": 11.5})
    platform = pd.Series({"a": 12.0, "b": 13.0, "c": 14.0, "d": 213.0})
    shifted = apply_platform_shift(ffc, platform, 1.0)
    assert shifted["missing"] == pytest.approx(13.5)


def test_platform_shift_with_no_matched_players_at_all_is_pure_ffc():
    # Nothing to learn a typical gap from, so imputation must decline to invent
    # one rather than producing NaN.
    ffc = pd.Series({"a": 10.0, "b": 20.0})
    platform = pd.Series({"z": 50.0})
    shifted = apply_platform_shift(ffc, platform, 0.75)
    assert shifted["a"] == pytest.approx(10.0)
    assert shifted["b"] == pytest.approx(20.0)


# --------------------------------------------------------------------------
# rank_to_pick_scale
# --------------------------------------------------------------------------

def test_rank_to_pick_scale_gives_the_top_rank_the_earliest_pick():
    # Board order disagrees with ADP order: "c" is the board's favourite even
    # though he has the latest ADP. He must come out on the earliest pick value.
    rank = pd.Series({"a": 2.0, "b": 3.0, "c": 1.0})
    adp = pd.Series({"a": 10.0, "b": 20.0, "c": 30.0})
    out = rank_to_pick_scale(rank, adp)

    assert out["c"] == pytest.approx(10.0)
    assert out["a"] == pytest.approx(20.0)
    assert out["b"] == pytest.approx(30.0)


def test_rank_to_pick_scale_output_is_a_permutation_of_the_reference():
    # The key property: it can only REORDER the board, never stretch or shift
    # its scale. That is what stops it fighting fit_to_pick_space.
    rank = pd.Series({"a": 5.0, "b": 1.0, "c": 9.0, "d": 3.0})
    adp = pd.Series({"a": 4.0, "b": 11.0, "c": 30.0, "d": 62.0})
    out = rank_to_pick_scale(rank, adp)

    assert sorted(out.to_numpy()) == sorted(adp.to_numpy())
    assert out.mean() == pytest.approx(adp.mean())


def test_rank_to_pick_scale_handles_sparse_ranks():
    # Yahoo's ranks are not a dense 1..N -- they run to 2473 across 1,175
    # players. Only the ORDER may matter; the raw magnitudes must not.
    sparse = pd.Series({"a": 7.0, "b": 250.0, "c": 2473.0})
    dense = pd.Series({"a": 1.0, "b": 2.0, "c": 3.0})
    adp = pd.Series({"a": 5.0, "b": 50.0, "c": 150.0})

    pd.testing.assert_series_equal(rank_to_pick_scale(sparse, adp),
                                   rank_to_pick_scale(dense, adp))


def test_rank_to_pick_scale_borrows_real_spacing_not_flat_ranks():
    # Real ADP is packed at the top and spread out deep. A flat 1,2,3 would make
    # early players look far more interchangeable than they are.
    rank = pd.Series({"a": 1.0, "b": 2.0, "c": 3.0})
    adp = pd.Series({"a": 1.0, "b": 2.0, "c": 90.0})
    out = rank_to_pick_scale(rank, adp)
    assert out["c"] - out["b"] == pytest.approx(88.0)


def test_rank_to_pick_scale_survives_an_empty_board():
    assert rank_to_pick_scale(pd.Series(dtype="float64"), pd.Series([1.0])).empty


# --------------------------------------------------------------------------
# adjust_for_keepers
# --------------------------------------------------------------------------

def test_keeper_adjustment_matches_the_chase_example():
    # Chase has ADP 3 and is kept at 3.01, overall pick 25 in a 12-team league.
    # Players between his ADP and his keeper pick move up exactly one; players
    # outside that window do not move at all.
    adp = pd.Series({"early": 2.0, "chase": 3.0, "mid": 10.0,
                     "edge": 24.0, "after": 30.0, "late": 90.0})
    kept = [False, True, False, False, False, False]
    out = adjust_for_keepers(adp, keeper_picks={25: "chase"}, kept=kept)

    assert out["early"] == pytest.approx(2.0)     # ahead of him, unaffected
    assert out["mid"] == pytest.approx(9.0)       # inside the window, up one
    assert out["edge"] == pytest.approx(23.0)     # still inside, up one
    assert out["after"] == pytest.approx(30.0)    # past pick 25, absorbed
    assert out["late"] == pytest.approx(90.0)     # far past, unaffected


def test_keeper_adjustment_handles_a_keeper_held_later_than_his_adp():
    # The reverse case: ADP 50 but kept at pick 1. His pick is consumed early
    # while he is removed from deeper in the pool, so players in between land
    # one slot LATER rather than earlier.
    adp = pd.Series({"kept": 50.0, "between": 20.0, "after": 80.0})
    kept = [True, False, False]
    out = adjust_for_keepers(adp, keeper_picks={1: "kept"}, kept=kept)

    assert out["between"] == pytest.approx(21.0)
    assert out["after"] == pytest.approx(80.0)


def test_keeper_adjustment_accumulates_over_several_keepers():
    # Three keepers all going earlier than this player and all kept late, so he
    # moves up three slots rather than one.
    adp = pd.Series({"k1": 1.0, "k2": 2.0, "k3": 3.0, "player": 40.0})
    kept = [True, True, True, False]
    out = adjust_for_keepers(adp, keeper_picks={100: "k1", 101: "k2", 102: "k3"},
                             kept=kept)
    assert out["player"] == pytest.approx(37.0)


def test_keeper_adjustment_is_a_no_op_for_redraft():
    adp = pd.Series({"a": 5.0, "b": 25.0})
    pd.testing.assert_series_equal(adjust_for_keepers(adp, {}, None), adp)


def test_keeper_adjustment_never_reorders_the_board():
    # Whatever the keepers do, the board's ordering must survive -- the shift is
    # a slot correction, not a re-ranking.
    adp = pd.Series(np.arange(1.0, 61.0))
    kept = [i in (2, 5, 11) for i in range(60)]
    keeper_picks = {40: "a", 41: "b", 42: "c"}
    out = adjust_for_keepers(adp, keeper_picks, kept)
    assert out.is_monotonic_increasing


# --------------------------------------------------------------------------
# fit_to_pick_space
# --------------------------------------------------------------------------

def test_fit_to_pick_space_centres_the_drafted_players():
    # 10 players, 10 picks: a draft hands out 1..10, so the drafted players must
    # average 5.5. These average 11.0, so everything should halve.
    adp = pd.Series([2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0])
    fitted = fit_to_pick_space(adp, total_picks=10)
    assert fitted.mean() == pytest.approx(5.5)
    assert fitted.iloc[0] == pytest.approx(1.0)


def test_fit_to_pick_space_only_averages_the_players_who_get_drafted():
    # 4 picks out of 6 players: the two deepest must not drag the scale, because
    # they never get selected.
    adp = pd.Series([1.0, 2.0, 3.0, 4.0, 500.0, 900.0])
    fitted = fit_to_pick_space(adp, total_picks=4)
    assert fitted.nsmallest(4).mean() == pytest.approx(2.5)


def test_fit_to_pick_space_preserves_order_and_relative_spacing():
    # One shared multiplier, so nobody overtakes anybody and the gaps keep their
    # proportions. Re-ranking the board is the platforms' job, not this one's.
    adp = pd.Series({"a": 3.0, "b": 9.0, "c": 27.0, "d": 81.0})
    fitted = fit_to_pick_space(adp, total_picks=4)
    assert list(fitted.sort_values().index) == ["a", "b", "c", "d"]
    assert fitted["b"] / fitted["a"] == pytest.approx(3.0)
    assert fitted["d"] / fitted["c"] == pytest.approx(3.0)


def test_fit_to_pick_space_excludes_keepers_from_the_scale():
    # 6 picks, but pick 1 is spent on a keeper. So only 5 SELECTIONS happen, at
    # picks 2..6 (mean 4.0), and the kept player must not shape the scale -- he
    # is early-ADP and would drag the average forward.
    adp = pd.Series([1.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    kept = [True, False, False, False, False, False, False]
    fitted = fit_to_pick_space(adp, total_picks=6, keeper_picks={1: "kept-guy"},
                               kept=kept)
    # The 5 competing players are 10..50, averaging 30; they must average 4.0.
    competing = fitted[1:6]
    assert competing.mean() == pytest.approx(4.0)


def test_fit_to_pick_space_ignoring_keepers_would_mis_scale():
    # Guards the regression directly: treating all 6 picks as selections and
    # letting the keeper into the average gives a visibly different factor.
    adp = pd.Series([1.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    kept = [True, False, False, False, False, False, False]
    aware = fit_to_pick_space(adp, total_picks=6, keeper_picks={1: "kept-guy"},
                              kept=kept)
    naive = fit_to_pick_space(adp, total_picks=6)
    assert not np.isclose(aware.iloc[1], naive.iloc[1])


def test_fit_to_pick_space_declines_when_the_pool_is_too_small():
    # Fewer players than picks means the draft cannot fill itself and "who gets
    # drafted" is meaningless. Scaling here once let a 400-ADP player through
    # the pool cap by shrinking him to 180.
    adp = pd.Series([2.0, 400.0])
    pd.testing.assert_series_equal(fit_to_pick_space(adp, total_picks=180), adp)


def test_build_table_makes_the_target_reachable():
    # The end-to-end guarantee: whatever the vendors said, the players this
    # draft will consume average the pick number the draft can actually offer.
    ffc = make_ffc([[i, f"c{i}", f"P{i}", "WR", "LAR", float(i * 3), 5.0,
                     float(i * 3) - 2, float(i * 3) + 2, 50]
                    for i in range(1, 21)])
    config = make_config(num_teams=2, num_rounds=5, draft_position=1)   # 10 picks
    table = build_table(config, ffc)
    assert table["adp_target"].nsmallest(10).mean() == pytest.approx(5.5)


# --------------------------------------------------------------------------
# fill_missing_stdev
# --------------------------------------------------------------------------

def test_stdev_uses_ffc_value_when_present():
    df = pd.DataFrame({"position": ["WR"], "stdev": [7.5],
                       "high": [10], "low": [30], "adp_target": [20.0]})
    assert fill_missing_stdev(df).iloc[0] == pytest.approx(7.5)


def test_stdev_falls_back_to_range_over_four():
    df = pd.DataFrame({"position": ["WR"], "stdev": [np.nan],
                       "high": [10], "low": [30], "adp_target": [20.0]})
    assert fill_missing_stdev(df).iloc[0] == pytest.approx(5.0)   # (30-10)/4


def test_stdev_falls_back_to_same_position_neighbours():
    # The real case: high == low, so BOTH stdev and the range fallback are
    # unusable. Must land on the median of nearby WRs (10.0), NOT the RBs (99.0).
    df = pd.DataFrame({
        "position": ["WR", "WR", "WR", "RB", "RB"],
        "stdev":    [np.nan, 8.0, 12.0, 99.0, 99.0],
        "high":     [50, 40, 45, 40, 45],
        "low":      [50, 60, 65, 60, 65],
        "adp_target": [50.0, 48.0, 52.0, 49.0, 51.0],
    })
    assert fill_missing_stdev(df).iloc[0] == pytest.approx(10.0)


def test_stdev_is_never_zero():
    # A zero width makes a player perfectly deterministic in the sampler.
    df = pd.DataFrame({"position": ["WR"], "stdev": [0.0],
                       "high": [50], "low": [50], "adp_target": [50.0]})
    assert fill_missing_stdev(df).iloc[0] >= MIN_STDEV


# --------------------------------------------------------------------------
# build_table
# --------------------------------------------------------------------------

def test_build_table_orders_by_adp_and_resets_index():
    # Row order IS picks-matrix column order (invariant 1), so it must be
    # deterministic and contiguous from zero.
    ffc = make_ffc([
        [2, "c2", "Later", "WR", "LAR", 50.0, 10.0, 40, 60, 100],
        [1, "c1", "Earlier", "RB", "DET", 2.0, 1.0, 1, 5, 500],
    ])
    table = build_table(make_config(), ffc)

    assert list(table["name"]) == ["Earlier", "Later"]
    assert list(table.index) == [0, 1]


def test_build_table_applies_pool_cap():
    # 12 x 15 = 180 picks, cap = 270. ADP 400 can never be selected.
    ffc = make_ffc([
        [1, "c1", "Keeper", "RB", "DET", 2.0, 1.0, 1, 5, 500],
        [2, "c2", "TooDeep", "WR", "LAR", 400.0, 10.0, 380, 420, 3],
    ])
    table = build_table(make_config(), ffc)
    assert list(table["name"]) == ["Keeper"]


def test_build_table_keeps_players_without_canonical_id():
    # Team defenses never resolve. Dropping them would push skill players
    # artificially later, since defenses really do get drafted.
    ffc = make_ffc([
        [1, "c1", "Some RB", "RB", "DET", 2.0, 1.0, 1, 5, 500],
        [2, None, "NY Giants Defense", "DST", "NYG", 180.0, 20.0, 160, 200, 36],
    ])
    table = build_table(make_config(), ffc)
    assert len(table) == 2
    assert table["canonical_id"].isna().sum() == 1


def test_build_table_mu_sd_start_at_targets():
    ffc = make_ffc([[1, "c1", "Player", "RB", "DET", 12.0, 3.0, 8, 18, 200]])
    table = build_table(make_config(), ffc)
    assert table["mu"].iloc[0] == pytest.approx(table["adp_target"].iloc[0])
    assert table["sd"].iloc[0] == pytest.approx(table["stdev_target"].iloc[0])


def test_build_table_attaches_enrichments_without_filtering():
    ffc = make_ffc([
        [1, "c1", "Has Projection", "RB", "DET", 2.0, 1.0, 1, 5, 500],
        [2, "c2", "No Projection", "WR", "LAR", 20.0, 5.0, 15, 25, 300],
    ])
    table = build_table(make_config(), ffc,
                        enrichments={"projection": pd.Series({"c1": 280.5})})

    assert len(table) == 2                                   # nothing dropped
    assert table.loc[0, "projection"] == pytest.approx(280.5)
    assert np.isnan(table.loc[1, "projection"])


def test_build_table_raises_on_missing_adp():
    # A NaN here becomes a NaN board value, which sorts unpredictably and
    # produces a plausible-looking wrong draft. Fail loudly instead.
    ffc = make_ffc([[1, "c1", "Broken", "RB", "DET", np.nan, 3.0, 8, 18, 200]])
    with pytest.raises(ValueError, match="adp_target"):
        build_table(make_config(), ffc)


def test_build_table_raises_on_missing_columns():
    with pytest.raises(ValueError, match="missing required columns"):
        build_table(make_config(), pd.DataFrame({"name": ["x"]}))
