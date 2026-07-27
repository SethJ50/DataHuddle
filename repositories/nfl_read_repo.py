"""Wraps nflreadpy's player data — the only non-Mongo data source.

Two separate nflreadpy pulls, cached independently:
- player_stats(): game-by-game stat rows, scoped to `seasons`. Only players
  who've actually played a recorded game appear here.
- players(): a broad player reference (~25k rows, every player who's ever
  had an NFL roster spot), independent of `seasons` and NOT stat-dependent —
  covers rookies with zero games played, which player_stats() cannot.
- teams(): NFL team reference (~36 rows: abbreviations, names, conference/
  division, colors, logos). Season-independent, like players().

Both are the heaviest loads in the app, so each is loaded once, lazily, and
held in memory for the process lifetime. Call `.refresh()`/`.refresh_players()`
for an explicit reload (e.g. during the season).
"""

import nflreadpy as nfl


class NflReadRepo:
    def __init__(self, seasons: list):
        self.seasons = seasons
        self._player_stats = None
        self._players = None
        self._teams = None

    def player_stats(self):
        if self._player_stats is None:
            self._player_stats = nfl.load_player_stats(self.seasons).to_pandas()
        return self._player_stats

    def players(self):
        if self._players is None:
            self._players = nfl.load_players().to_pandas()
        return self._players

    def teams(self):
        # Purpose: return NFL team reference data (abbr, names, conference/division,
        #          colors, logos), loaded once and cached for the process lifetime.
        # Parameters: none — load_teams() is not season-scoped, so it ignores self.seasons.
        # Returns: a pandas DataFrame, 1 row per team (~36 rows incl. historical/relocated
        #          entries), 16 columns: team_abbr, team_name, team_id, team_nick,
        #          team_conf, team_division, team_color, team_color2..4, and several
        #          logo/wordmark URL columns (team_logo_espn, team_wordmark, etc.).
        # Notes: mirrors players() — lazy so the cost is only paid if something asks
        #        for teams; cached so repeated calls in a rerun are free.
        if self._teams is None:
            self._teams = nfl.load_teams().to_pandas()
        return self._teams

    def refresh(self, seasons: list = None):
        if seasons is not None:
            self.seasons = seasons
        self._player_stats = nfl.load_player_stats(self.seasons).to_pandas()
        return self._player_stats

    def refresh_players(self):
        self._players = nfl.load_players().to_pandas()
        return self._players

    def refresh_teams(self):
        # Purpose: force a fresh pull of the teams table, bypassing the cache.
        # Parameters: none.
        # Returns: the newly loaded teams DataFrame (same structure as teams()).
        # Notes: team metadata rarely changes, so you'll seldom need this — it exists
        #        for parity with refresh()/refresh_players().
        self._teams = nfl.load_teams().to_pandas()
        return self._teams