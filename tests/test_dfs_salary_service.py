"""Tests for pairing a slate's prices with each player's recent form.

A salary file describes a week that has not been played, so there is nothing to
join it to on the week. The join is on the player, and what it brings back is his
recent form. Three things there are easy to get wrong and hard to notice:

1. FORM MUST STOP BEFORE THE SLATE. Once a week has actually been played,
   including it puts the answer inside the form used to predict it.
2. FORM CROSSES THE OFFSEASON, because in week one there is nothing else -- and
   the pages have to be able to say so.
3. EVERY PRICED PLAYER SURVIVES THE JOIN. Defences, unmatched names and rookies
   all arrive with nothing to attach, and dropping them empties the sheet of
   exactly the rows it exists to list.
"""

import numpy as np
import pandas as pd
import pytest

from services.dfs_salary_service import (
    VALUE_PER, slate_board, trailing_form,
)


def weeks_frame(rows):
    """A `player_weeks`-shaped table, filled out around what each row states."""
    frame = pd.DataFrame(rows)
    defaults = {"canonical_id": "P1", "name": "A", "position": "WR",
                "team": "SEA", "opponent": "SF", "season": 2025, "week": 1,
                "total_fantasy_points": 10.0, "total_fantasy_points_exp": 9.0,
                "snap_share": 0.8, "target_share": 0.2}
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
        else:
            frame[column] = frame[column].fillna(value)
    return frame


def salary_frame(rows):
    """A stored-slate-shaped table.

    `canonical_id` is deliberately NOT filled in when a row states it as None.
    A blank id is the meaningful case here -- defences and unmatched names -- so
    filling it would quietly turn those tests into the ordinary one.
    """
    frame = pd.DataFrame(rows)
    defaults = {"site": "FanDuel", "season": 2026, "week": 1,
                "site_player_id": "1", "name": "A", "canonical_id": "P1",
                "position": "WR", "salary": 5000, "team": "SEA",
                "opponent": "SF"}
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
        elif column != "canonical_id":
            frame[column] = frame[column].fillna(value)
    return frame


# ---------------------------------------------------------------------------
# Trailing form
# ---------------------------------------------------------------------------


def test_form_averages_the_most_recent_games():
    frame = weeks_frame([
        {"week": 1, "total_fantasy_points": 5.0},
        {"week": 2, "total_fantasy_points": 15.0},
        {"week": 3, "total_fantasy_points": 25.0},
    ])
    form = trailing_form(frame, 2025, 4, games=2).set_index("canonical_id")
    assert form.loc["P1", "form_points"] == pytest.approx(20.0)   # weeks 3, 2
    assert form.loc["P1", "form_games"] == 2


def test_form_never_includes_the_slate_week_itself():
    # THE ONE THAT MATTERS. For a week already played, including it would put
    # the result inside the form used to predict it -- and the numbers would
    # look wonderful.
    frame = weeks_frame([
        {"week": 1, "total_fantasy_points": 10.0},
        {"week": 2, "total_fantasy_points": 99.0},     # the slate week
    ])
    form = trailing_form(frame, 2025, 2).set_index("canonical_id")
    assert form.loc["P1", "form_points"] == pytest.approx(10.0)


def test_form_reaches_back_into_the_previous_season():
    # In week one there is nothing else to look at.
    frame = weeks_frame([
        {"season": 2025, "week": 17, "total_fantasy_points": 12.0},
        {"season": 2025, "week": 18, "total_fantasy_points": 18.0},
    ])
    form = trailing_form(frame, 2026, 1, games=2).set_index("canonical_id")
    assert form.loc["P1", "form_games"] == 2
    assert form.loc["P1", "form_points"] == pytest.approx(15.0)


def test_form_says_how_far_back_it_had_to_reach():
    # So a page can warn that a role which changed over the offseason will not
    # show yet.
    frame = weeks_frame([{"season": 2025, "week": 18}])
    form = trailing_form(frame, 2026, 1).set_index("canonical_id")
    assert form.loc["P1", "form_seasons_back"] == 1

    within = weeks_frame([{"season": 2026, "week": 1}])
    assert trailing_form(within, 2026, 2).set_index(
        "canonical_id").loc["P1", "form_seasons_back"] == 0


def test_weeks_are_ordered_across_a_season_boundary():
    # Week 18 of one season comes BEFORE week 1 of the next. Sorting on the week
    # alone would put them the other way round.
    frame = weeks_frame([
        {"season": 2025, "week": 18, "total_fantasy_points": 4.0},
        {"season": 2026, "week": 1, "total_fantasy_points": 30.0},
    ])
    form = trailing_form(frame, 2026, 2, games=1).set_index("canonical_id")
    assert form.loc["P1", "form_points"] == pytest.approx(30.0)


def test_a_player_with_no_earlier_games_is_absent():
    frame = weeks_frame([{"season": 2026, "week": 5}])
    assert trailing_form(frame, 2026, 1).empty


def test_form_for_an_empty_table_still_has_its_columns():
    empty = trailing_form(pd.DataFrame(), 2026, 1)
    assert empty.empty
    assert "form_points" in empty.columns


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------


def test_prices_are_paired_with_form():
    board = slate_board(salary_frame([{"salary": 5000}]),
                        weeks_frame([{"week": 1, "total_fantasy_points": 10.0}]),
                        2026, 1, "FanDuel")
    assert board["form_points"].iloc[0] == pytest.approx(10.0)


def test_value_is_points_per_thousand_dollars():
    # The only way to compare a $9,100 back with a $4,200 receiver.
    board = slate_board(salary_frame([{"salary": 5000}]),
                        weeks_frame([{"week": 1, "total_fantasy_points": 15.0}]),
                        2026, 1, "FanDuel")
    assert board["value_per_1k"].iloc[0] == pytest.approx(15.0 / (5000 / VALUE_PER))


def test_only_the_chosen_site_is_shown():
    prices = pd.concat([salary_frame([{"site": "FanDuel", "salary": 5000}]),
                        salary_frame([{"site": "DraftKings", "salary": 6000}])])
    board = slate_board(prices, weeks_frame([{"week": 1}]), 2026, 1, "DraftKings")
    assert len(board) == 1
    assert board["salary"].iloc[0] == 6000


def test_a_player_with_no_form_keeps_his_price():
    # A rookie, an unmatched name or a defence. Dropping them would empty the
    # sheet of exactly the rows it exists to list.
    board = slate_board(salary_frame([{"canonical_id": None, "name": "Lions",
                                       "position": "DST", "salary": 3600}]),
                        weeks_frame([{"week": 1}]), 2026, 1, "FanDuel")
    assert len(board) == 1
    assert board["salary"].iloc[0] == 3600
    assert np.isnan(board["form_points"].iloc[0])


def test_a_player_with_no_form_has_no_value_either():
    # Rather than a zero, which would sort him alongside genuinely worthless
    # players instead of alongside the unknown ones.
    board = slate_board(salary_frame([{"canonical_id": None, "salary": 3600}]),
                        weeks_frame([{"week": 1}]), 2026, 1, "FanDuel")
    assert np.isnan(board["value_per_1k"].iloc[0])


def test_the_board_is_sorted_dearest_first():
    prices = pd.concat([salary_frame([{"site_player_id": "1", "salary": 4000}]),
                        salary_frame([{"site_player_id": "2", "salary": 9000}])])
    board = slate_board(prices, weeks_frame([{"week": 1}]), 2026, 1, "FanDuel")
    assert list(board["salary"]) == [9000, 4000]


def test_an_empty_slate_comes_back_with_its_columns():
    board = slate_board(salary_frame([{"site": "FanDuel"}]),
                        weeks_frame([{"week": 1}]), 2026, 1, "DraftKings")
    assert board.empty
    assert "value_per_1k" in board.columns


# ---------------------------------------------------------------------------
# Recent history
# ---------------------------------------------------------------------------

def test_history_lays_games_out_most_recent_first():
    from services.dfs_salary_service import recent_history
    frame = weeks_frame([
        {"week": 1, "total_fantasy_points": 4.0},
        {"week": 2, "total_fantasy_points": 8.0},
        {"week": 3, "total_fantasy_points": 12.0},
    ])
    row = recent_history(frame, 2025, 4, games=3).iloc[0]
    assert row["L1"] == 12.0          # his last game
    assert row["L3"] == 4.0           # three games ago


def test_history_columns_are_numbered_not_named_by_week():
    # Byes and missed games mean two players' week 14s are not comparable
    # positions in a list, whereas "his last game" always is.
    from services.dfs_salary_service import recent_history
    early = weeks_frame([{"canonical_id": "A", "week": 1,
                          "total_fantasy_points": 9.0}])
    late = weeks_frame([{"canonical_id": "B", "week": 8,
                         "total_fantasy_points": 9.0}])
    both = recent_history(pd.concat([early, late]), 2025, 10, games=3)
    assert both.set_index("canonical_id").loc["A", "L1"] == 9.0
    assert both.set_index("canonical_id").loc["B", "L1"] == 9.0


def test_history_carries_the_week_and_opponent_for_a_tooltip():
    from services.dfs_salary_service import recent_history
    frame = weeks_frame([{"week": 7, "total_fantasy_points": 11.0}])
    row = recent_history(frame, 2025, 8, games=3).iloc[0]
    assert "7" in row["L1_note"] and "SF" in row["L1_note"]


def test_history_stops_before_the_slate_week():
    from services.dfs_salary_service import recent_history
    frame = weeks_frame([
        {"week": 1, "total_fantasy_points": 5.0},
        {"week": 2, "total_fantasy_points": 99.0},
    ])
    assert recent_history(frame, 2025, 2, games=3).iloc[0]["L1"] == 5.0


def test_history_headers_are_whole_numbers():
    # The rank is a float by default, which turns the headers into "L1.0" and
    # silently breaks every lookup that expects "L1".
    from services.dfs_salary_service import recent_history
    frame = weeks_frame([{"week": 1}])
    assert "L1" in recent_history(frame, 2025, 2, games=3).columns


def test_a_player_with_fewer_games_has_blanks_on_the_right():
    from services.dfs_salary_service import recent_history
    frame = weeks_frame([{"week": 1, "total_fantasy_points": 6.0}])
    row = recent_history(frame, 2025, 5, games=3).iloc[0]
    assert row["L1"] == 6.0
    assert np.isnan(row.get("L2", np.nan))


# ---------------------------------------------------------------------------
# Every measurement averaged, not only the named few
# ---------------------------------------------------------------------------

def test_any_numeric_column_is_averaged_under_a_form_prefix():
    # So a statistic added upstream becomes available here with no change.
    frame = weeks_frame([{"week": 1, "carries": 10}, {"week": 2, "carries": 20}])
    form = trailing_form(frame, 2025, 3, games=2)
    assert form["form_carries"].iloc[0] == pytest.approx(15.0)


def test_the_named_averages_are_not_duplicated_by_the_generic_ones():
    # Both would land on `form_snap_share`, and pandas would suffix them into
    # `form_snap_share_x` and `_y` -- two columns holding the same number.
    form = trailing_form(weeks_frame([{"week": 1}]), 2025, 2)
    assert not [c for c in form.columns if c.endswith(("_x", "_y"))]


def test_keys_are_not_averaged_as_though_they_were_measurements():
    form = trailing_form(weeks_frame([{"week": 1}]), 2025, 2)
    assert "form_week" not in form.columns
    assert "form_season" not in form.columns
