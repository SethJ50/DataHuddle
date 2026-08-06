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
        """Load the saved note for one team in one draft.

        Called when the team profile page renders, to fill in whatever the user
        last wrote about that team.

        Steps:
            1. Call `find_one` from db/documents.py, matching on both the draft
               and the team abbreviation.
            2. If a record came back, return its `notes` text. Otherwise return
               an empty string, since "no note yet" is normal.

        Args:
            draft_id: The draft this note belongs to.
            team_abbr: The team's abbreviation, such as "KC". Together with the
                draft id this identifies the note.

        Returns:
            str: The saved note text, or an empty string if nothing has been
                saved yet. Never None, so the caller can pass it straight to a
                text box.

        Note:
            Returns just the text rather than the whole record, because the UI
            only ever needs a string to seed a text area.
        """
        doc = find_one(
            Collections.TEAM_NOTES,
            {"draft_id": draft_id, "team_abbr": team_abbr},
        )
        return doc["notes"] if doc else ""

    def save(self, draft_id, team_abbr, notes):
        """Save the note for one team in one draft, creating it if needed.

        Backs the save button on the team profile page. The caller does not need
        to know whether a note already exists.

        Steps:
            1. Call `upsert` from db/documents.py, matching on the draft and team
               pair and storing that same pair alongside the note text.
            2. That match decides the outcome: an existing note is overwritten,
               and a missing one is created.

        Args:
            draft_id: The draft this note belongs to.
            team_abbr: The team's abbreviation, such as "KC".
            notes: The free-text note to save. Pass an empty string to clear it.

        Returns:
            None: The write either succeeded or raised.
        """
        upsert(
            Collections.TEAM_NOTES,
            {"draft_id": draft_id, "team_abbr": team_abbr},
            {"draft_id": draft_id, "team_abbr": team_abbr, "notes": notes},
        )
