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
from draft_model.table import apply_platform_shift, blend_adp, build_table, fill_missing_stdev
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


def test_platform_shift_leaves_unmatched_players_alone():
    # A player the platform has never heard of keeps his FFC value, rather than
    # becoming NaN and poisoning the sampler.
    ffc = pd.Series({"a": 10.0, "missing": 99.0})
    platform = pd.Series({"a": 20.0})
    shifted = apply_platform_shift(ffc, platform, 0.5)
    assert shifted["missing"] == pytest.approx(99.0)


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
