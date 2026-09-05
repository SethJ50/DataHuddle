"""Tests for the ADP Analysis table.

The thing most worth locking in is that keepers are handled the way a RANK needs
rather than the way an ADP needs. A kept player is not in the field, so the
players behind him move up -- but a rank has no picks in it, so none of the
pick-space arithmetic that `adjust_for_keepers` does applies here.
"""

import numpy as np
import pandas as pd
import pytest

from presentation.adp_analysis_view import (
    DIFF_COLUMNS, diff_scale, shape, style, tint,
)
from services.adp_analysis_service import rank_available


# --------------------------------------------------------------------------
# rank_available
# --------------------------------------------------------------------------

def test_rank_available_ranks_everyone_in_a_redraft_league():
    values = pd.Series({"a": 30.0, "b": 10.0, "c": 20.0})
    ranked = rank_available(values, None)
    assert ranked["b"] == 1
    assert ranked["c"] == 2
    assert ranked["a"] == 3


def test_rank_available_moves_players_up_past_a_keeper():
    # "b" is the second-best player and is kept, so "c" -- third on the board --
    # becomes the second-best player anyone can actually draft.
    values = pd.Series({"a": 10.0, "b": 20.0, "c": 30.0})
    ranked = rank_available(values, [False, True, False])

    assert ranked["a"] == 1
    assert ranked["c"] == 2
    assert np.isnan(ranked["b"])


def test_rank_available_does_not_modify_the_caller_column():
    values = pd.Series({"a": 10.0, "b": 20.0})
    rank_available(values, [False, True])
    assert values["b"] == pytest.approx(20.0)


def test_rank_available_leaves_missing_values_unranked():
    values = pd.Series({"a": 10.0, "b": np.nan})
    assert np.isnan(rank_available(values, None)["b"])


def test_rank_available_ties_share_a_rank():
    values = pd.Series({"a": 5.0, "b": 5.0, "c": 9.0})
    ranked = rank_available(values, None)
    assert ranked["a"] == ranked["b"] == 1
    assert ranked["c"] == 3


# --------------------------------------------------------------------------
# shape
# --------------------------------------------------------------------------

def make_analysis(**overrides):
    """A minimal AdpAnalysisService.build()-shaped frame."""
    base = {
        "name": ["Gibbs", "Nacua", "Chase", "Kicker Guy"],
        "position": ["RB", "WR", "WR", "K"],
        "is_kept": [False, False, True, False],
        "sim_adp": [1.5, 3.0, np.nan, 140.0],
        "plat_adp_adj": [1.6, 3.2, 4.0, np.nan],
        "plat_adp_rank_adj": [1.0, 2.0, np.nan, np.nan],
        "blend_adp_adj": [1.6, 3.3, 4.0, np.nan],
        "blend_adp_rank_adj": [1.0, 2.0, np.nan, np.nan],
        "platform_rank_adj": [1.0, 3.0, np.nan, np.nan],
        "sim_adp_rank": [1.0, 2.0, np.nan, 100.0],
        "draft_rate": [1.0, 1.0, 0.0, 0.006],
        "target_adp": [1.6, 3.1, 4.0, 138.0],
        "target_adp_rank": [1.0, 2.0, np.nan, 100.0],
        "ffb_rank_adj": [1.0, 4.0, np.nan, np.nan],
        "diff_market_vs_board": [0.0, -1.0, np.nan, np.nan],
        "diff_sim_vs_board": [0.0, -1.0, np.nan, np.nan],
        "diff_sim_vs_target": [-0.1, -0.1, np.nan, 2.0],
        "diff_market_vs_ffb": [0.0, -2.0, np.nan, np.nan],
        "diff_sim_vs_ffb": [0.0, -2.0, np.nan, np.nan],
        "diff_board_vs_ffb": [0.0, -1.0, np.nan, np.nan],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_shape_hides_kept_players():
    # Nobody can draft them, so every rank and simulated column is blank -- the
    # row would be noise rather than information.
    out = shape(make_analysis())
    assert "Chase" not in list(out["Player"])
    assert len(out) == 3


def test_shape_filters_by_position():
    out = shape(make_analysis(), position="WR")
    assert list(out["Player"]) == ["Nacua"]


def test_shape_search_ignores_capitalisation():
    assert list(shape(make_analysis(), search="gibbs")["Player"]) == ["Gibbs"]


def test_shape_sorts_by_simulated_adp_with_undrafted_last():
    out = shape(make_analysis(sim_adp=[9.0, 3.0, np.nan, np.nan]))
    assert list(out["Player"])[:2] == ["Nacua", "Gibbs"]
    assert list(out["Player"])[-1] == "Kicker Guy"


def test_shape_of_an_empty_table_still_has_the_headings():
    # The page indexes these columns unconditionally, so they must exist even
    # when there is nothing to show.
    out = shape(pd.DataFrame())
    assert list(out.columns)[:2] == ["Player", "Pos"]
    for column in DIFF_COLUMNS:
        assert column in out.columns


# --------------------------------------------------------------------------
# colouring
# --------------------------------------------------------------------------

def test_diff_scale_ignores_a_single_extreme_player():
    # One player 500 picks out must not wash the colour out for everybody else.
    ordinary = pd.Series(list(range(1, 20)) + [500.0])
    assert diff_scale(ordinary) < 100


def test_diff_scale_survives_an_all_missing_column():
    assert diff_scale(pd.Series([np.nan, np.nan])) == pytest.approx(1.0)


def test_tint_is_green_for_value_and_red_for_a_reach():
    assert "34, 160, 90" in tint(10.0, 10.0)     # later than rated -> value
    assert "208, 66, 66" in tint(-10.0, 10.0)    # earlier than rated -> reach


def test_tint_strength_grows_with_the_difference():
    def alpha(css):
        return float(css.rsplit(",", 1)[1].strip(" )"))

    assert alpha(tint(2.0, 10.0)) < alpha(tint(8.0, 10.0))


def test_tint_is_capped_so_text_stays_readable():
    def alpha(css):
        return float(css.rsplit(",", 1)[1].strip(" )"))

    assert alpha(tint(10_000.0, 10.0)) <= 0.55


def test_tint_leaves_missing_values_uncoloured():
    assert tint(np.nan, 10.0) == ""
    assert tint(None, 10.0) == ""


def test_style_colours_only_the_difference_columns():
    styled = style(shape(make_analysis()))
    css = styled.to_html()
    assert "background-color" in css

    # The rendered CSS must not touch the plain data columns.
    painted = styled._compute().ctx
    columns = list(styled.data.columns)
    for (_, col), styles in painted.items():
        if styles:
            assert columns[col] in DIFF_COLUMNS


def test_style_of_an_empty_table_returns_the_frame():
    empty = shape(pd.DataFrame())
    assert isinstance(style(empty), pd.DataFrame)


# --------------------------------------------------------------------------
# the naming convention itself
# --------------------------------------------------------------------------

def test_every_diff_heading_matches_the_column_it_is_built_from():
    # Every heading reads "A vs B" and the column underneath holds A minus B.
    # One heading was briefly written the other way round, which silently
    # inverts how a reader interprets the sign and the colour -- green would
    # read as a reach instead of value. Lock the convention down.
    from presentation.adp_analysis_view import DISPLAY_COLUMNS

    shorthand = {"mkt": "market", "tgt": "target", "sim": "sim",
                 "board": "board", "ffb": "ffb"}

    for internal, heading in DISPLAY_COLUMNS.items():
        if heading not in DIFF_COLUMNS:
            continue
        left, right = (part.strip().lower() for part in heading.split(" vs "))
        expected = f"diff_{shorthand[left]}_vs_{shorthand[right]}"
        assert internal == expected, (
            f"heading {heading!r} does not describe {internal!r}"
        )
