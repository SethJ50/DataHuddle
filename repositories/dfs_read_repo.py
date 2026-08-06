"""Loads the nflreadpy tables the Daily Fantasy pages need, once each.

The DFS counterpart to repositories/nfl_read_repo.py, kept separate because the
two halves of the app want different data over different seasons. Season-long
wants many years of game logs; Daily Fantasy wants a few years of nearly
everything, including play-by-play.

Every table is downloaded at most once per process and handed out cached
afterwards, because these are network downloads and a Streamlit page re-runs its
whole script on every click.

Play-by-play gets special treatment -- see `PBP_COLUMNS` below. It is the only
table here big enough to matter, and the only one that is trimmed on the way in.
"""

import pandas as pd

import nflreadpy as _nflreadpy


PBP_COLUMNS = [
    # Who, when, and which play
    "game_id", "play_id", "season", "week",
    "posteam", "defteam", "home_team", "away_team",

    # The game situation, which is what "neutral script" is defined in terms of
    "qtr", "down", "ydstogo", "yardline_100", "goal_to_go", "score_differential",
    "game_seconds_remaining", "half_seconds_remaining", "wp", "drive",

    # What kind of play it was, including the ones to exclude
    "play_type", "pass", "rush", "qb_kneel", "qb_spike", "qb_dropback",
    "special", "penalty", "two_point_attempt",

    # What happened
    "epa", "success", "yards_gained", "air_yards", "yards_after_catch",
    "complete_pass", "touchdown", "pass_touchdown", "rush_touchdown",
    "interception", "fumble_lost", "first_down",

    # The pass-rate model, for pass rate over expected
    "xpass", "pass_oe",

    # Who touched the ball, for player-level splits
    "passer_player_id", "rusher_player_id", "receiver_player_id",
]
"""The only play-by-play columns loaded. THIS LIST IS LOAD-BEARING.

nflreadpy's play-by-play table has 372 columns and weighs 372 MB per season once
converted to pandas. Three seasons of that would be over a gigabyte held for the
life of the app, for the sake of a handful of numbers.

Selecting these 44 columns while the data is still a Polars frame -- BEFORE
`.to_pandas()` -- brings a season down to 34 MB, because the conversion never
builds the other 328 columns at all. Selecting afterwards would not help; the
cost is in the conversion.

If a later feature needs a column that is not here, add it and the data reloads.
That is the expected cost of this design, not a failure of it.
"""


class DfsReadRepo:
    """Downloads and caches the nflreadpy tables behind the Daily Fantasy pages.

    Each table has a getter that loads it on first use and returns the cached
    copy every time after. Nothing is fetched when this object is built, so a
    page that never asks for snap counts never waits for them.

    The nflreadpy library itself can be swapped out through the `loader`
    argument, which is what lets the tests run without a network connection.

    Attributes:
        seasons: The seasons every season-scoped table is loaded for.
    """

    def __init__(self, seasons, loader=None):
        """Record which seasons to load, without downloading anything yet.

        Steps:
            1. Save the season list on the instance.
            2. Save the library to load through, defaulting to the real
               nflreadpy.
            3. Start with an empty cache, meaning nothing is loaded.

        Args:
            seasons: NFL seasons to load, as a list of years such as
                `[2023, 2024, 2025]`. Use `DFS_SEASONS` from streamlit_state.py,
                which is deliberately shorter than the season-long list.
            loader: Something exposing nflreadpy's `load_*` functions. Left out,
                the real library is used. The tests pass a stand-in so they need
                no network.
        """
        self.seasons = seasons
        self._loader = loader or _nflreadpy
        self._cache = {}

    def _cached(self, key, build):
        """Return a table from the cache, building it the first time only.

        The shared body of every getter below, so the load-once rule lives in one
        place rather than being repeated seven times.

        Steps:
            1. If this key has not been seen, call `build` and store the result.
            2. Return whatever is stored.

        Args:
            key: What to file this table under. A tuple for tables that come in
                variants, so `("nextgen_stats", "rushing")` and
                `("nextgen_stats", "passing")` are cached apart.
            build: A no-argument function that loads and returns the table.

        Returns:
            pd.DataFrame: The cached table.
        """
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    JOIN_KEYS = ("season", "week")
    """Columns forced to the same type in every table. See `_to_pandas` below."""

    @classmethod
    def _to_pandas(cls, frame, columns=None):
        """Convert a Polars frame to pandas, trimming, compacting and squaring it up.

        Steps:
            1. Narrow to the requested columns first, if any were given. Doing
               this BEFORE converting is the whole trick for play-by-play -- see
               `PBP_COLUMNS` above.
            2. Convert to pandas.
            3. Force `season` and `week` to whole numbers, so tables from
               different sources can be joined. See the note.
            4. Store the remaining text columns as Arrow-backed strings, which
               hold the same values in far less memory.

        Args:
            frame: The Polars frame nflreadpy returned.
            columns: Which columns to keep. None keeps all of them.

        Returns:
            pd.DataFrame: The converted table.

        Note:
            SEASON AND WEEK ARE NOT THE SAME TYPE ACROSS SOURCES, which is the
            single most annoying thing about combining these tables. Six of the
            seven store both as whole numbers; `ff_opportunity` stores the season
            as TEXT and the week as a DECIMAL. Joining it to anything else fails
            outright with "You are trying to merge on int32 and string columns",
            and that is the good case -- a silent mismatch would be worse.

            Step 3 settles it here, once, so nothing downstream has to remember.
            The type used is pandas' nullable `Int64` rather than plain `int`
            because a missing week has to survive the conversion; plain integers
            cannot hold a blank.

            ARROW STRINGS RATHER THAN pandas CATEGORIES, deliberately. Categories
            would save a little more memory, but they change what `groupby` does:
            with a category column pandas fills in every combination that COULD
            exist, so grouping three seasons of plays by season, week and team
            returns 704 rows instead of the 570 that really happened -- the extra
            134 being teams on a bye, with a blank pass rate.

            That is a silent wrong answer, and avoiding it would mean every
            `groupby` in the app remembering `observed=True` forever. Arrow
            strings save 45% of the memory and behave exactly like ordinary text.
        """
        if columns is not None:
            frame = frame.select(columns)

        table = frame.to_pandas()

        for key in cls.JOIN_KEYS:
            if key in table.columns:
                table[key] = pd.to_numeric(table[key], errors="coerce").astype("Int64")

        for column in table.columns:
            if table[column].dtype == object:
                table[column] = table[column].astype("string[pyarrow]")
        return table

    def pbp(self):
        """Get play-by-play data, trimmed to the columns this app uses.

        The source behind every team tendency number: pass rate in neutral game
        situations, pace, and the efficiency each defence allows. One row per
        play.

        Steps:
            1. Call nflreadpy's `load_pbp` for the configured seasons.
            2. Hand it to `_to_pandas` above with `PBP_COLUMNS`, which trims it
               before converting.

        Returns:
            pd.DataFrame: One row per play, roughly 50,000 per season, with the
                44 columns listed in `PBP_COLUMNS`. About 19 MB per season.

        Note:
            The single heaviest thing the DFS pages load, and the reason this
            repository exists rather than pages calling nflreadpy directly.
        """
        return self._cached(
            "pbp",
            lambda: self._to_pandas(self._loader.load_pbp(self.seasons),
                                    PBP_COLUMNS),
        )

    def ff_opportunity(self):
        """Get expected fantasy points, one row per player per week.

        "Expected" means what a player's opportunities were worth on average,
        regardless of how they turned out -- a target on the goal line is worth
        more than one at midfield whether or not it was caught. Comparing it to
        what he actually scored is how you tell production from luck.

        Steps:
            1. Call nflreadpy's `load_ff_opportunity` for the configured seasons,
               asking for the weekly summary rather than play-level detail.
            2. Convert it with `_to_pandas` above.

        Returns:
            pd.DataFrame: About 6,000 rows per season and 159 columns, including
                `player_id` (which is the same id this app calls `canonical_id`),
                `full_name`, `position`, `posteam`, `season`, `week`, and
                actual/expected pairs such as `total_fantasy_points` against
                `total_fantasy_points_exp`.

        Note:
            THESE NUMBERS ARE FULL PPR. The app defaults to FanDuel's half-PPR,
            so they are converted before display -- see services/dfs_scoring.py.
        """
        return self._cached(
            "ff_opportunity",
            lambda: self._to_pandas(
                self._loader.load_ff_opportunity(self.seasons,
                                                 stat_type="weekly")),
        )

    def snap_counts(self):
        """Get how many snaps each player was on the field for, per game.

        The purest measure of opportunity there is: a player cannot score points
        from the sideline, and a rising snap share usually shows up before the
        production does.

        Steps:
            1. Call nflreadpy's `load_snap_counts` for the configured seasons.
            2. Convert it with `_to_pandas` above.

        Returns:
            pd.DataFrame: About 27,000 rows per season, including `player`,
                `position`, `offense_snaps` and `offense_pct`.

        Note:
            KEYED BY `pfr_player_id`, NOT by this app's `canonical_id`. Use
            `player_id_crosswalk` below to join it to anything else.
        """
        return self._cached(
            "snap_counts",
            lambda: self._to_pandas(self._loader.load_snap_counts(self.seasons)),
        )

    def nextgen_stats(self, stat_type):
        """Get NFL Next Gen Stats -- the tracking-camera numbers.

        Things no box score contains, because they come from chips in the
        players' shoulder pads: how much separation a receiver got, how far
        downfield he was targeted, how long a quarterback held the ball.

        Steps:
            1. Call nflreadpy's `load_nextgen_stats` for the configured seasons
               and the requested kind of stat.
            2. Convert it with `_to_pandas` above.

        Args:
            stat_type: Which set to load -- `"passing"`, `"rushing"` or
                `"receiving"`. Each is cached separately, so asking for two kinds
                downloads two tables.

        Returns:
            pd.DataFrame: Roughly 1,500 rows per season per kind. Identifies
                players by `player_gsis_id`, which is this app's `canonical_id`
                under a different name.
        """
        return self._cached(
            ("nextgen_stats", stat_type),
            lambda: self._to_pandas(
                self._loader.load_nextgen_stats(self.seasons,
                                                stat_type=stat_type)),
        )

    def pfr_advstats(self, stat_type):
        """Get Pro Football Reference's charted advanced stats.

        Hand-charted detail that the official play-by-play does not record:
        dropped passes, how often a quarterback was pressured, how many rushing
        yards came after first contact.

        Steps:
            1. Call nflreadpy's `load_pfr_advstats` for the configured seasons
               and the requested kind of stat.
            2. Convert it with `_to_pandas` above.

        Args:
            stat_type: Which set to load -- `"pass"`, `"rush"`, `"rec"` or
                `"def"`. Each is cached separately.

        Returns:
            pd.DataFrame: A few thousand rows per season per kind.

        Note:
            KEYED BY `pfr_player_id`, like snap counts above. Join it through
            `player_id_crosswalk` below.
        """
        return self._cached(
            ("pfr_advstats", stat_type),
            lambda: self._to_pandas(
                self._loader.load_pfr_advstats(self.seasons,
                                               stat_type=stat_type)),
        )

    def schedules(self):
        """Get the season's fixtures, including the betting lines.

        Two jobs: saying who played whom in which week, and carrying the Vegas
        point spread and over/under. The betting line is the market's forecast of
        how many points a game will produce, which is one of the better single
        predictors of fantasy scoring available before kickoff.

        Steps:
            1. Call nflreadpy's `load_schedules` for the configured seasons.
            2. Convert it with `_to_pandas` above.

        Returns:
            pd.DataFrame: About 285 rows per season, one per game, including
                `game_id`, `season`, `week`, `home_team`, `away_team`,
                `spread_line` and `total_line`.
        """
        return self._cached(
            "schedules",
            lambda: self._to_pandas(self._loader.load_schedules(self.seasons)),
        )

    def teams(self):
        """Get team reference data, including the logo images pages draw with.

        Steps:
            1. Call nflreadpy's `load_teams`.
            2. Convert it with `_to_pandas` above.

        Returns:
            pd.DataFrame: About 36 rows -- one per team, including relocated and
                historical ones -- with `team_abbr`, `team_name`, `team_color`
                and several logo URLs such as `team_logo_espn`.

        Note:
            Not season-scoped, so it ignores `self.seasons`. Duplicated from
            `NflReadRepo.teams` on purpose: the DFS half of the app should not
            have to reach into the season-long repository to draw a chart, and
            nflreadpy caches the download so asking twice costs nothing.
        """
        return self._cached("teams", lambda: self._to_pandas(self._loader.load_teams()))

    def player_id_crosswalk(self):
        """Build the bridge from Pro Football Reference's player ids to this app's.

        Snap counts and PFR's advanced stats identify players by a Pro Football
        Reference id, and everything else in this app uses the NFL's own id. This
        is the lookup table that connects them, and without it neither of those
        sources can be joined to anything.

        Steps:
            1. Call nflreadpy's `load_ff_playerids`, a reference table that
               collects the ids every fantasy site uses for the same player.
            2. Keep only the rows that carry both ids, since a row with one is no
               use as a bridge.
            3. Drop repeated rows -- the table lists some players twice, once per
               position they have played.
            4. Drop any PFR id that still points at more than one player. Three of
               them do, which is an error in the source data, and there is no way
               to tell which player is meant.
            5. Rename the NFL id to `canonical_id`, the name the rest of this app
               uses for it.

        Returns:
            pd.DataFrame: About 7,800 rows with two columns, `pfr_player_id` and
                `canonical_id`. One row per player, so merging with it can never
                duplicate rows.

        Note:
            IT DOES NOT COVER EVERYONE, and the gap is the point of step 4. Across
            three seasons it resolves 98% of quarterbacks, backs, receivers and
            tight ends; the misses are practice-squad players with a handful of
            snaps. Rows that fail to resolve must be dropped by the caller rather
            than left blank, because a missing snap count reads as "did not play"
            when it actually means "could not look him up".
        """
        def build():
            table = self._to_pandas(self._loader.load_ff_playerids())
            table = table[["pfr_id", "gsis_id"]].dropna()
            table = table.drop_duplicates()

            # A PFR id pointing at two different players cannot be resolved, so
            # it is dropped rather than guessed at.
            counts = table["pfr_id"].value_counts()
            ambiguous = counts[counts > 1].index
            table = table[~table["pfr_id"].isin(ambiguous)]

            return table.rename(columns={"pfr_id": "pfr_player_id",
                                         "gsis_id": "canonical_id"}
                                ).reset_index(drop=True)

        return self._cached("player_id_crosswalk", build)

    def refresh(self, name=None):
        """Forget cached tables so the next call downloads them again.

        Worth calling during the season, when new games have been played and the
        copy loaded at startup has gone stale.

        Steps:
            1. With no name, empty the whole cache.
            2. With a name, forget just that table, including every variant of it
               if it is one of the ones that comes in kinds.

        Args:
            name: Which table to forget, such as `"pbp"` or `"nextgen_stats"`.
                Leave it out to forget everything.

        Returns:
            None. The next getter call does the reloading.
        """
        if name is None:
            self._cache.clear()
            return

        for key in [k for k in self._cache
                    if k == name or (isinstance(k, tuple) and k[0] == name)]:
            del self._cache[key]
