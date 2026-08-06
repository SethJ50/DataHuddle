"""Wraps nflreadpy's player data — the only non-Mongo data source.

Three separate nflreadpy pulls, cached independently:
- player_stats(): game-by-game stat rows, scoped to `seasons`. Only players
  who've actually played a recorded game appear here.
- players(): a broad player reference (~25k rows, every player who's ever
  had an NFL roster spot), independent of `seasons` and NOT stat-dependent —
  covers rookies with zero games played, which player_stats() cannot.
- teams(): NFL team reference (~36 rows: abbreviations, names, conference/
  division, colors, logos). Season-independent, like players().

All are among the heaviest loads in the app, so each is loaded once, lazily, and
held in memory for the process lifetime. Call `.refresh()`/`.refresh_players()`
for an explicit reload (e.g. during the season).
"""

import nflreadpy as nfl


class NflReadRepo:
    """Loads and caches the three nflreadpy tables the app depends on.

    nflreadpy downloads public NFL data files over the network, which is slow
    enough that doing it on every Streamlit rerun would make the app unusable.
    This class loads each table at most once per process and hands out the
    cached copy afterwards.

    Each table has a lazy getter (`player_stats`, `players`, `teams`) and a
    matching `refresh_*` method that forces a re-download.
    """

    def __init__(self, seasons: list):
        """Record which seasons to pull stats for, without loading anything yet.

        Building this is deliberately instant. The expensive downloads are
        deferred until a getter is actually called, so a page that never shows
        stats never pays for them.

        Steps:
            1. Save the list of seasons on the instance.
            2. Set all three caches to None, meaning "not loaded yet".

        Args:
            seasons: The NFL seasons to load game stats for, as a list of years
                such as `[2024, 2025]`. This affects `player_stats` only;
                `players` and `teams` are not season-scoped.
        """
        self.seasons = seasons
        self._player_stats = None
        self._players = None
        self._teams = None

    def player_stats(self):
        """Get game-by-game stat lines for the configured seasons.

        This is the source behind game logs and any season-to-date totals. It
        only covers players who actually appeared in a recorded game, so use
        `players` below when you need rookies or anyone with no snaps yet.

        Steps:
            1. If the cache is still None, call nflreadpy's `load_player_stats`
               for the configured seasons.
            2. Convert the result from a Polars DataFrame to a pandas one, since
               the rest of the app works in pandas.
            3. Store it in the cache and return it.

        Returns:
            pd.DataFrame: One row per player per game, with a wide set of
                columns covering passing, rushing, and receiving stats, plus
                identifying columns such as `player_id`, `player_display_name`,
                `season`, `week`, and `recent_team`.
        """
        if self._player_stats is None:
            self._player_stats = nfl.load_player_stats(self.seasons).to_pandas()
        return self._player_stats

    def players(self):
        """Get the broad reference list of every player nflreadpy knows about.

        Used to look players up by name or id, including rookies who have not
        played a game and therefore do not appear in `player_stats` at all.

        Steps:
            1. If the cache is still None, call nflreadpy's `load_players`.
            2. Convert the result to pandas and store it in the cache.

        Returns:
            pd.DataFrame: Roughly 25,000 rows, one per player who has ever held
                an NFL roster spot. Key columns include `gsis_id` (the stable
                canonical id used throughout this app), `display_name`,
                `position`, `team_abbr`, and `status`. Not season-scoped, so it
                ignores `self.seasons` entirely.
        """
        if self._players is None:
            self._players = nfl.load_players().to_pandas()
        return self._players

    def teams(self):
        """Get NFL team reference data such as names, divisions, colors, and logos.

        Backs anything that needs to display a team properly rather than as a
        bare abbreviation: the team profile page, colored badges, and logos.

        Steps:
            1. If the cache is still None, call nflreadpy's `load_teams`.
            2. Convert the result to pandas and store it in the cache.

        Returns:
            pd.DataFrame: About 36 rows, one per team including historical and
                relocated entries, and 16 columns: `team_abbr`, `team_name`,
                `team_id`, `team_nick`, `team_conf`, `team_division`,
                `team_color` through `team_color4`, and several logo and
                wordmark URL columns such as `team_logo_espn` and
                `team_wordmark`.

        Note:
            Mirrors `players` above: lazy, so the cost is only paid if something
            asks for teams, and cached, so repeated calls during a rerun are
            free. Not season-scoped, so it ignores `self.seasons`.
        """
        if self._teams is None:
            self._teams = nfl.load_teams().to_pandas()
        return self._teams

    def refresh(self, seasons: list = None):
        """Re-download game stats, optionally for a different set of seasons.

        Needed during the season, when new games are played and the cached copy
        goes stale while the app is still running. Also the only way to change
        which seasons this repository covers after it has been built.

        Steps:
            1. If a new season list was passed, replace the stored one, so all
               later calls to `player_stats` use it too.
            2. Call nflreadpy's `load_player_stats` unconditionally, skipping
               the cache check.
            3. Overwrite the cache with the fresh result and return it.

        Args:
            seasons: Optional replacement list of seasons. Leave it out to
                reload the seasons already configured.

        Returns:
            pd.DataFrame: The freshly loaded stats, in the same shape
                `player_stats` describes.
        """
        if seasons is not None:
            self.seasons = seasons
        self._player_stats = nfl.load_player_stats(self.seasons).to_pandas()
        return self._player_stats

    def refresh_players(self):
        """Re-download the player reference list, bypassing the cache.

        Worth calling after roster moves or signings, since a player who was not
        in nflreadpy's list when the app started will not appear until this runs.

        Steps:
            1. Call nflreadpy's `load_players` unconditionally.
            2. Overwrite the cache with the fresh result and return it.

        Returns:
            pd.DataFrame: The freshly loaded player reference, in the same shape
                `players` describes.
        """
        self._players = nfl.load_players().to_pandas()
        return self._players

    def refresh_teams(self):
        """Re-download the team reference table, bypassing the cache.

        Team metadata rarely changes, so you will seldom need this. It exists
        mainly so all three cached tables can be refreshed the same way.

        Steps:
            1. Call nflreadpy's `load_teams` unconditionally.
            2. Overwrite the cache with the fresh result and return it.

        Returns:
            pd.DataFrame: The freshly loaded teams table, in the same shape
                `teams` describes.
        """
        self._teams = nfl.load_teams().to_pandas()
        return self._teams
