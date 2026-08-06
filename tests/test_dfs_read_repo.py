"""Tests for the Daily Fantasy data loader.

No network. A stand-in for nflreadpy is passed in, which is what the repository's
`loader` argument exists for, so the suite stays fast and works offline.

Four things here are worth more than the rest, because each is a real problem
found while building this and each fails quietly rather than loudly:

1. PLAY-BY-PLAY MUST BE TRIMMED BEFORE CONVERSION. Untrimmed it is 372 MB per
   season; trimmed it is 19 MB. Selecting after the conversion would not help.
2. SEASON AND WEEK MUST COME OUT THE SAME TYPE EVERYWHERE. `ff_opportunity`
   ships the season as text and the week as a decimal while everything else uses
   whole numbers, which breaks every join in the app.
3. TEXT MUST NOT BECOME A pandas CATEGORY. Categories are smaller, but they make
   `groupby` invent rows for combinations that never happened.
4. THE ID CROSSWALK MUST BE UNIQUE. Three Pro Football Reference ids point at two
   different players each, and a repeated key silently multiplies rows on join.
"""

import pandas as pd
import polars as pl
import pytest

from repositories.dfs_read_repo import PBP_COLUMNS, DfsReadRepo


class FakeLoader:
    """Stands in for nflreadpy, returning small Polars frames and counting calls.

    Mirrors the real library's shape: every `load_*` function takes the seasons
    and returns a Polars frame. Recording the calls is what lets the tests prove
    the caching works.
    """

    def __init__(self, **frames):
        """Store the frames to hand back, and start the call log empty."""
        self.frames = frames
        self.calls = []

    def _serve(self, name, seasons, **kwargs):
        """Log one call and return the frame registered under that name."""
        self.calls.append((name, tuple(seasons or ()), tuple(sorted(kwargs.items()))))
        return self.frames[name]

    def load_pbp(self, seasons):
        return self._serve("pbp", seasons)

    def load_ff_opportunity(self, seasons, stat_type=None):
        return self._serve("ff_opportunity", seasons, stat_type=stat_type)

    def load_snap_counts(self, seasons):
        return self._serve("snap_counts", seasons)

    def load_nextgen_stats(self, seasons, stat_type=None):
        return self._serve("nextgen_stats", seasons, stat_type=stat_type)

    def load_pfr_advstats(self, seasons, stat_type=None):
        return self._serve("pfr_advstats", seasons, stat_type=stat_type)

    def load_schedules(self, seasons):
        return self._serve("schedules", seasons)

    def load_teams(self):
        return self._serve("teams", ())

    def load_ff_playerids(self):
        return self._serve("ff_playerids", ())

    def count(self, name):
        """How many times a given table was asked for."""
        return sum(1 for call in self.calls if call[0] == name)


def wide_pbp(rows=6):
    """A play-by-play frame with every needed column plus junk to be trimmed.

    The junk columns stand in for the 328 real ones this app never reads, which
    is the whole reason the trimming exists.
    """
    data = {}
    for column in PBP_COLUMNS:
        if column in ("posteam", "defteam", "home_team", "away_team"):
            data[column] = ["SEA", "SF"] * (rows // 2)
        elif column in ("game_id", "play_type", "passer_player_id",
                        "rusher_player_id", "receiver_player_id"):
            data[column] = [f"{column}_{i}" for i in range(rows)]
        elif column in ("season", "week"):
            data[column] = [2024] * rows
        else:
            data[column] = [float(i) for i in range(rows)]

    for i in range(40):                      # the columns nobody asked for
        data[f"unused_{i}"] = list(range(rows))
    return pl.DataFrame(data)


def crosswalk_frame():
    """An id table containing every awkward case the real one contains."""
    return pl.DataFrame({
        "pfr_id":   ["GoodPl00", "GoodPl00", "NoGsis00", None,
                     "Ambig000", "Ambig000", "OtherP00"],
        "gsis_id":  ["00-0001", "00-0001", None, "00-0009",
                     "00-0002", "00-0003", "00-0004"],
        "name":     ["Good Player", "Good Player", "No Gsis", "No Pfr",
                     "Ambiguous One", "Ambiguous Two", "Other Player"],
    })


@pytest.fixture
def repo():
    """A repository wired to the fake loader, over two seasons."""
    return DfsReadRepo(
        [2024, 2025],
        loader=FakeLoader(
            pbp=wide_pbp(),
            ff_opportunity=pl.DataFrame({
                # The awkward one: season as TEXT, week as a DECIMAL.
                "season": ["2024", "2024"], "week": [1.0, 2.0],
                "player_id": ["00-0001", "00-0001"],
                "total_fantasy_points": [17.2, 9.4],
            }),
            snap_counts=pl.DataFrame({
                "season": [2024, 2024], "week": [1, 2],
                "pfr_player_id": ["GoodPl00", "GoodPl00"],
                "offense_pct": [0.88, 0.91],
            }),
            nextgen_stats=pl.DataFrame({"season": [2024], "week": [1],
                                        "player_gsis_id": ["00-0001"]}),
            pfr_advstats=pl.DataFrame({"season": [2024], "week": [1],
                                       "pfr_player_id": ["GoodPl00"]}),
            schedules=pl.DataFrame({"season": [2024], "week": [1],
                                    "game_id": ["2024_01_SEA_SF"]}),
            teams=pl.DataFrame({"team_abbr": ["SEA", "SF"],
                                "team_logo_espn": ["sea.png", "sf.png"]}),
            ff_playerids=crosswalk_frame(),
        ),
    )


# ---------------------------------------------------------------------------
# Play-by-play trimming
# ---------------------------------------------------------------------------


def test_play_by_play_is_trimmed_to_the_declared_columns(repo):
    # The load-bearing one. Untrimmed this table is 372 MB per season.
    frame = repo.pbp()
    assert list(frame.columns) == PBP_COLUMNS
    assert not [c for c in frame.columns if c.startswith("unused_")]


def test_the_trim_happens_before_the_conversion(repo):
    # THE POINT OF THE WHOLE EXERCISE. Trimming afterwards gives the same
    # columns and none of the saving, because the cost is in building the pandas
    # frame -- 372 MB of it. So this records what order the two actually happen
    # in, rather than just checking the result looks right.
    order = []

    class SpyFrame:
        """Wraps a Polars frame and notes which method was called when."""

        def __init__(self, frame):
            self._frame = frame

        @property
        def width(self):
            return self._frame.width

        def select(self, columns):
            order.append(("select", len(columns)))
            return SpyFrame(self._frame.select(columns))

        def to_pandas(self):
            order.append(("to_pandas", self._frame.width))
            return self._frame.to_pandas()

    repo._loader.frames["pbp"] = SpyFrame(wide_pbp())
    repo.pbp()

    assert order == [("select", len(PBP_COLUMNS)),
                     ("to_pandas", len(PBP_COLUMNS))], (
        "pbp must be narrowed while still a Polars frame, then converted")


# ---------------------------------------------------------------------------
# Types: the thing that breaks every join
# ---------------------------------------------------------------------------


def test_season_and_week_come_out_whole_numbers_everywhere(repo):
    for frame in (repo.pbp(), repo.ff_opportunity(), repo.snap_counts(),
                  repo.schedules(), repo.nextgen_stats("receiving")):
        assert frame["season"].dtype == "Int64"
        assert frame["week"].dtype == "Int64"


def test_the_odd_source_can_be_joined_to_the_normal_ones(repo):
    # ff_opportunity ships season as text and week as a decimal. Before
    # normalising, this merge raised outright:
    #   "You are trying to merge on int32 and string columns for key 'season'"
    merged = repo.snap_counts().merge(
        repo.ff_opportunity(), on=["season", "week"], how="inner")
    assert len(merged) == 2


def test_a_missing_week_survives_the_conversion(repo):
    # Nullable Int64 rather than plain int, because plain integers cannot hold a
    # blank and would turn a missing week into a wrong one.
    repo._loader.frames["schedules"] = pl.DataFrame(
        {"season": [2024, 2024], "week": [1.0, None], "game_id": ["a", "b"]})
    frame = repo.schedules()
    assert frame["week"].dtype == "Int64"
    assert frame["week"].isna().sum() == 1


def test_text_is_stored_as_arrow_strings_not_categories(repo):
    frame = repo.pbp()
    assert frame["posteam"].dtype == "string[pyarrow]"
    assert not isinstance(frame["posteam"].dtype, pd.CategoricalDtype)


def test_grouping_does_not_invent_rows_that_never_happened(repo):
    # The reason for the previous test. With a category column, pandas fills in
    # every combination that COULD exist, so a team on a bye appears with a
    # blank pass rate and the aggregate is silently wrong.
    frame = repo.pbp()
    grouped = frame.groupby(["season", "week", "posteam"], as_index=False)["epa"].mean()

    real_combinations = frame[["season", "week", "posteam"]].drop_duplicates()
    assert len(grouped) == len(real_combinations)
    assert not grouped["epa"].isna().any()


# ---------------------------------------------------------------------------
# The id crosswalk
# ---------------------------------------------------------------------------


def test_the_crosswalk_maps_pfr_ids_to_this_app_s_own_id(repo):
    frame = repo.player_id_crosswalk()
    assert list(frame.columns) == ["pfr_player_id", "canonical_id"]
    assert frame.loc[frame.pfr_player_id == "GoodPl00", "canonical_id"].iloc[0] == "00-0001"


def test_a_player_missing_either_id_is_dropped(repo):
    # A row with one id is no use as a bridge between the two.
    ids = set(repo.player_id_crosswalk()["pfr_player_id"])
    assert "NoGsis00" not in ids
    assert None not in ids


def test_a_player_listed_twice_appears_once(repo):
    # The source lists some players once per position they have played.
    frame = repo.player_id_crosswalk()
    assert (frame.pfr_player_id == "GoodPl00").sum() == 1


def test_an_id_pointing_at_two_players_is_dropped_not_guessed(repo):
    # Three real ids do this. There is no way to tell which player is meant, and
    # picking one would attach a stranger's snap counts to somebody's profile.
    assert "Ambig000" not in set(repo.player_id_crosswalk()["pfr_player_id"])


def test_the_crosswalk_can_never_multiply_rows_on_a_join(repo):
    # The consequence of the two tests above, stated as the property that
    # actually matters at the call site.
    crosswalk = repo.player_id_crosswalk()
    assert crosswalk["pfr_player_id"].is_unique

    snaps = repo.snap_counts()
    merged = snaps.merge(crosswalk, on="pfr_player_id", how="left")
    assert len(merged) == len(snaps)


# ---------------------------------------------------------------------------
# Loading once
# ---------------------------------------------------------------------------


def test_nothing_is_downloaded_until_it_is_asked_for(repo):
    assert repo._loader.calls == []


def test_a_table_is_downloaded_only_once(repo):
    repo.pbp(); repo.pbp(); repo.pbp()
    assert repo._loader.count("pbp") == 1


def test_each_variant_is_cached_separately(repo):
    repo.nextgen_stats("receiving")
    repo.nextgen_stats("rushing")
    repo.nextgen_stats("receiving")

    assert repo._loader.count("nextgen_stats") == 2
    kinds = {call[2] for call in repo._loader.calls if call[0] == "nextgen_stats"}
    assert kinds == {(("stat_type", "receiving"),), (("stat_type", "rushing"),)}


def test_the_configured_seasons_are_the_ones_requested(repo):
    repo.pbp()
    assert repo._loader.calls[0][1] == (2024, 2025)


def test_refreshing_reloads_everything(repo):
    repo.pbp(); repo.schedules()
    repo.refresh()
    repo.pbp(); repo.schedules()

    assert repo._loader.count("pbp") == 2
    assert repo._loader.count("schedules") == 2


def test_refreshing_one_table_leaves_the_others_alone(repo):
    repo.pbp(); repo.schedules()
    repo.refresh("pbp")
    repo.pbp(); repo.schedules()

    assert repo._loader.count("pbp") == 2
    assert repo._loader.count("schedules") == 1


def test_refreshing_a_table_clears_all_of_its_variants(repo):
    repo.nextgen_stats("receiving")
    repo.nextgen_stats("rushing")
    repo.refresh("nextgen_stats")
    repo.nextgen_stats("receiving")

    assert repo._loader.count("nextgen_stats") == 3
