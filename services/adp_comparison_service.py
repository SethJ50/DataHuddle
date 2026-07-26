"""
Combines ADP from ESPN, Sleeper, and Yahoo into one comparison table.

Driven by RosterService's UDK-ranked player universe: every roster player
appears (with blank ADP for any platform that doesn't have them), and any
ESPN/Sleeper/Yahoo player who ISN'T in the roster is excluded entirely.
"""

import pandas as pd
from scoring import ScoringFormat

class AdpComparisonService:


    def __init__(self, espn_adapter, sleeper_adapter, yahoo_adapter,
    identity_repo, roster_service):
        self._espn = espn_adapter
        self._sleeper = sleeper_adapter
        self._yahoo = yahoo_adapter
        self._identity_repo = identity_repo
        self._roster_service = roster_service

    def compare(self, fmt: ScoringFormat):
        """
        Purpose: Builds the full ADP comparison table, one row per roster
            (UDK-ranked) player.

        Parameters:
            fmt (ScoringFormat): HALF_PPR or FULL_PPR — passed through to
                the ESPN/Sleeper adapters (Yahoo ignores it).

        Returns:
            pd.DataFrame with one row per roster player, columns:
            canonical_id, display_name, headshot_url, position, espn_adp,
            yahoo_adp, sleeper_adp.

        Notes:
            Uses a left join from the roster, so every UDK-ranked player
            shows even if 0 of the 3 platforms have them (blank ADP in
            those columns); a platform player who ISN'T UDK-ranked never
            enters the result at all.
        """

        base = self._roster_service.roster()[
            ["canonical_id", "display_name", "headshot_url", "position"]
        ]

        espn = self._prepare("espn", self._espn.load(fmt), "espn_adp")
        sleeper = self._prepare("sleeper", self._sleeper.load(fmt), "sleeper_adp")
        yahoo = self._prepare("yahoo", self._yahoo.load(fmt), "yahoo_adp")

        # Left join: keep every roster player, attach each platform's ADP
        # where it resolves, blank otherwise.
        result = base.merge(espn, on="canonical_id", how="left")
        result = result.merge(sleeper, on="canonical_id", how="left")
        result = result.merge(yahoo, on="canonical_id", how="left")

        return result[["canonical_id", "display_name", "headshot_url", "position",
                        "espn_adp", "yahoo_adp", "sleeper_adp"]]


    def unresolved(self, source: str):
        """
        Purpose: Reports which player names from one platform could not be
            matched to a canonical_id at all -- these are the names that
            need a manual player_id_map row.

        Parameters:
            source (str): "espn", "sleeper", or "yahoo".

        Returns:
            list[str]: unresolved names, de-duplicated.
        """

        adapter = {"espn": self._espn, "sleeper": self._sleeper, "yahoo": self._yahoo}[source]
        df = adapter.load(ScoringFormat.HALF_PPR)
        return self._identity_repo.unresolved_with_fallback(source, df["name"], df["position"])

    def _prepare(self, source, df, adp_col):
        """
        Purpose: Resolves one platform's raw ADP rows to canonical_id, and
            collapses any accidental duplicate rows per player.

        Parameters:
            source (str): which platform this data came from.
            df (pd.DataFrame): that platform's raw [name, ..., adp] rows.
            adp_col (str): what to name the ADP column in the output
                (e.g. "espn_adp").

        Returns:
            pd.DataFrame with columns: canonical_id, <adp_col>. One row per
            resolved player; unresolved names are dropped (not shown blank).
        """

        canonical_id = self._identity_repo.resolve_many_with_fallback(source, df["name"], df["position"])
        out = pd.DataFrame({"canonical_id": canonical_id, adp_col: df["adp"]})
        out = out.dropna(subset=["canonical_id"])

        # Safety net: if a source ever has two rows for the same resolved
        # player, keep the lower (better) ADP rather than erroring.
        return out.groupby("canonical_id", as_index=False)[adp_col].min()