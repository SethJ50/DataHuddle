"""Vendor-schema -> canonical-schema adapter for the Fantasy Footballers' overall board.

Every other UDK file in this app is ONE POSITION per file, which makes them a
ranking of quarterbacks against quarterbacks. This file is different and that is
the whole point of it: it is a single list running every position together, so
it says whether the analysts would take a tight end ahead of a running back.
That cross-position ordering is what the ADP Analysis page compares platform
behaviour against.

Two limitations worth knowing before using it anywhere:

  1. It is published for FULL PPR with SIX-POINT passing touchdowns only. There
     is no half-PPR or four-point variant, so this ranking does NOT change with
     a league's scoring settings even though quarterback value plainly should.
  2. It covers skill positions only -- no kickers and no team defenses.
"""

import pandas as pd

from registry import canonical_position


class UdkTop200Adapter:
    """Provides the Fantasy Footballers' single overall ranked board.

    Sits alongside `UdkRankingsAdapter`, which reads the per-position files. Both
    come from the same publisher; this one is the cross-position list.
    """

    def __init__(self, collection_repo):
        """Remember where the stored rows can be read from.

        Steps:
            1. Save the repository on the instance. Nothing is read yet; the
               database is only touched when `load` is called.

        Args:
            collection_repo: An object with a `.read()` method returning the
                stored rows as a DataFrame.
        """
        self._collection_repo = collection_repo

    def load(self) -> pd.DataFrame:
        """Read the overall board and rename its columns to the app's vocabulary.

        The only method callers need. Everything downstream works from the
        canonical column names produced here and never sees UDK's own spelling.

        Steps:
            1. Call `.read()` on the repository to pull the whole collection into
               a DataFrame.
            2. Return an empty table with the right column names if nothing is
               stored, so callers can use those columns unconditionally rather
               than guarding every access.
            3. Build the canonical table, converting the rank with
               `errors="coerce"` so a malformed value becomes NaN instead of
               raising, and mapping positions through `canonical_position` from
               registry.py.
            4. Drop rows with no rank, since a ranking row without a rank cannot
               be used for anything.
            5. Sort by rank so the best player comes first, and renumber the rows.

        Returns:
            pd.DataFrame: One row per player, sorted by rank, with columns:
                name      str    "Jahmyr Gibbs"
                position  str    canonical QB/RB/WR/TE
                team      str    NFL team abbreviation, e.g. "DET"
                ffb_rank  float  overall board position, 1 being the best

        Raises:
            KeyError: If the stored rows are missing one of UDK's expected
                columns, which would mean the export's shape changed.

        Note:
            The per-analyst columns (`Andy`, `Jason`, `Mike`) are deliberately
            dropped. They are each analyst's own rank, and their disagreement is
            a genuine signal -- but nothing consumes it yet, and carrying three
            more columns through every join for a "someday" would be clutter.
            They are one line away in the collection if ever wanted.
        """
        # One row per player. UDK's own column names: Rank, Name, Bye, Team,
        # Pos, plus one column per analyst and a Markers column.
        df = self._collection_repo.read()

        if df.empty:
            return pd.DataFrame(columns=["name", "position", "team", "ffb_rank"])

        out = pd.DataFrame({
            "name": df["Name"].astype(str).str.strip(),
            "position": df["Pos"].astype(str).map(canonical_position),
            "team": df["Team"].astype(str).str.strip(),
            "ffb_rank": pd.to_numeric(df["Rank"], errors="coerce"),
        })

        out = out.dropna(subset=["ffb_rank"])
        return out.sort_values("ffb_rank").reset_index(drop=True)
