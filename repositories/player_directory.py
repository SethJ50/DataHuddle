import pandas as pd

"""Player identity/lookup, keyed by canonical_id.

canonical_id is nflreadpy's stable gsis-style ID — `gsis_id` in
NflReadRepo.players(), the same value as `player_id` in
NflReadRepo.player_stats() (confirmed: same ID scheme, both tables).

Identity/profile lookups (name, headshot, position, team) use players(),
a broad ~25k-row reference covering every rostered player ever, INCLUDING
rookies with zero recorded games — player_stats() only has rows for players
who've actually played, which would silently exclude those rookies. Game
logs still come from player_stats(), since only that table has game-by-game
rows.
"""

DEFAULT_HEADSHOT = "www/defaultPlayer.png"


class PlayerDirectory:
    def __init__(self, nfl_read_repo):
        self._nfl_read_repo = nfl_read_repo

    def _stats(self):
        return self._nfl_read_repo.player_stats()

    def _players(self):
        return self._nfl_read_repo.players()

    def search_names(self, query: str = "", positions: list = None) -> dict:
        """Returns {canonical_id: display_name}, suited directly for a Shiny
        input_selectize's `choices=` dict, optionally filtered by position
        and/or a case-insensitive substring of the display name."""
        df = self._players()

        if positions:
            df = df[df["position"].isin(positions)]
        if query:
            df = df[df["display_name"].str.contains(query, case=False, na=False)]

        unique = (
            df[["gsis_id", "display_name"]]
            .dropna()
            .drop_duplicates(subset="gsis_id")
            .sort_values("display_name")
        )
        return dict(zip(unique["gsis_id"], unique["display_name"]))

    def get_display_name(self, canonical_id):
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "display_name" in rows.columns:
            names = rows["display_name"].dropna()
            if not names.empty:
                return str(names.iloc[0])

        return None

    def get_headshot(self, canonical_id):
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "headshot" in rows.columns:
            urls = rows["headshot"].dropna()
            if not urls.empty:
                return str(urls.iloc[0])

        return DEFAULT_HEADSHOT

    def get_position(self, canonical_id):
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "position" in rows.columns:
            positions = rows["position"].dropna()
            if not positions.empty:
                return str(positions.iloc[0])

        return None

    def get_team(self, canonical_id):
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "latest_team" in rows.columns:
            teams = rows["latest_team"].dropna()
            if not teams.empty:
                return str(teams.iloc[0])

        return None

    def get_gamelog(self, canonical_id):
        """Returns all game weeks for a specific player."""
        rows = self._stats()
        return rows[rows["player_id"] == canonical_id].copy()

    def resolve_by_display_name(self, names: pd.Series, positions: pd.Series = None):
        '''
            Given name(s) from an outside source, attempt to get the player's
            stable 'canonical_id', return NaN otherwise.
        '''

        df = (
            self._players()[["gsis_id", "display_name", "position"]]
                .dropna(subset=["display_name"])
                .drop_duplicates(subset="gsis_id")
        )
        df["_key"] = df["display_name"].str.strip().str.lower()
        keys = names.str.strip().str.lower()

        if positions is not None:
            df["_key"] = df["_key"] + "|" + df["position"].str.strip().str.upper()
            keys = keys + "|" + positions.str.strip().str.upper()

        lookup = df.drop_duplicates(subset="_key").set_index("_key")["gsis_id"]

        return keys.map(lookup)