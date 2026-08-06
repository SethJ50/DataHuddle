"""Tests for the actual-versus-expected table and the plot registry.

Two things sit behind the app's first Daily Fantasy plot: a service that reduces
a season of weekly rows to one row per player, and a registry that tells the page
what to draw. Both are tested without Streamlit.

The judgement calls worth pinning:

1. PER-GAME, NOT TOTALS. Totals reward whoever played most, which is a fact about
   availability rather than about beating expectation.
2. THE MINIMUM-GAMES FLOOR. A one-game sample sits further from the break-even
   line than a full season does purely because it is one game.
3. THE SCORING REACHES THE TABLE. Every number here is FanDuel by default, and a
   service that quietly reported PPR would look entirely reasonable.
"""

import pandas as pd
import pytest

from presentation.dfs_plot_registry import (
    ALTAIR, MATPLOTLIB, PLOTS, PLOTS_BY_LABEL, SPLIT_OPTIONS,
)
from services.dfs_opportunity_service import (
    SPLITS, actual_vs_expected, seasons_available, week_range,
)
from services.dfs_scoring import DfsScoring


class FakeRepo:
    """Hands back one fixed expected-points table, as `DfsReadRepo` would."""

    def __init__(self, frame):
        self._frame = frame

    def ff_opportunity(self):
        return self._frame


def weekly(rows):
    """Build an `ff_opportunity`-shaped table from a few readable rows.

    Each row gives a player, a week, and his actual and expected receiving
    points. Everything `rescore` needs is filled in around them, so a test can
    say only what it means.
    """
    frame = pd.DataFrame(rows)
    frame["season"] = frame.get("season", 2024)
    for column, default in (
        ("posteam", "SEA"), ("position", "WR"),
        ("pass_fantasy_points", 0.0), ("pass_fantasy_points_exp", 0.0),
        ("rush_fantasy_points", 0.0), ("rush_fantasy_points_exp", 0.0),
        ("receptions", 0.0), ("receptions_exp", 0.0),
        ("pass_interception", 0.0), ("pass_interception_exp", 0.0),
    ):
        if column not in frame.columns:
            frame[column] = default

    frame["total_fantasy_points"] = (frame["pass_fantasy_points"]
                                     + frame["rec_fantasy_points"]
                                     + frame["rush_fantasy_points"])
    frame["total_fantasy_points_exp"] = (frame["pass_fantasy_points_exp"]
                                         + frame["rec_fantasy_points_exp"]
                                         + frame["rush_fantasy_points_exp"])
    return FakeRepo(frame)


@pytest.fixture
def repo():
    """Two players over three weeks, one beating expectation and one missing it."""
    return weekly([
        {"player_id": "A", "full_name": "Alice Ace", "week": 1,
         "rec_fantasy_points": 20.0, "rec_fantasy_points_exp": 10.0},
        {"player_id": "A", "full_name": "Alice Ace", "week": 2,
         "rec_fantasy_points": 20.0, "rec_fantasy_points_exp": 10.0},
        {"player_id": "A", "full_name": "Alice Ace", "week": 3,
         "rec_fantasy_points": 20.0, "rec_fantasy_points_exp": 10.0},
        {"player_id": "B", "full_name": "Bob Bench", "week": 1,
         "rec_fantasy_points": 4.0, "rec_fantasy_points_exp": 12.0},
    ])


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_it_returns_one_row_per_player(repo):
    frame = actual_vs_expected(repo, 2024, scoring=DfsScoring.PPR)
    assert list(frame["player_id"]) == ["A", "B"]      # sorted by gap, best first


def test_the_gap_is_measured_per_game_not_in_total(repo):
    # Alice beat expectation by 10 a game across three games. Reporting the
    # total, 30, would say she is three times better than she is.
    frame = actual_vs_expected(repo, 2024, scoring=DfsScoring.PPR)
    alice = frame.set_index("player_id").loc["A"]

    assert alice["games"] == 3
    assert alice["actual"] == 60.0                    # the total is still there
    assert alice["actual_per_game"] == 20.0
    assert alice["gap_per_game"] == 10.0


def test_players_are_ranked_by_the_gap(repo):
    frame = actual_vs_expected(repo, 2024, scoring=DfsScoring.PPR)
    assert list(frame["gap_per_game"]) == sorted(frame["gap_per_game"],
                                                 reverse=True)
    assert frame["player_id"].iloc[-1] == "B"          # missed expectation


def test_a_short_sample_can_be_filtered_out(repo):
    # Bob played once. Over a long range a single game sits further from the
    # line than a full season does, purely because it is one game.
    frame = actual_vs_expected(repo, 2024, minimum_games=2, scoring=DfsScoring.PPR)
    assert list(frame["player_id"]) == ["A"]


def test_the_week_range_is_respected(repo):
    frame = actual_vs_expected(repo, 2024, weeks=(1, 2), scoring=DfsScoring.PPR)
    assert frame.set_index("player_id").loc["A", "games"] == 2


def test_both_ends_of_the_week_range_are_included(repo):
    # An off-by-one here would silently drop a week from every number.
    frame = actual_vs_expected(repo, 2024, weeks=(1, 3), scoring=DfsScoring.PPR)
    assert frame.set_index("player_id").loc["A", "games"] == 3


def test_positions_can_be_narrowed():
    repo = weekly([
        {"player_id": "A", "full_name": "A", "week": 1, "position": "WR",
         "rec_fantasy_points": 10.0, "rec_fantasy_points_exp": 8.0},
        {"player_id": "B", "full_name": "B", "week": 1, "position": "TE",
         "rec_fantasy_points": 9.0, "rec_fantasy_points_exp": 7.0},
    ])
    frame = actual_vs_expected(repo, 2024, positions=["TE"], scoring=DfsScoring.PPR)
    assert list(frame["player_id"]) == ["B"]


def test_another_season_is_not_mixed_in():
    repo = weekly([
        {"player_id": "A", "full_name": "A", "week": 1, "season": 2023,
         "rec_fantasy_points": 30.0, "rec_fantasy_points_exp": 5.0},
        {"player_id": "A", "full_name": "A", "week": 1, "season": 2024,
         "rec_fantasy_points": 10.0, "rec_fantasy_points_exp": 10.0},
    ])
    frame = actual_vs_expected(repo, 2024, scoring=DfsScoring.PPR)
    assert frame["actual"].iloc[0] == 10.0


def test_the_chosen_scoring_reaches_the_numbers():
    # Eight catches is four FanDuel points less than eight PPR points. A service
    # that forgot to rescore would look entirely reasonable and be wrong by that
    # much on every receiver.
    repo = weekly([{"player_id": "A", "full_name": "A", "week": 1,
                    "rec_fantasy_points": 20.0, "rec_fantasy_points_exp": 16.0,
                    "receptions": 8.0, "receptions_exp": 6.0}])

    ppr = actual_vs_expected(repo, 2024, scoring=DfsScoring.PPR)
    fanduel = actual_vs_expected(repo, 2024, scoring=DfsScoring.FANDUEL)

    assert ppr["actual"].iloc[0] == 20.0
    assert fanduel["actual"].iloc[0] == 16.0          # 20 - 8 * 0.5
    assert fanduel["expected"].iloc[0] == 13.0        # 16 - 6 * 0.5


def test_each_split_reads_its_own_columns():
    repo = weekly([{"player_id": "A", "full_name": "A", "week": 1,
                    "rec_fantasy_points": 10.0, "rec_fantasy_points_exp": 8.0,
                    "rush_fantasy_points": 6.0, "rush_fantasy_points_exp": 5.0}])

    receiving = actual_vs_expected(repo, 2024, split="Receiving", scoring=DfsScoring.PPR)
    rushing = actual_vs_expected(repo, 2024, split="Rushing", scoring=DfsScoring.PPR)
    total = actual_vs_expected(repo, 2024, split="Total", scoring=DfsScoring.PPR)

    assert receiving["actual"].iloc[0] == 10.0
    assert rushing["actual"].iloc[0] == 6.0
    assert total["actual"].iloc[0] == 16.0


def test_an_unknown_split_is_refused(repo):
    # Checked before any data is read, so a typo fails immediately rather than
    # after a slow load.
    with pytest.raises(KeyError, match="unknown split"):
        actual_vs_expected(repo, 2024, split="Kicking")


def test_a_traded_player_shows_the_team_he_is_on_now():
    # `last` rather than `first`, because the team that matters for picking him
    # is the current one.
    repo = weekly([
        {"player_id": "A", "full_name": "A", "week": 1, "posteam": "NYJ",
         "rec_fantasy_points": 8.0, "rec_fantasy_points_exp": 8.0},
        {"player_id": "A", "full_name": "A", "week": 2, "posteam": "KC",
         "rec_fantasy_points": 8.0, "rec_fantasy_points_exp": 8.0},
    ])
    assert actual_vs_expected(repo, 2024, scoring=DfsScoring.PPR)["team"].iloc[0] == "KC"


# ---------------------------------------------------------------------------
# Empty results, which the page has to be able to draw
# ---------------------------------------------------------------------------


def test_an_empty_result_still_has_its_columns(repo):
    frame = actual_vs_expected(repo, 1999, scoring=DfsScoring.PPR)
    assert frame.empty
    assert "gap_per_game" in frame.columns


def test_filtering_everyone_out_still_has_its_columns(repo):
    frame = actual_vs_expected(repo, 2024, minimum_games=50, scoring=DfsScoring.PPR)
    assert frame.empty
    assert "gap_per_game" in frame.columns


# ---------------------------------------------------------------------------
# The filter helpers
# ---------------------------------------------------------------------------


def test_seasons_are_listed_newest_first():
    repo = weekly([
        {"player_id": "A", "full_name": "A", "week": 1, "season": 2023,
         "rec_fantasy_points": 1.0, "rec_fantasy_points_exp": 1.0},
        {"player_id": "A", "full_name": "A", "week": 1, "season": 2025,
         "rec_fantasy_points": 1.0, "rec_fantasy_points_exp": 1.0},
    ])
    assert seasons_available(repo) == [2025, 2023]


def test_the_week_range_comes_from_the_data(repo):
    # Not hard-coded to 1-18: a finished season runs into the playoffs and one
    # in progress stops early.
    assert week_range(repo, 2024) == (1, 3)


def test_a_season_with_no_data_still_gives_a_drawable_range(repo):
    assert week_range(repo, 1999) == (1, 18)


# ---------------------------------------------------------------------------
# The registry the page reads
# ---------------------------------------------------------------------------


def test_every_plot_is_complete_enough_to_draw():
    # The page trusts these entries. A missing piece would fail at click time.
    for plot in PLOTS:
        assert plot.key and plot.label and plot.question
        assert callable(plot.build)
        assert callable(plot.chart)


def test_plot_keys_and_labels_are_unique():
    # Labels are what the dropdown shows and keys namespace the widgets. A
    # repeat in either makes two plots share filter state.
    assert len({plot.key for plot in PLOTS}) == len(PLOTS)
    assert len(PLOTS_BY_LABEL) == len(PLOTS)


def test_every_filter_a_plot_asks_for_is_one_the_page_can_draw():
    known = {"season", "weeks", "positions", "split", "minimum_games",
             "measure"}
    for plot in PLOTS:
        assert set(plot.filters) <= known, plot.key


def test_every_filter_reaches_the_function_that_accepts_it():
    # Filters route three ways: DATA choices go to `build`, DRAWING choices go
    # to `chart`, and shared ones go to both. A filter routed to a function that
    # does not take it raises an unexpected-keyword error, and only at click
    # time -- so the routing is checked here instead.
    import inspect

    def accepts(function, names):
        """True if the function takes these arguments, or takes anything."""
        signature = inspect.signature(function)
        if any(p.kind == p.VAR_KEYWORD for p in signature.parameters.values()):
            return True
        return names <= set(signature.parameters)

    for plot in PLOTS:
        declared = set(plot.filters)
        chart_only = set(plot.chart_filters)
        shared = set(plot.shared_filters)

        assert chart_only <= declared, f"{plot.key}: chart_filters not in filters"
        assert shared <= declared, f"{plot.key}: shared_filters not in filters"
        assert not (chart_only & shared), (
            f"{plot.key}: a filter cannot be both chart-only and shared")

        assert accepts(plot.build, declared - chart_only), plot.key
        assert accepts(plot.chart, chart_only | shared), plot.key


def test_a_plot_that_uses_scoring_accepts_it():
    # The page passes `scoring` only to plots that declared they use it, so the
    # two have to agree. A pass rate is the same number whatever a catch pays.
    import inspect
    for plot in PLOTS:
        signature = inspect.signature(plot.build)
        takes_anything = any(p.kind == p.VAR_KEYWORD
                             for p in signature.parameters.values())
        if plot.uses_scoring:
            assert takes_anything or "scoring" in signature.parameters, plot.key


def test_every_plot_declares_how_it_should_be_drawn():
    for plot in PLOTS:
        assert plot.renderer in (MATPLOTLIB, ALTAIR), plot.key


def test_there_is_more_than_one_plot_to_choose_from():
    # The dropdown, the filter routing and the per-plot widget keys all exist
    # for the multi-plot case. One plot would not exercise any of it.
    assert len(PLOTS) >= 4


def test_the_split_options_come_from_the_service():
    # So the dropdown and the lookup can never disagree about a split's name.
    assert tuple(SPLITS) == SPLIT_OPTIONS
