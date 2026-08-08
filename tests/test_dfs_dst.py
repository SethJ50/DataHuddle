"""Tests for scoring team defences.

A defence is scored on something no player table records, so this is the one
place in the app that builds a fantasy score from scratch. Two things carry the
weight:

1. THE POINTS-ALLOWED TIERS, which are most of a defensive score and are written
   as upper bounds -- so the order they are applied in decides whether a shutout
   pays 10 or 1.
2. POINTS ALLOWED IS THE OPPONENT'S SCORE. It is in no team-stats table, and
   taking a team's own score instead would rank the best offences as the best
   defences.
"""

import numpy as np
import pandas as pd
import pytest

from services.dfs_dst_service import (
    BLOWOUT_POINTS, EVENT_POINTS, POINTS_ALLOWED_TIERS, dst_weeks,
    points_allowed_score,
)


class FakeRepo:
    def __init__(self, stats, schedules):
        self._stats, self._schedules = stats, schedules

    def team_stats(self):
        return self._stats

    def schedules(self):
        return self._schedules


def stats_frame(rows):
    frame = pd.DataFrame(rows)
    for column, default in (("season", 2024), ("week", 1), ("team", "SEA"),
                            ("opponent_team", "SF"), ("def_sacks", 0),
                            ("def_interceptions", 0), ("fumble_recovery_opp", 0),
                            ("def_tds", 0), ("def_safeties", 0),
                            ("special_teams_tds", 0)):
        if column not in frame.columns:
            frame[column] = default
        else:
            frame[column] = frame[column].fillna(default)
    return frame


def schedule_frame(home="SEA", away="SF", home_score=17, away_score=10):
    return pd.DataFrame([{"season": 2024, "week": 1, "home_team": home,
                          "away_team": away, "home_score": home_score,
                          "away_score": away_score}])


# ---------------------------------------------------------------------------
# The tier table
# ---------------------------------------------------------------------------


def test_a_shutout_pays_the_most():
    assert points_allowed_score(pd.Series([0])).iloc[0] == 10.0


def test_each_band_pays_what_it_says():
    for ceiling, payment in POINTS_ALLOWED_TIERS:
        assert points_allowed_score(pd.Series([ceiling])).iloc[0] == payment


def test_the_bands_are_upper_bounds_not_exact_values():
    # THE ORDERING TRAP. Applied the other way round, the loosest band would
    # overwrite every stricter one and a shutout would pay the same as a
    # twenty-point game.
    assert points_allowed_score(pd.Series([3])).iloc[0] == 7.0    # in the 1-6 band
    assert points_allowed_score(pd.Series([17])).iloc[0] == 1.0   # in the 14-20
    assert points_allowed_score(pd.Series([0])).iloc[0] == 10.0   # still 10


def test_a_blowout_costs_points():
    beyond = POINTS_ALLOWED_TIERS[-1][0] + 1
    assert points_allowed_score(pd.Series([beyond])).iloc[0] == BLOWOUT_POINTS
    assert points_allowed_score(pd.Series([70])).iloc[0] == BLOWOUT_POINTS


def test_an_unplayed_game_scores_nothing_rather_than_a_blowout():
    # A game with no score yet must not read as having conceded zero, which
    # would pay 10, nor as a blowout.
    assert np.isnan(points_allowed_score(pd.Series([np.nan])).iloc[0])


# ---------------------------------------------------------------------------
# The full score
# ---------------------------------------------------------------------------


def test_points_allowed_is_the_other_team_s_score():
    # In no team-stats table anywhere. Taking a team's OWN score would rank the
    # best offences as the best defences.
    repo = FakeRepo(stats_frame([{"team": "SEA"}]),
                    schedule_frame(home="SEA", away="SF",
                                   home_score=31, away_score=6))
    row = dst_weeks(repo).iloc[0]
    assert row["points_allowed"] == 6          # what SEA's defence conceded


def test_each_event_pays_its_own_rate():
    repo = FakeRepo(
        stats_frame([{"def_sacks": 4, "def_interceptions": 2,
                      "fumble_recovery_opp": 1, "def_tds": 1,
                      "def_safeties": 1}]),
        schedule_frame(home_score=17, away_score=24),      # SEA allowed 24
    )
    row = dst_weeks(repo).iloc[0]
    events = 4 * 1 + 2 * 2 + 1 * 2 + 1 * 6 + 1 * 2         # = 18
    assert row["total_fantasy_points"] == pytest.approx(events + 0.0)  # 24 -> 0


def test_a_recovered_fumble_pays_but_a_forced_one_does_not():
    # Forcing a fumble the other side falls on scores nothing, and
    # `def_fumbles_forced` counts those too.
    assert "fumble_recovery_opp" in EVENT_POINTS
    assert "def_fumbles_forced" not in EVENT_POINTS


def test_return_touchdowns_count_as_well_as_defensive_ones():
    repo = FakeRepo(stats_frame([{"special_teams_tds": 1}]),
                    schedule_frame(home_score=17, away_score=24))
    assert dst_weeks(repo).iloc[0]["total_fantasy_points"] == pytest.approx(6.0)


def test_the_opponent_is_carried_through():
    repo = FakeRepo(stats_frame([{"team": "SEA", "opponent_team": "SF"}]),
                    schedule_frame())
    assert dst_weeks(repo).iloc[0]["opponent"] == "SF"


def test_the_points_column_is_named_like_every_player_row():
    # So a page can read a defence and a receiver the same way.
    repo = FakeRepo(stats_frame([{}]), schedule_frame())
    assert "total_fantasy_points" in dst_weeks(repo).columns


def test_a_missing_source_gives_an_empty_table_rather_than_raising():
    empty = dst_weeks(FakeRepo(pd.DataFrame(), pd.DataFrame()))
    assert empty.empty
    assert "total_fantasy_points" in empty.columns


def test_a_schedule_with_no_scores_leaves_points_allowed_blank():
    games = schedule_frame().drop(columns=["home_score", "away_score"])
    repo = FakeRepo(stats_frame([{"def_sacks": 3}]), games)
    assert np.isnan(dst_weeks(repo).iloc[0]["points_allowed"])
