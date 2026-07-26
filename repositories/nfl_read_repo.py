"""Wraps nflreadpy's player data — the only non-Mongo data source.

Two separate nflreadpy pulls, cached independently:
- player_stats(): game-by-game stat rows, scoped to `seasons`. Only players
  who've actually played a recorded game appear here.
- players(): a broad player reference (~25k rows, every player who's ever
  had an NFL roster spot), independent of `seasons` and NOT stat-dependent —
  covers rookies with zero games played, which player_stats() cannot.

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

    def player_stats(self):
        if self._player_stats is None:
            self._player_stats = nfl.load_player_stats(self.seasons).to_pandas()
        return self._player_stats

    def players(self):
        if self._players is None:
            self._players = nfl.load_players().to_pandas()
        return self._players

    def refresh(self, seasons: list = None):
        if seasons is not None:
            self.seasons = seasons
        self._player_stats = nfl.load_player_stats(self.seasons).to_pandas()
        return self._player_stats

    def refresh_players(self):
        self._players = nfl.load_players().to_pandas()
        return self._players