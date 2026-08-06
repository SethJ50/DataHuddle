"""Stores the user's own tags and notes about players, per draft.

Everything else in the app is data pulled from outside sources; this is the one
place the user's own opinions live. Markings are scoped to a draft rather than
global, so a player can be a target in one league and a fade in another.
"""

from registry import Collections
from db.documents import find_one, find_all, upsert

class PlayerMarkingsService:
    """Reads and writes the categories and notes a user attached to a player.

    Every method is keyed by a draft and a player together, since the same player
    can be marked differently in two different leagues. Storage goes through
    `db/documents.py` rather than touching MongoDB directly.
    """

    def get(self, draft_id, canonical_id):
        """Load one player's tags and notes within a draft.

        Called whenever a player is displayed with his markings, such as on the
        player profile page or in a draft board row.

        Steps:
            1. Call `find_one` from db/documents.py, matching on both the draft
               and the player.
            2. If nothing is stored, return an empty marking rather than None, so
               callers never have to check before reading `categories` or
               `notes`.
            3. Otherwise return the stored record as it is.

        Args:
            draft_id: Which draft's markings to read.
            canonical_id: Which player, by his stable nflreadpy id.

        Returns:
            dict: Always has `categories`, a list of tag names, and `notes`, a
                free-text string. An unmarked player comes back with an empty
                list and an empty string rather than nothing at all.
        """

        doc = find_one(
            Collections.PLAYER_MARKINGS,
            {"draft_id": draft_id, "canonical_id": canonical_id},
        )
        if doc is None:
            return {"categories": [], "notes": ""}
        return doc

    def save(self, draft_id, canonical_id, categories, notes):
        """Save a player's tags and notes, creating or updating as needed.

        Backs the marking editor in the UI. The caller does not need to know
        whether this player has been marked before, since the same call handles
        both cases.

        Steps:
            1. Build the record, including the draft and player ids so a
               record read back on its own still knows what it describes.
            2. Call `upsert` from db/documents.py, matching on the draft and
               player pair. That match decides whether an existing record is
               updated or a new one created.

        Args:
            draft_id: Which draft these markings belong to.
            canonical_id: Which player, by his stable nflreadpy id.
            categories: The tag names to attach, as a list. Pass an empty list to
                clear them.
            notes: Free-text notes about the player. Pass an empty string to
                clear them.

        Returns:
            None: The write either succeeded or raised.
        """

        doc = {
            "draft_id": draft_id,
            "canonical_id": canonical_id,
            "categories": categories,
            "notes": notes,
        }
        upsert(
            Collections.PLAYER_MARKINGS,
            {"draft_id": draft_id, "canonical_id": canonical_id},
            doc,
        )

    def all_for_draft(self, draft_id):
        """Load every player's markings for one draft, in a single query.

        Used when a whole board is rendered, so the page makes one database call
        rather than one per player row.

        Steps:
            1. Call `find_all` from db/documents.py, filtering on the draft id
               only so every player in that draft comes back.

        Args:
            draft_id: Which draft's markings to read.

        Returns:
            list: One dictionary per marked player, each with `draft_id`,
                `canonical_id`, `categories`, and `notes`. Players who have never
                been marked simply do not appear, so callers should not expect an
                entry for everyone.
        """

        return find_all(Collections.PLAYER_MARKINGS, {"draft_id": draft_id})
