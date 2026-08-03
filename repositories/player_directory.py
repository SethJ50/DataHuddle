import re
import unicodedata

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

# Generational suffixes stripped when matching names across sources. One source
# writes "James Cook III" and another writes "James Cook"; they are the same guy.
# Plain "V" is deliberately NOT here -- a bare single letter is far more likely to
# be part of a real name than a generational suffix, and a wrong strip is worse
# than a missed one.
NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


def normalize_name(name) -> str:
    """
    Purpose:
        Reduce a player name to a form that matches across data sources.

    Parameters:
        name (str): A display name from anywhere — "Eddy Piñeiro",
            "James Cook III", "Tre' Harris".

    Returns:
        str: Lowercased, accent-free, punctuation-free, suffix-free.
            "James Cook III" -> "james cook"
            "Eddy Piñeiro"   -> "eddy pineiro"
            "Tre' Harris"    -> "tre harris"

    Notes:
        Every source spells names slightly differently, and the differences are
        systematic rather than random: generational suffixes, accented characters,
        and apostrophes. Ten UDK-ranked players — including an RB5 — were being
        silently dropped from the app's universe over exactly these three things.

        This is intentionally NOT fuzzy matching. It removes known-meaningless
        variation and nothing else; "Mike Williams" and "Michael Williams" still
        do not match, which is correct — they may well be different people.
    """
    text = unicodedata.normalize("NFD", str(name))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")  # drop accents
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())                       # punctuation -> space
    parts = [p for p in text.split() if p not in NAME_SUFFIXES]
    return " ".join(parts)


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

    def _name_reference(self):
        """One row per player, with both an exact and a normalized match key."""
        df = (
            self._players()[["gsis_id", "display_name", "position"]]
                .dropna(subset=["display_name"])
                .drop_duplicates(subset="gsis_id")
                .copy()
        )
        df["_exact"] = df["display_name"].str.strip().str.lower()
        df["_norm"] = df["display_name"].map(normalize_name)
        return df

    def resolve_by_display_name(self, names: pd.Series, positions: pd.Series = None):
        """
        Purpose:
            Given name(s) from an outside source, find each player's stable
            canonical_id. Returns None where no confident match exists.

        Parameters:
            names (pd.Series): Names as the source spells them.
            positions (pd.Series | None): Matching positions. When supplied they
                become part of the match key, so two different players sharing a
                name can still be told apart.

        Returns:
            pd.Series aligned to `names`'s index; None where unmatched.

        Notes:
            TWO PASSES, and the order matters:

              1. Exact match on the lowercased name (the original behaviour).
              2. Normalized match (see normalize_name) for whatever pass 1 missed.

            Pass 2 only ever looks at names that already failed, so this cannot
            change a match that was previously correct — it can only add new ones.

            AMBIGUOUS NORMALIZED KEYS ARE DROPPED, NOT GUESSED. If two different
            players reduce to the same normalized key, that key is excluded from
            pass 2 entirely and both stay unresolved. Leaving a player unmatched is
            visible and recoverable; silently attaching the wrong gsis_id is an
            error that would never surface. Use name_collisions() to see them.
        """
        df = self._name_reference()

        exact_keys = names.str.strip().str.lower()
        norm_keys = names.map(normalize_name)

        if positions is not None:
            suffix = "|" + positions.str.strip().str.upper()
            df_suffix = "|" + df["position"].str.strip().str.upper()
            df["_exact"] = df["_exact"] + df_suffix
            df["_norm"] = df["_norm"] + df_suffix
            exact_keys = exact_keys + suffix
            norm_keys = norm_keys + suffix

        # Pass 1 -- exact, unchanged from before.
        exact_lookup = df.drop_duplicates(subset="_exact").set_index("_exact")["gsis_id"]
        resolved = exact_keys.map(exact_lookup).astype(object)

        # Pass 2 -- normalized, only for what's still missing, and only for keys
        # that identify exactly one player.
        still_missing = resolved.isna()
        if still_missing.any():
            unambiguous = df.drop_duplicates(subset=["_norm", "gsis_id"])
            counts = unambiguous["_norm"].value_counts()
            unambiguous = unambiguous[unambiguous["_norm"].isin(counts[counts == 1].index)]
            norm_lookup = unambiguous.set_index("_norm")["gsis_id"]
            resolved.loc[still_missing] = norm_keys[still_missing].map(norm_lookup)

        return resolved

    def name_collisions(self, with_position: bool = True) -> pd.DataFrame:
        """
        Purpose:
            Players whose normalized names are indistinguishable from someone
            else's — the cases pass 2 of resolve_by_display_name deliberately
            refuses to match rather than guess between.

        Parameters:
            with_position (bool): True (default) mirrors how resolution actually
                works — the key is name + position, so two players sharing a name
                at DIFFERENT positions are not a collision. Set False to see
                name-only clashes, which is a much larger and mostly harmless set.

        Returns:
            pd.DataFrame with columns gsis_id, display_name, position, _key,
            sorted so colliding rows sit together. Empty when there are none.

        Notes:
            Diagnostic, not used at runtime. If a player you expect is missing and
            isn't in the actionable-unresolved list, look here — a collision is the
            other way a name can fail to resolve, and it fails silently by design.

            The fix for a genuine collision is a manual player_id_map row, which
            bypasses name matching entirely.
        """
        df = self._name_reference()
        df["_key"] = df["_norm"]
        if with_position:
            df["_key"] = df["_key"] + "|" + df["position"].fillna("").str.strip().str.upper()

        counts = df["_key"].value_counts()
        colliding = df[df["_key"].isin(counts[counts > 1].index)]
        return (
            colliding[["gsis_id", "display_name", "position", "_key"]]
            .sort_values(["_key", "display_name"])
            .reset_index(drop=True)
        )