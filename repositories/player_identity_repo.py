"""Resolves source-specific player names to a stable canonical player ID.

canonical_id is nflreadpy's stable internal ID (player_id/gsis_id) — not the
display name, since display names can collide across players or shift
formatting between nflreadpy releases. Mapping rows live in the player_id_map
collection and are curated by hand (see PLANNING.md: explicitly not
fuzzy-matched), loaded like any other CSV via scripts/load_data.py.

The problem this solves: ESPN calls him "Kenneth Walker III", Sleeper might
write "Kenneth Walker", and joining two tables on those strings would fail. Both
resolve to one canonical id, and every join in the app uses that instead.
"""

from typing import Iterable, Optional

import pandas as pd

from repositories.collection_repo import CollectionRepo


class PlayerIdentityRepo:
    """Translates one data source's player names into canonical player IDs.

    Every external source spells names its own way, so the app keeps a
    hand-curated mapping table and looks names up in it rather than guessing.
    Lookups come in two flavours: the plain `resolve*` methods that use only the
    curated table, and the `*_with_fallback` methods that additionally try an
    exact name match against nflreadpy's own player list.
    """

    def __init__(self, collection_repo: CollectionRepo, player_directory):
        """Store the two lookup sources this repository will consult.

        Both are handed in rather than created here, which keeps this class
        testable and lets the app share one cached copy of each.

        Steps:
            1. Save the mapping table's repository on the instance.
            2. Save the player directory, used only by the fallback methods.

        Args:
            collection_repo: A `CollectionRepo` wrapping the `player_id_map`
                collection, the hand-curated mapping table.
            player_directory: A `PlayerDirectory` used as a second chance when
                the curated table has no row for a name.
        """
        self._collection_repo = collection_repo
        self._player_directory = player_directory

    def _mapping(self) -> pd.DataFrame:
        """Read the hand-curated name mapping table.

        A one-line helper so every method below goes through the same call and
        benefits from the repository's caching rather than re-reading the
        collection.

        Steps:
            1. Call `.read()` on the collection repository, which downloads the
               table on the first call and returns the cached copy afterwards.

        Returns:
            pd.DataFrame: One row per curated mapping, with the columns
                `source` (which platform the name came from, e.g. "espn"),
                `source_name` (exactly how that platform spells it), and
                `canonical_id` (the nflreadpy id it means). Empty if nothing has
                been loaded into the collection yet.
        """
        return self._collection_repo.read()

    def resolve(self, source: str, source_name: str) -> Optional[str]:
        """Look up the canonical ID for a single player name from one source.

        Use this for one-off lookups. When translating a whole column of names,
        use `resolve_many` below instead, which is far faster.

        Steps:
            1. Call `_mapping` above to get the curated table.
            2. If it is empty, there is nothing to match against, so give up
               immediately.
            3. Filter to rows where both the source and the spelled name match.
            4. If nothing matched, return None. Otherwise take the first
               matching row's canonical id.

        Args:
            source: Which platform the name came from, for example "espn".
                Matching is on the exact string, so it must be spelled the same
                way as in the mapping table.
            source_name: The player's name exactly as that platform writes it.

        Returns:
            str | None: The canonical player id, or None when this name has no
                curated row. None is normal rather than an error, so callers
                have to handle it.
        """
        df = self._mapping()
        if df.empty:
            return None

        match = df[(df["source"] == source) & (df["source_name"] == source_name)]
        if match.empty:
            return None
        return str(match.iloc[0]["canonical_id"])

    def resolve_many(self, source: str, names: pd.Series) -> pd.Series:
        """Translate a whole column of player names into canonical IDs at once.

        This is the fast version of `resolve` above. It does one pass over the
        mapping table instead of one search per name, which matters when
        translating hundreds of players.

        Steps:
            1. Call `_mapping` above to get the curated table.
            2. If it is empty, return a column of None values that still lines
               up with the input, so callers can treat the result uniformly.
            3. Filter to this source's rows and turn them into a lookup keyed by
               the source's spelling.
            4. Use pandas' `map` to swap every name for its canonical id. Any
               name not in the lookup becomes NaN automatically.

        Args:
            source: Which platform the names came from, for example "sleeper".
            names: A column of player names, one per row, spelled the way that
                platform spells them.

        Returns:
            pd.Series: Canonical ids, positioned to line up with `names` row for
                row, so it can be assigned straight back onto the same
                DataFrame. Unresolved names come back as NaN or None.
        """
        df = self._mapping()
        if df.empty:
            return pd.Series([None] * len(names), index=names.index)

        source_map = df[df["source"] == source].set_index("source_name")["canonical_id"]
        return names.map(source_map)

    def unresolved(self, source: str, names: Iterable[str]) -> list:
        """List the names this source uses that have no curated mapping row.

        Surfaced deliberately rather than swallowed: an unmapped player would
        otherwise vanish from whatever table is being joined, with no sign that
        anything went missing. The output is the to-do list for hand-editing the
        mapping file.

        Steps:
            1. Call `_mapping` above to get the curated table.
            2. Collect every name this source already has a row for into a set,
               which makes the membership checks in step 3 fast.
            3. Walk the input names, using `dict.fromkeys` to drop duplicates
               while keeping the original order, and keep only those absent from
               the set.

        Args:
            source: Which platform the names came from.
            names: The names to check. Duplicates are fine and are reported
                once.

        Returns:
            list: The unmapped names, in the order first seen, with no
                repeats. Empty when everything resolved.
        """
        df = self._mapping()
        mapped_names = set(df[df["source"] == source]["source_name"]) if not df.empty else set()
        return [name for name in dict.fromkeys(names) if name not in mapped_names]

    def resolve_many_with_fallback(self, source: str, names: pd.Series, positions: pd.Series = None) -> pd.Series:
        """Translate names to canonical IDs, trying an exact name match as backup.

        The two-step resolution every service uses. Most names are handled by the
        curated table, and the leftovers usually resolve by matching the name
        exactly against nflreadpy's own player list. Keeping both steps here
        means no service has to reimplement the order.

        Steps:
            1. Call `resolve_many` above to resolve everything the curated table
               covers.
            2. Work out which rows came back unresolved.
            3. If none did, return early with the result as is.
            4. Convert the column to hold arbitrary objects before writing
               string ids into it -- see the note below.
            5. Ask `PlayerDirectory.resolve_by_display_name` to handle the
               leftovers, passing their positions when available so two players
               sharing a name can be told apart.
            6. Write those answers back into the unresolved slots.

        Args:
            source: Which platform the names came from.
            names: A column of player names as that platform spells them.
            positions: Optional column of positions ("RB", "WR", ...) lined up
                with `names`. Supplying it makes the fallback safer, since a
                name match that disagrees on position is probably a different
                player.

        Returns:
            pd.Series: Canonical ids lined up with `names`, with NaN left for
                anything neither step could resolve.

        Note:
            Step 4 exists for a real pandas trap: when nothing resolves, the
            column comes back as all-NaN and typed as float64. Assigning string
            ids into a float column is deprecated in pandas 2 and an error in
            pandas 3, so the type is widened first.
        """
        mapped = self.resolve_many(source, names)
        still_missing = mapped.isna()
        if still_missing.any():
            # astype(object) because an all-unresolved Series comes back as
            # float64 (all-NaN), and assigning string ids into a float column is
            # deprecated in pandas 2 and an error in pandas 3.
            mapped = mapped.astype(object).copy()
            fallback_positions = positions[still_missing] if positions is not None else None
            mapped.loc[still_missing] = self._player_directory.resolve_by_display_name(
                names[still_missing], fallback_positions
            )
        return mapped

    def unresolved_with_fallback(self, source: str, names: pd.Series, positions: pd.Series = None) -> list:
        """List names that neither the curated table nor the name match could resolve.

        These are the genuine problem cases: either they need a hand-written
        mapping row, or nflreadpy has no record of the player at all, which
        happens with a very recent signing.

        Steps:
            1. Call `resolve_many_with_fallback` above to run both resolution
               steps.
            2. Keep only the input names whose result came back unresolved.
            3. Drop duplicates and return them as a plain list.

        Args:
            source: Which platform the names came from.
            names: A column of player names as that platform spells them.
            positions: Optional column of positions lined up with `names`, used
                by the fallback step.

        Returns:
            list: The still-unresolved names, with no repeats. Empty when
                everything resolved one way or the other.
        """
        resolved = self.resolve_many_with_fallback(source, names, positions)
        return names[resolved.isna()].drop_duplicates().tolist()
