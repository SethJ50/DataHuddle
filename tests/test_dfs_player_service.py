"""Tests for the joined player-week table.

Six sources, three different names for the same player, and every join a chance
to lose rows or invent them. The failures worth guarding are all quiet ones:

1. AN INNER JOIN ANYWHERE DELETES PLAYERS. Every source past the box score covers
   fewer people, and joining the wrong way silently drops whoever the narrower
   source never heard of.
2. SEASON-SUMMARY ROWS. Next Gen Stats ships a `week == 0` row per player holding
   season averages. Joined in, it attaches a season's worth of numbers to a week.
3. A REPEATED KEY MULTIPLIES ROWS, turning one game into two and every average
   built on it into nonsense.
"""

import numpy as np
import pandas as pd
import pytest

from services.dfs_player_service import (
    SEASON_TOTAL_WEEK, player_weeks, rolling_form,
)
from services.dfs_scoring import DfsScoring


class FakeRepo:
    """Serves each table the player-week builder asks for."""

    def __init__(self, **tables):
        self.tables = tables

    def _get(self, name, columns=()):
        frame = self.tables.get(name)
        return frame if frame is not None else pd.DataFrame(columns=list(columns))

    def player_stats(self):
        return self._get("player_stats")

    def ff_opportunity(self):
        return self._get("ff_opportunity")

    def snap_counts(self):
        return self._get("snap_counts",
                         ["pfr_player_id", "season", "week", "offense_snaps",
                          "offense_pct"])

    def nextgen_stats(self, kind):
        return self._get(f"nextgen_{kind}")

    def pfr_advstats(self, kind):
        return self._get(f"pfr_{kind}")

    def player_id_crosswalk(self):
        return self._get("crosswalk", ["pfr_player_id", "canonical_id"])

    def pbp(self):
        return self._get("pbp", ["yardline_100", "season", "week", "play_id",
                                 "rusher_player_id", "receiver_player_id"])


def box_score(rows):
    """A `player_stats`-shaped table, filled out around what each row states."""
    frame = pd.DataFrame(rows)
    defaults = {
        "player_id": "P1", "player_display_name": "Player One",
        "position": "WR", "team": "SEA", "opponent_team": "SF",
        "season": 2024, "week": 1, "headshot_url": "u", "targets": 6,
        "receptions": 4, "receiving_yards": 55, "carries": 0,
        "target_share": 0.2,
    }
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
        else:
            frame[column] = frame[column].fillna(value)
    return frame


def expected(rows):
    """An `ff_opportunity`-shaped table with everything `rescore` needs.

    Rows state the RECEIVING points and the totals are derived, exactly as the
    real source guarantees and as `rescore` rebuilds them. Stating a total
    directly would be silently discarded, which is a trap worth not laying.
    """
    frame = pd.DataFrame(rows)
    defaults = {
        "player_id": "P1", "season": 2024, "week": 1,
        "rush_fantasy_points": 0.0, "rec_fantasy_points": 0.0,
        "pass_fantasy_points": 0.0,
        "rush_fantasy_points_exp": 0.0, "rec_fantasy_points_exp": 0.0,
        "pass_fantasy_points_exp": 0.0,
        "receptions": 0.0, "receptions_exp": 0.0,
        "pass_interception": 0.0, "pass_interception_exp": 0.0,
    }
    for column, value in defaults.items():
        if column not in frame.columns:
            frame[column] = value
        else:
            frame[column] = frame[column].fillna(value)

    for suffix in ("", "_exp"):
        frame[f"total_fantasy_points{suffix}"] = sum(
            frame[f"{part}_fantasy_points{suffix}"]
            for part in ("rush", "rec", "pass"))
    return frame


@pytest.fixture
def repo():
    """Two players over two weeks, with every source represented."""
    return FakeRepo(
        player_stats=box_score([
            {"player_id": "P1", "week": 1, "targets": 8, "receptions": 6},
            {"player_id": "P1", "week": 2, "targets": 10, "receptions": 7},
            {"player_id": "P2", "week": 1, "player_display_name": "Player Two",
             "position": "RB", "targets": 2, "carries": 15},
        ]),
        ff_opportunity=expected([
            {"player_id": "P1", "week": 1, "rec_fantasy_points": 18.0,
             "rec_fantasy_points_exp": 14.0},
            {"player_id": "P1", "week": 2, "rec_fantasy_points": 12.0,
             "rec_fantasy_points_exp": 16.0},
        ]),
        snap_counts=pd.DataFrame({
            "pfr_player_id": ["PfrOne00", "PfrOne00"],
            "season": [2024, 2024], "week": [1, 2],
            "offense_snaps": [60, 70], "offense_pct": [0.85, 0.92],
        }),
        crosswalk=pd.DataFrame({"pfr_player_id": ["PfrOne00"],
                                "canonical_id": ["P1"]}),
        nextgen_receiving=pd.DataFrame({
            "player_gsis_id": ["P1", "P1", "P1"],
            "season": [2024, 2024, 2024],
            "week": [SEASON_TOTAL_WEEK, 1, 2],          # the trap row first
            "avg_separation": [99.0, 2.8, 3.1],
        }),
        pbp=pd.DataFrame({
            "yardline_100": [10.0, 5.0, 45.0],
            "season": [2024, 2024, 2024], "week": [1, 1, 1],
            "play_id": [1, 2, 3],
            "rusher_player_id": [None, "P2", None],
            "receiver_player_id": ["P1", None, "P1"],
        }),
    )


# ---------------------------------------------------------------------------
# The shape of the result
# ---------------------------------------------------------------------------


def test_it_returns_one_row_per_player_per_week(repo):
    frame = player_weeks(repo)
    assert len(frame) == 3
    assert not frame.duplicated(["canonical_id", "season", "week"]).any()


def test_the_box_score_columns_survive(repo):
    frame = player_weeks(repo).set_index(["canonical_id", "week"])
    assert frame.loc[("P1", 2), "targets"] == 10
    assert frame.loc[("P2", 1), "carries"] == 15


def test_the_player_id_is_renamed_to_the_app_s_own_name(repo):
    # Everything downstream works in `canonical_id`, and three sources each call
    # it something different.
    assert "canonical_id" in player_weeks(repo).columns
    assert "player_id" not in player_weeks(repo).columns


# ---------------------------------------------------------------------------
# Joins that must not lose or duplicate anybody
# ---------------------------------------------------------------------------


def test_a_player_missing_from_a_narrower_source_still_gets_a_row(repo):
    # P2 has no expected points, no snaps and no tracking data. An inner join
    # anywhere would delete him rather than leave those columns blank.
    frame = player_weeks(repo).set_index(["canonical_id", "week"])
    assert ("P2", 1) in frame.index
    assert np.isnan(frame.loc[("P2", 1), "total_fantasy_points"])
    assert np.isnan(frame.loc[("P2", 1), "snap_share"])


def test_the_season_summary_row_is_not_joined_in(repo):
    # Next Gen Stats ships a week-0 row holding season averages. Joined in, it
    # attaches a whole season's numbers to whichever week it lands on -- and
    # nothing announces it is there.
    frame = player_weeks(repo).set_index(["canonical_id", "week"])
    assert frame.loc[("P1", 1), "avg_separation"] == pytest.approx(2.8)
    assert frame.loc[("P1", 2), "avg_separation"] == pytest.approx(3.1)
    assert 99.0 not in set(frame["avg_separation"].dropna())


def test_a_repeated_row_in_a_source_cannot_multiply_the_table(repo):
    repo.tables["nextgen_receiving"] = pd.concat(
        [repo.tables["nextgen_receiving"]] * 3, ignore_index=True)
    assert len(player_weeks(repo)) == 3


def test_two_snap_rows_for_one_week_are_added_together(repo):
    # A suspended game resumed on another day appears twice.
    repo.tables["snap_counts"] = pd.DataFrame({
        "pfr_player_id": ["PfrOne00", "PfrOne00"],
        "season": [2024, 2024], "week": [1, 1],
        "offense_snaps": [30, 25], "offense_pct": [0.5, 0.4],
    })
    frame = player_weeks(repo).set_index(["canonical_id", "week"])
    assert frame.loc[("P1", 1), "offense_snaps"] == 55


def test_snaps_arrive_through_the_id_crosswalk(repo):
    # They are keyed by Pro Football Reference's id, which nothing else uses.
    frame = player_weeks(repo).set_index(["canonical_id", "week"])
    assert frame.loc[("P1", 1), "offense_snaps"] == 60
    assert frame.loc[("P1", 2), "snap_share"] == pytest.approx(0.92)


def test_a_snap_row_that_cannot_be_resolved_is_dropped(repo):
    # Keeping it with an empty id would let every unresolved player collide into
    # one meaningless row.
    repo.tables["snap_counts"] = pd.concat([
        repo.tables["snap_counts"],
        pd.DataFrame({"pfr_player_id": ["Unknown0"], "season": [2024],
                      "week": [1], "offense_snaps": [40], "offense_pct": [0.6]}),
    ], ignore_index=True)
    assert len(player_weeks(repo)) == 3


def test_a_source_that_is_entirely_missing_costs_only_its_columns(repo):
    # Tracking data can be unavailable for a season. That should cost those
    # columns, not the table.
    repo.tables["nextgen_receiving"] = pd.DataFrame()
    frame = player_weeks(repo)
    assert len(frame) == 3
    assert "avg_separation" not in frame.columns


# ---------------------------------------------------------------------------
# Points and scoring
# ---------------------------------------------------------------------------


def test_actual_and_expected_points_both_arrive(repo):
    frame = player_weeks(repo).set_index(["canonical_id", "week"])
    assert frame.loc[("P1", 1), "total_fantasy_points"] == 18.0
    assert frame.loc[("P1", 1), "total_fantasy_points_exp"] == 14.0


def test_the_chosen_scoring_reaches_the_points(repo):
    repo.tables["ff_opportunity"] = expected([
        {"player_id": "P1", "week": 1, "rec_fantasy_points": 20.0,
         "receptions": 8.0},
    ])
    ppr = player_weeks(repo, DfsScoring.PPR).set_index(["canonical_id", "week"])
    fanduel = player_weeks(repo, DfsScoring.FANDUEL).set_index(["canonical_id", "week"])

    assert ppr.loc[("P1", 1), "total_fantasy_points"] == 20.0
    assert fanduel.loc[("P1", 1), "total_fantasy_points"] == 16.0   # 8 catches


# ---------------------------------------------------------------------------
# Red-zone touches
# ---------------------------------------------------------------------------


def test_red_zone_carries_and_targets_both_count():
    # A target is the opportunity; whether it was caught is the outcome, and
    # usage is looked at separately from production precisely to see the one
    # without the other.
    repo = FakeRepo(
        player_stats=box_score([{"player_id": "P1", "week": 1}]),
        pbp=pd.DataFrame({
            "yardline_100": [8.0, 3.0, 60.0],
            "season": [2024] * 3, "week": [1] * 3, "play_id": [1, 2, 3],
            "rusher_player_id": [None, "P1", None],
            "receiver_player_id": ["P1", None, "P1"],   # the last is outside
        }),
    )
    assert player_weeks(repo)["red_zone_touches"].iloc[0] == 2


def test_a_player_with_no_red_zone_work_scores_zero_not_blank():
    # Every row is a player who APPEARED, so nothing to count means none rather
    # than unknown -- and a blank would read as missing data.
    repo = FakeRepo(
        player_stats=box_score([{"player_id": "P1", "week": 1}]),
        pbp=pd.DataFrame({
            "yardline_100": [70.0], "season": [2024], "week": [1],
            "play_id": [1], "rusher_player_id": [None],
            "receiver_player_id": ["P1"],
        }),
    )
    frame = player_weeks(repo)
    assert frame["red_zone_touches"].iloc[0] == 0
    assert not frame["red_zone_touches"].isna().any()


# ---------------------------------------------------------------------------
# Rolling form
# ---------------------------------------------------------------------------


def test_form_summarises_the_most_recent_games(repo):
    form = rolling_form(player_weeks(repo), "P1", games=2)
    assert form["games"] == 2
    assert form["points"] == pytest.approx(15.0)          # (18 + 12) / 2
    assert form["expected_points"] == pytest.approx(15.0)  # (14 + 16) / 2
    assert form["gap"] == pytest.approx(0.0)


def test_form_takes_the_LAST_few_games_not_the_first(repo):
    form = rolling_form(player_weeks(repo), "P1", games=1)
    assert form["points"] == pytest.approx(12.0)          # week 2, not week 1


def test_form_for_a_player_with_no_rows_says_so(repo):
    assert rolling_form(player_weeks(repo), "NOBODY")["games"] == 0


def test_form_ignores_missing_numbers_rather_than_counting_them_as_zero(repo):
    # P2 has no expected points at all. Averaging a blank as zero would make him
    # look like a spectacular overperformer.
    form = rolling_form(player_weeks(repo), "P2")
    assert form["games"] == 1
    assert np.isnan(form["expected_points"])
    assert np.isnan(form["gap"])


# ---------------------------------------------------------------------------
# Who gets the ball
# ---------------------------------------------------------------------------

def test_usage_shares_are_of_the_team_total(repo):
    from services.dfs_player_service import team_usage
    frame = player_weeks(repo)
    usage = team_usage(frame, "SEA", season=2024).set_index("name")

    # P1 saw 8 + 10 targets, P2 saw 2, of 20 between them.
    assert usage.loc["Player One", "targets"] == 18
    assert usage.loc["Player One", "target_share"] == pytest.approx(0.9)
    assert usage.loc["Player Two", "target_share"] == pytest.approx(0.1)


def test_every_share_adds_up_to_one(repo):
    from services.dfs_player_service import team_usage
    usage = team_usage(player_weeks(repo), "SEA", season=2024)
    for share in ("target_share", "carry_share"):
        assert usage[share].sum() == pytest.approx(1.0)


def test_shares_come_from_totals_not_from_averaging_weekly_shares():
    # A player who saw 40% of the targets in his only game did not command 40%
    # of the offence. Averaging his weekly shares would say he did.
    from services.dfs_player_service import team_usage
    repo = FakeRepo(player_stats=box_score([
        {"player_id": "EVERY", "player_display_name": "Every Week",
         "week": 1, "targets": 6},
        {"player_id": "EVERY", "player_display_name": "Every Week",
         "week": 2, "targets": 6},
        {"player_id": "ONCE", "player_display_name": "One Game",
         "week": 1, "targets": 4},
    ]))
    usage = team_usage(player_weeks(repo), "SEA").set_index("name")

    # Totals: 12 of 16 against 4 of 16.
    assert usage.loc["One Game", "target_share"] == pytest.approx(0.25)
    # Averaging weekly shares would have given 4/10 = 0.40.
    assert usage.loc["One Game", "target_share"] != pytest.approx(0.40)


def test_usage_counts_games_played_not_weeks_in_the_range(repo):
    from services.dfs_player_service import team_usage
    usage = team_usage(player_weeks(repo), "SEA", season=2024).set_index("name")
    assert usage.loc["Player One", "games"] == 2
    assert usage.loc["Player Two", "games"] == 1


def test_usage_is_sorted_by_target_share(repo):
    from services.dfs_player_service import team_usage
    usage = team_usage(player_weeks(repo), "SEA", season=2024)
    assert list(usage["target_share"]) == sorted(usage["target_share"],
                                                 reverse=True)


def test_a_team_with_no_players_returns_the_columns_anyway(repo):
    from services.dfs_player_service import team_usage
    usage = team_usage(player_weeks(repo), "NOBODY")
    assert usage.empty
    assert "target_share" in usage.columns


def test_usage_respects_the_week_range(repo):
    from services.dfs_player_service import team_usage
    usage = team_usage(player_weeks(repo), "SEA", season=2024,
                       weeks=(1, 1)).set_index("name")
    assert usage.loc["Player One", "targets"] == 8      # week 1 only
