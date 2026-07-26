"""Defines the app's player universe.

Only players ranked in UDK's QB/RB/WR/TE rankings are "in scope" for this
app. Everything else that needs a player list — Player Profile's dropdown,
ADP Platform Comparison, own-projections — filters down to this roster
rather than showing every player nflreadpy has ever heard of.
"""

import pandas as pd


class RosterService:
    def __init__(self, udk_rankings_adapter, identity_repo, player_directory):
        self._udk_adapter = udk_rankings_adapter
        self._identity_repo = identity_repo
        self._player_directory = player_directory

    def roster(self) -> pd.DataFrame:
        """
        Purpose: The full in-scope player roster -- one row per UDK-ranked
            player successfully resolved to a canonical_id.

        Returns:
            pd.DataFrame with columns: canonical_id, display_name,
            headshot_url, position, team, bye_week, rank, points, risk,
            upside, adp, tier. UDK players who can't be resolved to a
            canonical_id at all are excluded -- see unresolved().

        Notes:
            If a player is (mistakenly) ranked in more than one position
            file, only the best (lowest) rank is kept.
        """
        udk = self._udk_adapter.load()

        canonical_id = self._identity_repo.resolve_many_with_fallback(
            "udk", udk["name"], udk["position"]
        )
        udk = udk.assign(canonical_id=canonical_id).dropna(subset=["canonical_id"])
        udk = udk.sort_values("rank").drop_duplicates(subset="canonical_id", keep="first")

        udk["display_name"] = udk["canonical_id"].apply(self._player_directory.get_display_name)
        udk["headshot_url"] = udk["canonical_id"].apply(self._player_directory.get_headshot)

        return udk[[
            "canonical_id", "display_name", "headshot_url", "position", "team",
            "bye_week", "rank", "points", "risk", "upside", "adp", "tier",
        ]]

    def canonical_ids(self) -> set:
        """Purpose: the bare set of in-scope canonical_ids, for restricting
        other data sources (projections, ADP, etc.) to just these players."""
        return set(self.roster()["canonical_id"])

    def player_names(self) -> dict:
        """Purpose: {canonical_id: display_name}, ready for a Shiny
        input_selectize's `choices=` -- the roster-scoped equivalent of
        PlayerDirectory.search_names()."""
        roster = self.roster()
        return dict(zip(roster["canonical_id"], roster["display_name"]))

    def unresolved(self) -> list:
        """Purpose: UDK player names that couldn't be matched to a
        canonical_id at all -- needs either a manual player_id_map row, or
        nflreadpy genuinely has no record of them yet (e.g. a very recent
        signing/draftee)."""
        udk = self._udk_adapter.load()
        return self._identity_repo.unresolved_with_fallback("udk", udk["name"], udk["position"])