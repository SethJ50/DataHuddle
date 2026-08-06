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

import re
import unicodedata

import pandas as pd

# Shown instead of a photo when nflreadpy has no headshot for a player, so the
# UI never has to deal with a missing image.
DEFAULT_HEADSHOT = "www/defaultPlayer.png"

# Generational suffixes stripped when matching names across sources. One source
# writes "James Cook III" and another writes "James Cook"; they are the same guy.
# Plain "V" is deliberately NOT here -- a bare single letter is far more likely to
# be part of a real name than a generational suffix, and a wrong strip is worse
# than a missed one.
NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})


def normalize_name(name) -> str:
    """Reduce a player name to a form that matches across data sources.

    Every source spells names slightly differently, and the differences are
    systematic rather than random: generational suffixes, accented characters,
    and apostrophes. Ten UDK-ranked players — including an RB5 — were being
    silently dropped from the app's universe over exactly these three things.

    Steps:
        1. Split accented characters into a base letter plus a separate accent
           mark, which is what unicodedata's "NFD" form does.
        2. Throw away those accent marks, leaving the plain letters behind.
        3. Lowercase everything and replace any character that is not a letter,
           digit, or space with a space, which removes apostrophes and periods.
        4. Split into words and drop any that appear in NAME_SUFFIXES.
        5. Join the surviving words back together with single spaces.

    Args:
        name: A display name from anywhere — "Eddy Piñeiro", "James Cook III",
            "Tre' Harris". Converted to text first, so a non-string will not
            raise.

    Returns:
        str: Lowercased, accent-free, punctuation-free, suffix-free.
            "James Cook III" -> "james cook"
            "Eddy Piñeiro"   -> "eddy pineiro"
            "Tre' Harris"    -> "tre harris"

    Note:
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
    """Answers "who is this player?" questions against nflreadpy's data.

    Two kinds of question live here. The `get_*` methods look a player up by his
    canonical id and return one fact about him — name, photo, position, team.
    The `resolve_by_display_name` method goes the other way, turning names an
    outside source used back into canonical ids, which is how data from ESPN or
    UDK gets attached to the right player.
    """

    def __init__(self, nfl_read_repo):
        """Store the nflreadpy data source every lookup will read from.

        Steps:
            1. Save the repository on the instance. Nothing is downloaded here;
               the repository loads its tables lazily on first use.

        Args:
            nfl_read_repo: An `NflReadRepo` providing `.players()` for the broad
                player reference and `.player_stats()` for game-by-game rows.
        """
        self._nfl_read_repo = nfl_read_repo

    def _stats(self):
        """Get the game-by-game stats table.

        A one-line helper so the game log method reads clearly and there is one
        place to change if the data source moves.

        Steps:
            1. Call `.player_stats()` on the stored repository, which downloads
               the table on first use and caches it afterwards.

        Returns:
            pd.DataFrame: One row per player per game, keyed by `player_id`,
                with the passing, rushing, and receiving stat columns.
        """
        return self._nfl_read_repo.player_stats()

    def _players(self):
        """Get the broad player reference table.

        Used by every identity lookup in this class. This table is preferred
        over the stats table because it includes rookies who have not played a
        game yet, who would otherwise silently vanish from the app.

        Steps:
            1. Call `.players()` on the stored repository, which downloads the
               table on first use and caches it afterwards.

        Returns:
            pd.DataFrame: Roughly 25,000 rows, one per player, keyed by
                `gsis_id`, with columns including `display_name`, `position`,
                `latest_team`, and `headshot`.
        """
        return self._nfl_read_repo.players()

    def search_names(self, query: str = "", positions: list = None) -> dict:
        """Find players by name fragment or position, for a dropdown menu.

        Backs the player pickers in the UI. The return shape is chosen to be
        handed straight to a select widget's `choices=` argument: the widget
        shows the names and gives back the id.

        Steps:
            1. Call `_players` above for the full player reference.
            2. If positions were given, keep only players at those positions.
            3. If a query was given, keep only players whose display name
               contains it, ignoring capitalization. `na=False` treats a missing
               name as "does not match" rather than raising.
            4. Narrow to just id and name, drop rows missing either, keep one
               row per player, and sort alphabetically by name.
            5. Zip the two columns into a dictionary.

        Args:
            query: A fragment of a display name to match, for example "jeff".
                Leave empty to match every name.
            positions: Optional list of positions to keep, for example
                `["RB", "WR"]`. Leave out to include every position.

        Returns:
            dict: Maps each matching player's canonical id to his display name,
                ordered alphabetically by name. Empty when nothing matches.
        """
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
        """Look up a player's full name from his canonical ID.

        Used wherever the app has an id and needs something readable to show,
        such as a page heading or a table label.

        Steps:
            1. Call `_players` above and keep only rows for this id.
            2. If any row was found and the table has a `display_name` column,
               drop rows where the name is missing and return the first one left.
            3. If any of those checks fail, return None.

        Args:
            canonical_id: The player's stable nflreadpy id, matching `gsis_id`.

        Returns:
            str | None: The player's display name, or None when the id is
                unknown or has no name recorded. Callers must handle None, since
                an unrecognized id is not treated as an error.
        """
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "display_name" in rows.columns:
            names = rows["display_name"].dropna()
            if not names.empty:
                return str(names.iloc[0])

        return None

    def get_headshot(self, canonical_id):
        """Look up the URL of a player's headshot photo.

        Unlike the other getters, this never returns None: the UI always needs
        something to put in an image tag, so a placeholder stands in when no
        photo exists.

        Steps:
            1. Call `_players` above and keep only rows for this id.
            2. If any row was found and the table has a `headshot` column, drop
               rows where the URL is missing and return the first one left.
            3. Otherwise return DEFAULT_HEADSHOT, the local placeholder image.

        Args:
            canonical_id: The player's stable nflreadpy id, matching `gsis_id`.

        Returns:
            str: A URL to the player's photo, or the DEFAULT_HEADSHOT path when
                none is available. Never None.
        """
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "headshot" in rows.columns:
            urls = rows["headshot"].dropna()
            if not urls.empty:
                return str(urls.iloc[0])

        return DEFAULT_HEADSHOT

    def get_position(self, canonical_id):
        """Look up a player's position from his canonical ID.

        Position drives a lot of the app's behavior — which replacement level
        applies, which roster slot he can fill — so it is looked up here rather
        than trusted from whichever source supplied the player.

        Steps:
            1. Call `_players` above and keep only rows for this id.
            2. If any row was found and the table has a `position` column, drop
               rows where it is missing and return the first one left.
            3. If any of those checks fail, return None.

        Args:
            canonical_id: The player's stable nflreadpy id, matching `gsis_id`.

        Returns:
            str | None: A position such as "RB" or "WR", or None when the id is
                unknown or has no position recorded.
        """
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "position" in rows.columns:
            positions = rows["position"].dropna()
            if not positions.empty:
                return str(positions.iloc[0])

        return None

    def get_team(self, canonical_id):
        """Look up the NFL team a player most recently belonged to.

        Reads the `latest_team` column rather than a per-season one, so it
        reflects the current roster even for a player who was traded.

        Steps:
            1. Call `_players` above and keep only rows for this id.
            2. If any row was found and the table has a `latest_team` column,
               drop rows where it is missing and return the first one left.
            3. If any of those checks fail, return None.

        Args:
            canonical_id: The player's stable nflreadpy id, matching `gsis_id`.

        Returns:
            str | None: A team abbreviation such as "DET", or None when the id
                is unknown or the player is a free agent with no team recorded.
        """
        rows = self._players()
        rows = rows[rows["gsis_id"] == canonical_id]

        if not rows.empty and "latest_team" in rows.columns:
            teams = rows["latest_team"].dropna()
            if not teams.empty:
                return str(teams.iloc[0])

        return None

    def get_gamelog(self, canonical_id):
        """Get every recorded game week for one player.

        The only method here that reads the stats table rather than the player
        reference, because game-by-game rows exist nowhere else. A player who
        has never played returns nothing, which is expected for rookies.

        Steps:
            1. Call `_stats` above for the game-by-game table.
            2. Keep only rows whose `player_id` matches this player.
            3. Copy the result so callers can add columns without altering the
               cached table everything else shares.

        Args:
            canonical_id: The player's stable nflreadpy id. In the stats table
                this column is called `player_id`, but it holds the same values
                as `gsis_id`.

        Returns:
            pd.DataFrame: One row per game the player appeared in, with the
                stats table's full set of columns including `season`, `week`,
                and the passing, rushing, and receiving stats. Empty when the
                player has no recorded games.
        """
        rows = self._stats()
        return rows[rows["player_id"] == canonical_id].copy()

    def _name_reference(self):
        """Build a lookup table of players with both matching keys precomputed.

        Shared by `resolve_by_display_name` and `name_collisions` below so both
        judge names by exactly the same rules. The two keys exist because
        resolution happens in two passes, strict then lenient.

        Steps:
            1. Call `_players` above and narrow it to id, name, and position.
            2. Drop players with no display name and keep one row per player.
            3. Copy, so the added columns do not modify the cached table.
            4. Add `_exact`: the display name trimmed and lowercased.
            5. Add `_norm`: the display name put through `normalize_name` above,
               which also strips accents, punctuation, and suffixes.

        Returns:
            pd.DataFrame: One row per player with columns `gsis_id`,
                `display_name`, `position`, `_exact`, and `_norm`. The two
                underscore-prefixed columns are internal match keys, not
                anything to display.
        """
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
        """Turn player names from an outside source into stable canonical IDs.

        The fallback used when the hand-curated mapping table has no row for a
        name. It tries an exact match first and a normalized match second, and
        it returns nothing rather than guessing when a name is ambiguous.

        Steps:
            1. Call `_name_reference` above for the lookup table with both match
               keys already computed.
            2. Build the same two keys from the incoming names.
            3. If positions were supplied, glue the uppercased position onto
               every key on both sides, so a name only matches a player at the
               same position.
            4. Pass 1: build a lookup from `_exact` to id and map the incoming
               exact keys through it. `astype(object)` keeps the column able to
               hold string ids even when nothing matched.
            5. Pass 2: for the rows still unmatched, build a lookup from `_norm`
               to id, but first count how many distinct players share each
               normalized key and keep only the keys belonging to exactly one.
            6. Fill the unmatched rows from that lookup, leaving anything still
               unresolved as missing.

        Args:
            names: Names as the outside source spells them, one per row.
            positions: Optional positions lined up with `names`. When supplied
                they become part of the match key, so two different players
                sharing a name can still be told apart.

        Returns:
            pd.Series: Canonical ids positioned to line up with `names` row for
                row, with missing values where no confident match exists.

        Note:
            The pass order matters. Pass 2 only ever looks at names that already
            failed pass 1, so it cannot change a match that was previously
            correct — it can only add new ones.

            AMBIGUOUS NORMALIZED KEYS ARE DROPPED, NOT GUESSED. If two different
            players reduce to the same normalized key, that key is excluded from
            pass 2 entirely and both stay unresolved. Leaving a player unmatched
            is visible and recoverable; silently attaching the wrong gsis_id is
            an error that would never surface. Use `name_collisions` below to see
            them.
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
        """List players whose normalized names are indistinguishable from someone else's.

        These are exactly the cases pass 2 of `resolve_by_display_name` above
        refuses to guess between. This is a diagnostic tool, not used at runtime.

        Steps:
            1. Call `_name_reference` above for the lookup table.
            2. Take the normalized name as the collision key.
            3. If positions are being considered, append the uppercased position
               to that key, treating a missing position as an empty string.
            4. Count how many rows share each key, and keep only the rows whose
               key appears more than once.
            5. Return those rows sorted so colliding players sit next to each
               other.

        Args:
            with_position: True, the default, mirrors how resolution actually
                works — the key is name plus position, so two players sharing a
                name at DIFFERENT positions are not a collision. Set False to see
                name-only clashes, which is a much larger and mostly harmless
                set.

        Returns:
            pd.DataFrame: Columns `gsis_id`, `display_name`, `position`, and
                `_key` (the collision key), sorted so colliding rows sit
                together. Empty when there are none.

        Note:
            If a player you expect is missing and is not in the actionable
            unresolved list, look here — a collision is the other way a name can
            fail to resolve, and it fails silently by design.

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
