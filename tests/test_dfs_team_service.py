"""Tests for the offensive-tendency numbers built from play-by-play.

Three things here are judgement rather than arithmetic, and each is the kind of
mistake that produces a plausible-looking wrong answer:

1. WHAT COUNTS AS NEUTRAL. Include the fourth quarter and every trailing team
   looks pass-happy; include blowouts and every winning team looks run-heavy.
   The filter is the measurement.
2. NaN RATHER THAN ZERO for a team with no qualifying plays. Zero would put a
   team that only played blowouts bottom of the league for pass rate.
3. PACE IS READ WITHIN A DRIVE. Measuring across a change of possession would
   count the punt, the return and the huddle as one team's tempo.
"""

import numpy as np
import pandas as pd
import pytest

from services.dfs_team_service import (
    MAX_SECONDS_BETWEEN_PLAYS, NEUTRAL_LAST_QUARTER, NEUTRAL_WIN_PROBABILITY,
    neutral_plays, neutral_script_description, offensive_tendencies,
)


class FakeRepo:
    """Hands back one fixed play-by-play table, as `DfsReadRepo` would."""

    def __init__(self, frame):
        self._frame = frame

    def pbp(self):
        return self._frame


def plays(rows):
    """Build a play-by-play frame from a few readable rows.

    Fills in a neutral, ordinary situation around whatever each row states, so a
    test only has to say the part it cares about.
    """
    frame = pd.DataFrame(rows)
    defaults = {
        "season": 2024, "week": 1, "game_id": "G1", "posteam": "SEA",
        "defteam": "SF", "drive": 1, "qtr": 1, "wp": 0.5,
        "play_type": "pass", "pass": 1.0, "xpass": 0.5,
        "two_point_attempt": 0.0, "yardline_100": 50.0,
        "game_seconds_remaining": 3600.0,
    }
    # Fill per VALUE, not per column. Filling only wholly-absent columns leaves
    # a blank wherever one row states a field and another does not, and a blank
    # game id silently drops that row from every join.
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
        else:
            frame[column] = frame[column].fillna(value)
    if "play_id" not in frame.columns:
        frame["play_id"] = range(1, len(frame) + 1)
    return frame


def repo_of(rows):
    """A fake repository over the plays described."""
    return FakeRepo(plays(rows))


# ---------------------------------------------------------------------------
# What counts as neutral
# ---------------------------------------------------------------------------


def test_only_runs_and_passes_count():
    # Kicks and punts are not play-calling in the same sense, and nflreadpy
    # files kneels and spikes under their own types -- so this one test covers
    # clock-killing plays too.
    frame = plays([
        {"play_type": "pass"}, {"play_type": "run"}, {"play_type": "punt"},
        {"play_type": "field_goal"}, {"play_type": "kickoff"},
        {"play_type": "qb_kneel"}, {"play_type": "qb_spike"},
        {"play_type": "no_play"},
    ])
    assert sorted(neutral_plays(frame)["play_type"]) == ["pass", "run"]


def test_blowouts_are_excluded_at_both_ends():
    low, high = NEUTRAL_WIN_PROBABILITY
    frame = plays([
        {"wp": 0.5}, {"wp": low - 0.01}, {"wp": high + 0.01},
        {"wp": low}, {"wp": high},
    ])
    # The band's own edges count; beyond them does not.
    assert len(neutral_plays(frame)) == 3


def test_the_fourth_quarter_is_excluded():
    # Play-calling there is driven by the score even when the win probability
    # still looks close, because there is no time left to recover from being
    # wrong.
    frame = plays([{"qtr": q} for q in (1, 2, 3, 4, 5)])
    assert list(neutral_plays(frame)["qtr"]) == [1, 2, 3][:NEUTRAL_LAST_QUARTER]


def test_two_point_conversions_do_not_count_as_pass_attempts():
    # Neither a normal down nor a normal distance, so including them would
    # inflate pass rate for teams that score a lot.
    frame = plays([{"two_point_attempt": 0.0}, {"two_point_attempt": 1.0}])
    assert len(neutral_plays(frame)) == 1


def test_the_description_is_built_from_the_rules_themselves():
    # So the caption on the page cannot drift away from what the filter does,
    # which would be worse than showing no caption.
    text = neutral_script_description()
    low, high = NEUTRAL_WIN_PROBABILITY
    assert f"{low:.0%}" in text and f"{high:.0%}" in text
    assert str(NEUTRAL_LAST_QUARTER) in text


# ---------------------------------------------------------------------------
# Pass rate and pass rate over expected
# ---------------------------------------------------------------------------


def test_pass_rate_is_the_share_of_neutral_plays_that_were_passes():
    frame = repo_of([
        {"play_type": "pass", "pass": 1.0}, {"play_type": "pass", "pass": 1.0},
        {"play_type": "pass", "pass": 1.0}, {"play_type": "run", "pass": 0.0},
    ])
    assert offensive_tendencies(frame, 2024)["pass_rate"].iloc[0] == 0.75


def test_proe_is_the_gap_from_the_model_in_percentage_points():
    # Threw on 75% of plays where a typical team would have thrown on 50%.
    frame = repo_of([
        {"play_type": "pass", "pass": 1.0, "xpass": 0.5},
        {"play_type": "pass", "pass": 1.0, "xpass": 0.5},
        {"play_type": "pass", "pass": 1.0, "xpass": 0.5},
        {"play_type": "run", "pass": 0.0, "xpass": 0.5},
    ])
    assert offensive_tendencies(frame, 2024)["proe"].iloc[0] == pytest.approx(25.0)


def test_a_team_matching_the_model_scores_zero_not_something_near_it():
    frame = repo_of([
        {"play_type": "pass", "pass": 1.0, "xpass": 0.5},
        {"play_type": "run", "pass": 0.0, "xpass": 0.5},
    ])
    assert offensive_tendencies(frame, 2024)["proe"].iloc[0] == pytest.approx(0.0)


def test_running_more_than_expected_gives_a_negative_score():
    frame = repo_of([
        {"play_type": "run", "pass": 0.0, "xpass": 0.6},
        {"play_type": "run", "pass": 0.0, "xpass": 0.6},
    ])
    assert offensive_tendencies(frame, 2024)["proe"].iloc[0] == pytest.approx(-60.0)


# ---------------------------------------------------------------------------
# Teams with nothing to measure
# ---------------------------------------------------------------------------


def test_a_team_with_no_neutral_plays_keeps_its_row_but_scores_nothing():
    # Every snap in a blowout. They genuinely have no neutral pass rate, and
    # calling it zero would rank them last for having played one-sided games.
    frame = repo_of([
        {"posteam": "SEA", "wp": 0.95, "play_type": "pass"},
        {"posteam": "SEA", "wp": 0.97, "play_type": "run"},
        {"posteam": "SF", "wp": 0.5, "play_type": "pass"},
    ])
    table = offensive_tendencies(frame, 2024).set_index("team")

    assert "SEA" in table.index                       # still there
    assert np.isnan(table.loc["SEA", "pass_rate"])
    assert np.isnan(table.loc["SEA", "proe"])
    assert table.loc["SEA", "plays_per_game"] == 2.0  # volume still counted
    assert table.loc["SF", "pass_rate"] == 1.0


def test_a_season_with_no_data_returns_an_empty_table_with_its_columns():
    table = offensive_tendencies(repo_of([{"play_type": "pass"}]), 1999)
    assert table.empty
    assert "proe" in table.columns


def test_plays_with_no_offence_are_ignored():
    # Timeouts and end-of-quarter markers have no possessing team.
    frame = plays([{"posteam": "SEA"}, {"posteam": None}])
    assert len(offensive_tendencies(FakeRepo(frame), 2024)) == 1


# ---------------------------------------------------------------------------
# Volume, which counts every situation rather than only neutral ones
# ---------------------------------------------------------------------------


def test_plays_per_game_counts_every_situation_not_just_neutral_ones():
    # It measures how much football a team plays. A team trailing all year
    # really does run more plays, and that is worth seeing.
    frame = repo_of([
        {"wp": 0.5, "play_type": "pass"},
        {"wp": 0.95, "play_type": "run"},     # not neutral, still a play
        {"wp": 0.02, "play_type": "run"},     # nor this
    ])
    table = offensive_tendencies(frame, 2024)
    assert table["plays_per_game"].iloc[0] == 3.0
    assert table["neutral_plays"].iloc[0] == 1


def test_games_are_counted_once_however_many_plays_they_hold():
    frame = repo_of([
        {"game_id": "G1"}, {"game_id": "G1"}, {"game_id": "G1"},
        {"game_id": "G2"},
    ])
    assert offensive_tendencies(frame, 2024)["games"].iloc[0] == 2


def test_a_red_zone_trip_is_counted_once_per_drive():
    # A drive with four snaps inside the twenty is one trip, not four.
    frame = repo_of([
        {"drive": 1, "yardline_100": 30.0}, {"drive": 1, "yardline_100": 18.0},
        {"drive": 1, "yardline_100": 12.0}, {"drive": 1, "yardline_100": 4.0},
        {"drive": 2, "yardline_100": 40.0},          # never got there
    ])
    assert offensive_tendencies(frame, 2024)["red_zone_trips_per_game"].iloc[0] == 1.0


def test_drive_numbers_restart_each_game_so_the_game_is_part_of_the_grouping():
    # Both games have a "drive 1". Counting them together would merge two trips
    # into one.
    frame = repo_of([
        {"game_id": "G1", "drive": 1, "yardline_100": 10.0},
        {"game_id": "G2", "drive": 1, "yardline_100": 10.0},
    ])
    table = offensive_tendencies(frame, 2024)
    assert table["red_zone_trips_per_game"].iloc[0] == 1.0    # 2 trips, 2 games


# ---------------------------------------------------------------------------
# Pace
# ---------------------------------------------------------------------------


def test_pace_is_the_time_between_one_snap_and_the_next():
    frame = repo_of([
        {"play_id": 1, "game_seconds_remaining": 900.0},
        {"play_id": 2, "game_seconds_remaining": 870.0},   # 30s
        {"play_id": 3, "game_seconds_remaining": 850.0},   # 20s
        {"play_id": 4, "game_seconds_remaining": 830.0},   # 20s, then no next
    ])
    # The last play of a drive has nothing to measure against, so three gaps.
    assert offensive_tendencies(frame, 2024)["seconds_per_play"].iloc[0] == \
        pytest.approx((30 + 20 + 20) / 3)


def test_pace_is_never_read_across_a_change_of_possession():
    # The gap between a punt and the next offence's first snap belongs to
    # neither team. Grouping by drive is what prevents it being charged to one.
    # The gap ACROSS the two drives is 35 seconds -- short enough to look like a
    # normal play, so the length cap cannot quietly rescue this. Only the
    # grouping keeps it out.
    frame = repo_of([
        {"drive": 1, "play_id": 1, "game_seconds_remaining": 900.0},
        {"drive": 1, "play_id": 2, "game_seconds_remaining": 880.0},   # 20s
        {"drive": 2, "play_id": 3, "game_seconds_remaining": 845.0},   # NOT 35s
        {"drive": 2, "play_id": 4, "game_seconds_remaining": 825.0},   # 20s
    ])
    assert offensive_tendencies(frame, 2024)["seconds_per_play"].iloc[0] == \
        pytest.approx(20.0)


def test_an_implausibly_long_gap_is_thrown_away():
    # A timeout, an injury or a television break, not an offence taking its
    # time. A handful of these would ruin a team's average.
    long_gap = MAX_SECONDS_BETWEEN_PLAYS + 60
    frame = repo_of([
        {"play_id": 1, "game_seconds_remaining": 900.0},
        {"play_id": 2, "game_seconds_remaining": 880.0},              # 20s, kept
        {"play_id": 3, "game_seconds_remaining": 880.0 - long_gap},   # dropped
    ])
    # Play 3 is last, so it contributes no gap of its own and 20s is all that
    # survives.
    assert offensive_tendencies(frame, 2024)["seconds_per_play"].iloc[0] == \
        pytest.approx(20.0)


def test_a_gap_exactly_at_the_limit_is_kept():
    # The boundary is inclusive, so a 60-second gap counts. Worth stating,
    # because which side of the limit is included is invisible otherwise.
    frame = repo_of([
        {"play_id": 1, "game_seconds_remaining": 900.0},
        {"play_id": 2, "game_seconds_remaining": 900.0 - MAX_SECONDS_BETWEEN_PLAYS},
    ])
    assert offensive_tendencies(frame, 2024)["seconds_per_play"].iloc[0] == \
        pytest.approx(float(MAX_SECONDS_BETWEEN_PLAYS))


def test_two_plays_sharing_a_clock_reading_are_thrown_away():
    # A penalty replayed from the same spot leaves a gap of zero, which is not a
    # pace of zero.
    frame = repo_of([
        {"play_id": 1, "game_seconds_remaining": 900.0},
        {"play_id": 2, "game_seconds_remaining": 900.0},
        {"play_id": 3, "game_seconds_remaining": 875.0},   # 25s from play 2
    ])
    assert offensive_tendencies(frame, 2024)["seconds_per_play"].iloc[0] == \
        pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_a_week_range_narrows_the_table():
    frame = repo_of([
        {"week": 1, "game_id": "G1"}, {"week": 2, "game_id": "G2"},
        {"week": 3, "game_id": "G3"},
    ])
    assert offensive_tendencies(frame, 2024, weeks=(1, 2))["games"].iloc[0] == 2


def test_both_ends_of_the_week_range_are_included():
    frame = repo_of([{"week": w, "game_id": f"G{w}"} for w in (1, 2, 3)])
    assert offensive_tendencies(frame, 2024, weeks=(1, 3))["games"].iloc[0] == 3


def test_another_season_is_not_mixed_in():
    frame = repo_of([
        {"season": 2023, "game_id": "A"}, {"season": 2024, "game_id": "B"},
    ])
    assert offensive_tendencies(frame, 2024)["games"].iloc[0] == 1


def test_teams_are_sorted_most_pass_happy_first():
    frame = repo_of([
        {"posteam": "RUN", "play_type": "run", "pass": 0.0, "xpass": 0.5},
        {"posteam": "THROW", "play_type": "pass", "pass": 1.0, "xpass": 0.5},
    ])
    assert list(offensive_tendencies(frame, 2024)["team"]) == ["THROW", "RUN"]


# ---------------------------------------------------------------------------
# What a defence gives up
# ---------------------------------------------------------------------------

def defensive_repo(plays_rows, opportunity_rows):
    """A repository serving both tables the defensive numbers need."""
    opportunity = pd.DataFrame(opportunity_rows)
    # `rescore` is all-or-nothing about its columns -- it refuses loudly rather
    # than producing points that are quietly wrong -- so the fixture has to carry
    # the full set the real table has.
    for column, default in (
        ("season", 2024), ("week", 1), ("game_id", "G1"), ("posteam", "SF"),
        ("position", "RB"), ("player_id", "P1"),
        ("rush_fantasy_points", 0.0), ("rec_fantasy_points", 0.0),
        ("pass_fantasy_points", 0.0), ("total_fantasy_points", 0.0),
        ("rush_fantasy_points_exp", 0.0), ("rec_fantasy_points_exp", 0.0),
        ("pass_fantasy_points_exp", 0.0), ("total_fantasy_points_exp", 0.0),
        ("receptions", 0.0), ("receptions_exp", 0.0),
        ("pass_interception", 0.0), ("pass_interception_exp", 0.0),
    ):
        if column not in opportunity.columns:
            opportunity[column] = default
        else:
            opportunity[column] = opportunity[column].fillna(default)

    class Repo:
        def pbp(self):
            return plays(plays_rows)

        def ff_opportunity(self):
            return opportunity

    return Repo()


def test_a_defence_is_measured_on_the_plays_it_faced():
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo(
        [{"posteam": "SF", "defteam": "SEA", "play_type": "run", "epa": 0.2},
         {"posteam": "SF", "defteam": "SEA", "play_type": "run", "epa": 0.4},
         {"posteam": "SF", "defteam": "SEA", "play_type": "pass", "epa": 9.0}],
        [{"posteam": "SF", "rush_fantasy_points": 12.0}],
    )
    row = defensive_allowances(repo, 2024, play_kind="rush").iloc[0]

    assert row["team"] == "SEA"
    assert row["plays_faced"] == 2                     # the pass is not a rush
    assert row["epa_per_play"] == pytest.approx(0.3)   # and not dragged by 9.0
    assert row["points_allowed"] == pytest.approx(12.0)
    assert row["points_per_play"] == pytest.approx(6.0)


def test_points_are_charged_to_the_defence_not_the_offence():
    # The whole reason the two tables have to be joined: fantasy points are
    # recorded against whoever SCORED them.
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo(
        [{"posteam": "SF", "defteam": "SEA", "play_type": "run", "epa": 0.0}],
        [{"posteam": "SF", "rush_fantasy_points": 20.0}],
    )
    table = defensive_allowances(repo, 2024, play_kind="rush")
    assert list(table["team"]) == ["SEA"]
    assert table["points_allowed"].iloc[0] == 20.0


def test_only_the_chosen_positions_points_are_counted():
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo(
        [{"posteam": "SF", "defteam": "SEA", "play_type": "run", "epa": 0.0}],
        [{"posteam": "SF", "position": "RB", "rush_fantasy_points": 10.0},
         {"posteam": "SF", "position": "QB", "rush_fantasy_points": 6.0,
          "player_id": "P2"}],
    )
    both = defensive_allowances(repo, 2024, play_kind="rush")
    backs = defensive_allowances(repo, 2024, positions=["RB"], play_kind="rush")

    assert both["points_allowed"].iloc[0] == 16.0
    assert backs["points_allowed"].iloc[0] == 10.0


def test_the_passing_view_counts_receiving_points():
    # Not the quarterback's passing points: the question is which defences let
    # PASS-CATCHERS score, and a pass-catcher is what you are choosing between.
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo(
        [{"posteam": "SF", "defteam": "SEA", "play_type": "pass", "epa": 0.1}],
        [{"posteam": "SF", "position": "WR", "rec_fantasy_points": 14.0,
          "pass_fantasy_points": 22.0}],
    )
    row = defensive_allowances(repo, 2024, play_kind="pass").iloc[0]
    assert row["points_allowed"] == 14.0


def test_epa_is_not_narrowed_by_position():
    # It measures the defence against every play of that kind, because that is
    # what defensive quality means. Only the fantasy side narrows.
    from services.dfs_team_service import defensive_allowances
    rows = [{"posteam": "SF", "defteam": "SEA", "play_type": "run", "epa": e}
            for e in (0.0, 1.0)]
    repo = defensive_repo(rows, [{"posteam": "SF", "position": "RB",
                                  "rush_fantasy_points": 5.0}])

    everyone = defensive_allowances(repo, 2024, play_kind="rush")
    backs = defensive_allowances(repo, 2024, positions=["RB"], play_kind="rush")
    assert everyone["epa_per_play"].iloc[0] == backs["epa_per_play"].iloc[0] == 0.5


def test_a_defence_that_allowed_nothing_scores_zero_not_blank():
    # Zero really is the right answer here, unlike for a pass rate: they faced
    # the plays and gave up no points.
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo(
        [{"posteam": "SF", "defteam": "SEA", "play_type": "run", "epa": -0.5}],
        [{"posteam": "SF", "position": "WR", "rec_fantasy_points": 9.0}],
    )
    row = defensive_allowances(repo, 2024, positions=["RB"], play_kind="rush").iloc[0]
    assert row["points_allowed"] == 0.0
    assert row["points_per_play"] == 0.0


def test_defences_are_sorted_most_generous_first():
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo(
        [{"posteam": "SF", "defteam": "SOFT", "play_type": "run", "epa": 0.0},
         {"posteam": "KC", "defteam": "HARD", "play_type": "run", "epa": 0.0,
          "game_id": "G2"}],
        [{"posteam": "SF", "rush_fantasy_points": 20.0},
         {"posteam": "KC", "rush_fantasy_points": 2.0, "game_id": "G2",
          "player_id": "P2"}],
    )
    assert list(defensive_allowances(repo, 2024, play_kind="rush")["team"]) == \
        ["SOFT", "HARD"]


def test_an_unknown_play_kind_is_refused():
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo([{"posteam": "SF", "defteam": "SEA"}], [{"posteam": "SF"}])
    with pytest.raises(KeyError, match="unknown play kind"):
        defensive_allowances(repo, 2024, play_kind="kicking")


def test_an_empty_season_returns_the_columns_anyway():
    from services.dfs_team_service import defensive_allowances
    repo = defensive_repo([{"posteam": "SF", "defteam": "SEA"}], [{"posteam": "SF"}])
    table = defensive_allowances(repo, 1999, play_kind="rush")
    assert table.empty
    assert "points_per_play" in table.columns
