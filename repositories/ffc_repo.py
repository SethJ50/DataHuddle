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

# Columns that must be numbers for any calculation to work. MongoDB does not
# remember whether a stored value was an int or a float, so these get converted
# back explicitly on every read.
NUMERIC_COLUMNS = ("adp", "stdev", "high", "low", "times_drafted", "bye")


class FfcRepo:
    """Cache-backed reader for the current-season FFC ADP table.

    The collection holds ALL THREE scoring formats at once, tagged with a
    `format` column, because scoring format is a per-draft setting -- one league
    may be half-PPR while another is standard, and FFC returns a genuinely
    different player pool for each (246 / 204 / 186 in 2026).
    """

    def __init__(self, collection_repo=None):
        """Set up where this repository reads its rows from.

        The data source is optional so normal app code can write `FfcRepo()` and
        get the real collection, while tests can pass a stub instead.

        Steps:
            1. If a repository was supplied, keep it.
            2. Otherwise build a `CollectionRepo` pointed at the real `ffc_adp`
               collection, whose name comes from `Collections` in registry.py.

        Args:
            collection_repo: An object with `.read()` and `.refresh()` methods
                returning the stored FFC rows. Defaults to the real collection.
        """
        self._collection_repo = collection_repo or CollectionRepo(Collections.FFC_ADP)

    def current(self, fmt: ScoringFormat | None = None) -> pd.DataFrame:
        """Get the latest stored FFC ADP table, optionally for one scoring format.

        This is the main read. Filtering by format matters because the three
        formats have genuinely different player pools, so mixing them would
        double-count players in anything that groups by name.

        Steps:
            1. Call `.read()` on the collection repository to get every stored
               row, using its cache after the first call.
            2. Return early if nothing has been loaded yet.
            3. If a format was requested, translate it to FFC's own spelling via
               FFC_FORMAT_BY_SCORING and keep only matching rows, returning
               early if that leaves nothing.
            4. Copy the table so the conversions below do not alter the cached
               copy that other callers share.
            5. Convert each of the NUMERIC_COLUMNS back to numbers.
            6. Sort by ADP so the earliest-drafted player is first, and renumber
               the rows.

        Args:
            fmt: REGULAR, HALF_PPR, or FULL_PPR. Omit to get every format at
                once; the rows stay distinguishable via the `format` column.

        Returns:
            pd.DataFrame: The adapter's shape plus provenance, with columns
                `ffc_player_id`, `name`, `position`, `team`, `adp`, `stdev`,
                `high`, `low`, `times_drafted`, `bye`, `format`, `season`, and
                `pulled_at`. Sorted by `adp`. An empty DataFrame if load_data
                has not run yet.

        Note:
            MongoDB does not preserve pandas' int/float distinction on read-back,
            so numeric columns are re-coerced here. `stdev` legitimately contains
            NaN (the adapter turns FFC's unmeasurable 0 into NaN), so the
            conversion must never fill it -- `errors="coerce"` preserves NaN
            rather than zeroing it.

            Asking for a format that was not loaded returns an EMPTY table rather
            than raising. Callers should check, since a silent empty pool would
            produce a simulation of nothing.
        """
        # One row per (player, scoring format), carrying FFC's ADP fields plus
        # the provenance columns listed in Returns.
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
        """List which scoring formats actually have data loaded.

        Lets a caller fail loudly and early rather than simulating an empty
        player pool, which looks like a working run that produces nonsense.

        Steps:
            1. Call `.read()` on the collection repository.
            2. Return an empty list if nothing is stored or the `format` column
               is missing entirely.
            3. Take the distinct non-missing values of `format` and sort them.

        Returns:
            list: The FFC format strings present, for example
                `["half-ppr", "ppr", "standard"]`. Note these are FFC's spellings
                rather than the app's `ScoringFormat` values.
        """
        df = self._collection_repo.read()
        if df.empty or "format" not in df.columns:
            return []
        return sorted(df["format"].dropna().unique().tolist())

    def refresh(self, fmt: ScoringFormat | None = None) -> pd.DataFrame:
        """Re-read the collection after load_data has rewritten it.

        Without this, a running app keeps serving the rows it cached at startup
        even after fresh ADP has been loaded into MongoDB.

        Steps:
            1. Call `.refresh()` on the collection repository, which throws away
               its cache and downloads the collection again.
            2. Call `current` above to apply the usual filtering, numeric
               conversion, and sorting to the fresh rows.

        Args:
            fmt: Optional scoring format to filter to, exactly as in `current`.

        Returns:
            pd.DataFrame: The freshly loaded table, in the same shape `current`
                describes.
        """
        self._collection_repo.refresh()
        return self.current(fmt)
