"""Reads the current-season FFC ADP snapshot.

The "what is true right now" half of the FFC storage split (DESIGN.md 5.1).
History lives in AdpSnapshotRepo and is never overwritten; this collection is
wiped and replaced on every load_data run, exactly like espn_projections.

Thin on purpose. It exists so nothing outside this file has to know the
collection name, and so restoring proper dtypes after a Mongo round trip
happens in one place.
"""

import pandas as pd

from adapters.ffc_adapter import FFC_FORMAT_BY_SCORING
from repositories.collection_repo import CollectionRepo
from registry import Collections
from scoring import ScoringFormat

NUMERIC_COLUMNS = ("adp", "stdev", "high", "low", "times_drafted", "bye")


class FfcRepo:
    """Cache-backed reader for the current-season FFC ADP table.

    The collection holds ALL THREE scoring formats at once, tagged with a
    `format` column, because scoring format is a per-draft setting -- one league
    may be half-PPR while another is standard, and FFC returns a genuinely
    different player pool for each (246 / 204 / 186 in 2026).
    """

    def __init__(self, collection_repo=None):
        """
        Parameters:
            collection_repo (CollectionRepo | None): Injected so tests can pass a
                stub. Defaults to the real ffc_adp collection.
        """
        self._collection_repo = collection_repo or CollectionRepo(Collections.FFC_ADP)

    def current(self, fmt: ScoringFormat | None = None) -> pd.DataFrame:
        """
        Purpose: The latest stored FFC ADP table, optionally for one scoring format.

        Parameters:
            fmt (ScoringFormat | None): REGULAR / HALF_PPR / FULL_PPR. Omit to get
                every format at once (rows stay distinguishable via the `format`
                column).

        Returns:
            pd.DataFrame -- the adapter's shape plus provenance:
                ffc_player_id, name, position, team, adp, stdev, high, low,
                times_drafted, bye, format, season, pulled_at
            Sorted by adp. Empty DataFrame if load_data hasn't run yet.

        Notes:
            Mongo doesn't preserve pandas' int/float distinction on read-back, so
            numeric columns are re-coerced here. `stdev` legitimately contains NaN
            (the adapter turns FFC's unmeasurable 0 into NaN), so coercion must
            never fill it -- errors="coerce" preserves NaN rather than zeroing it.

            Asking for a format that wasn't loaded returns an EMPTY frame rather
            than raising. Callers should check, since a silent empty pool would
            produce a simulation of nothing.
        """
        df = self._collection_repo.read()
        if df.empty:
            return df

        if fmt is not None:
            df = df[df["format"] == FFC_FORMAT_BY_SCORING[fmt]]
            if df.empty:
                return df

        df = df.copy()
        for column in NUMERIC_COLUMNS:
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

        return df.sort_values("adp").reset_index(drop=True)

    def available_formats(self) -> list:
        """
        Purpose: Which scoring formats are actually loaded -- so a caller can fail
            loudly and early instead of simulating an empty pool.

        Returns:
            list[str]: FFC format strings present, e.g. ["half-ppr", "ppr", "standard"].
        """
        df = self._collection_repo.read()
        if df.empty or "format" not in df.columns:
            return []
        return sorted(df["format"].dropna().unique().tolist())

    def refresh(self, fmt: ScoringFormat | None = None) -> pd.DataFrame:
        """Force a re-read after load_data has rewritten the collection,
        without restarting the app."""
        self._collection_repo.refresh()
        return self.current(fmt)
