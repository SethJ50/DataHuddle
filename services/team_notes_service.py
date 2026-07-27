"""Loads and persists a free-text note for one team within one draft.

Team notes are the team-level counterpart to PlayerMarkingsService's per-player
notes: same "one document per entity per draft" shape, but keyed by team
abbreviation (e.g. "KC") instead of canonical_id. Stored in the `team_notes`
collection, read/written one document at a time via db/documents.py.
"""

from registry import Collections
from db.documents import find_one, upsert


class TeamNotesService:
    """CRUD for a single team's note inside a single draft.

    A note is uniquely identified by the (draft_id, team_abbr) pair, so the
    same team can carry different notes across different drafts. No caching
    here -- reads/writes go straight to Mongo, which is fine for the low volume
    of note edits.
    """

    def get(self, draft_id, team_abbr):
        """
        Purpose: Load the saved note for one team in one draft.

        Parameters:
            draft_id: the draft this note belongs to.
            team_abbr: the team's abbreviation (the per-team key).

        Returns:
            str -- the saved note text, or "" if nothing has been saved yet.

        Notes:
            Returns just the text (not the whole document) because the UI only
            ever needs a string to seed a text_area.
        """
        doc = find_one(
            Collections.TEAM_NOTES,
            {"draft_id": draft_id, "team_abbr": team_abbr},
        )
        return doc["notes"] if doc else ""

    def save(self, draft_id, team_abbr, notes):
        """
        Purpose: Create or update the note for one team in one draft.

        Parameters:
            draft_id: the draft this note belongs to.
            team_abbr: the team's abbreviation (the per-team key).
            notes: the free-text note to persist.

        Returns:
            None.

        Notes:
            upsert keys on (draft_id, team_abbr), so re-saving overwrites the
            same document rather than creating duplicates.
        """
        upsert(
            Collections.TEAM_NOTES,
            {"draft_id": draft_id, "team_abbr": team_abbr},
            {"draft_id": draft_id, "team_abbr": team_abbr, "notes": notes},
        )
