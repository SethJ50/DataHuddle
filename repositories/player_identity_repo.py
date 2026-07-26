"""Resolves source-specific player names to a stable canonical player ID.

canonical_id is nflreadpy's stable internal ID (player_id/gsis_id) — not the
display name, since display names can collide across players or shift
formatting between nflreadpy releases. Mapping rows live in the player_id_map
collection and are curated by hand (see PLANNING.md: explicitly not
fuzzy-matched), loaded like any other CSV via scripts/load_data.py.
"""

from typing import Iterable, Optional

import pandas as pd

from repositories.collection_repo import CollectionRepo


class PlayerIdentityRepo:
    def __init__(self, collection_repo: CollectionRepo, player_directory):
        self._collection_repo = collection_repo
        self._player_directory = player_directory

    def _mapping(self) -> pd.DataFrame:
        return self._collection_repo.read()

    def resolve(self, source: str, source_name: str) -> Optional[str]:
        df = self._mapping()
        if df.empty:
            return None

        match = df[(df["source"] == source) & (df["source_name"] == source_name)]
        if match.empty:
            return None
        return str(match.iloc[0]["canonical_id"])

    def resolve_many(self, source: str, names: pd.Series) -> pd.Series:
        """Vectorized resolve — returns a Series aligned to `names`'s index,
        with unresolved names mapped to None."""
        df = self._mapping()
        if df.empty:
            return pd.Series([None] * len(names), index=names.index)

        source_map = df[df["source"] == source].set_index("source_name")["canonical_id"]
        return names.map(source_map)

    def unresolved(self, source: str, names: Iterable[str]) -> list:
        """Names with no player_id_map row for this source — surfaced so they
        can be added to the manually curated mapping table, rather than
        silently dropped from whatever's being joined."""
        df = self._mapping()
        mapped_names = set(df[df["source"] == source]["source_name"]) if not df.empty else set()
        return [name for name in dict.fromkeys(names) if name not in mapped_names]

    def resolve_many_with_fallback(self, source: str, names: pd.Series, positions: pd.Series = None) -> pd.Series:
        """resolve_many() (the manually-curated player_id_map), falling back
        to PlayerDirectory.resolve_by_display_name() (an exact, not fuzzy,
        name/position match) for anything still unresolved. Shared by every
        service that needs to match a source's player names to canonical_id,
        so this two-step resolution logic lives in exactly one place."""
        mapped = self.resolve_many(source, names)
        still_missing = mapped.isna()
        if still_missing.any():
            mapped = mapped.copy()
            fallback_positions = positions[still_missing] if positions is not None else None
            mapped.loc[still_missing] = self._player_directory.resolve_by_display_name(
                names[still_missing], fallback_positions
            )
        return mapped

    def unresolved_with_fallback(self, source: str, names: pd.Series, positions: pd.Series = None) -> list:
        """Names failing BOTH player_id_map and the exact-name fallback —
        candidates for a manual player_id_map row (or nflreadpy genuinely
        has no record of them yet, e.g. a very recent signing)."""
        resolved = self.resolve_many_with_fallback(source, names, positions)
        return names[resolved.isna()].drop_duplicates().tolist()