"""Stores drafts in progress, so one survives a browser refresh.

A session is one run-through of a draft: the real one you enter live, or a
practice simulation. It holds the ordered pick log that
services/draft_runner_service.py works in, plus enough context to rebuild the
draft exactly -- which league it belongs to, and the seed its simulated managers
were drawn from.

WHY THIS EXISTS AT ALL: without it, closing a tab in round 9 of a live draft
loses everything, with the draft still running and no way to catch up. Every
pick is written as it is made.

Reads and writes go through db/documents.py rather than pymongo directly, and
that module is INJECTED rather than imported at the top -- which is what lets the
tests run against a small in-memory fake instead of a real database.
"""

import uuid
from datetime import datetime, timezone

import numpy as np

from registry import Collections

# The only two kinds of session. A draft has at most one "live" -- the real
# thing -- and any number of "sim" sessions to practise against.
VALID_MODES = ("sim", "live")


class DraftSessionRepo:
    """Creates, loads and saves draft sessions.

    Deliberately thin. It knows how a session is SHAPED and nothing about how a
    draft works -- no pick numbers, no snake order, no keepers. All of that lives
    in DraftState, which this only stores and returns.

    Every method takes or returns plain dictionaries, so nothing here needs to
    import the draft model.
    """

    def __init__(self, collection_name=Collections.DRAFT_SESSIONS, documents=None):
        """Set up where sessions are stored and how to reach the database.

        Both arguments have working defaults, so `DraftSessionRepo()` is the
        normal way to build one. Tests override them.

        Steps:
            1. Save the collection name.
            2. If no database helpers were supplied, import the real
               db/documents.py module and use that.

        Args:
            collection_name: Which collection to read and write. Defaults to the
                real one named in registry.py; tests pass a throwaway name.
            documents: An object providing `find_one`, `find_all`, `upsert`,
                `delete` and `ensure_index`, with the same signatures as
                db/documents.py. Defaults to that module itself.

        Note:
            The import happens INSIDE this method rather than at the top of the
            file on purpose. A top-level import would connect to MongoDB the
            moment anything imported this module, so a test passing a fake would
            still need a live database to get that far.
        """
        if documents is None:
            from db import documents as default_documents
            documents = default_documents

        self._collection_name = collection_name
        self._documents = documents

    def ensure_indexes(self):
        """Create the indexes this collection needs.

        Call once at startup. Both are cheap and safe to repeat -- MongoDB does
        nothing if an identical index already exists.

        Steps:
            1. A unique index on `session_id`, so a duplicate id is rejected by
               the database rather than trusted to never happen.
            2. A plain index on draft and mode, which is how the session picker
               and `get_or_create_live` below look sessions up.

        Returns:
            None: Both calls either succeed or raise.
        """
        self._documents.ensure_index(self._collection_name, ("session_id",),
                                     unique=True, name="session_id")
        self._documents.ensure_index(self._collection_name, ("draft_id", "mode"))

    def create(self, draft_id, mode, name, seed=None):
        """Start a new session with no picks in it.

        Steps:
            1. Reject an unrecognised mode, naming the valid ones.
            2. Generate a random id. `uuid4().hex` gives a 32-character string
               with no meaning of its own, which is what an identifier should be.
            3. Invent a random seed if none was given -- see the note.
            4. Build the document with an empty pick log and matching timestamps.
            5. Write it, keyed on the new id, which always inserts because
               nothing can already match.

        Args:
            draft_id: Which league this session belongs to.
            mode: "sim" for practice, "live" for the real draft.
            name: What to show in the session picker.
            seed: Fixes the simulated managers' opinions. Leave it out for a
                random one.

        Returns:
            dict: The session just created, ready to hand to
                `state_from_session`.

        Raises:
            ValueError: If `mode` is not "sim" or "live".

        Note:
            The seed is what makes a simulated draft REPLAYABLE: the AI managers
            are drawn from it, so rewinding and playing forward reproduces their
            picks exactly. A fresh random seed per session is what makes two
            practice drafts genuinely different opponents rather than the same
            draft twice.
        """
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")

        now = datetime.now(timezone.utc).isoformat()
        session = {
            "session_id": uuid.uuid4().hex,
            "draft_id": draft_id,
            "mode": mode,
            "name": name,
            "seed": (int(seed) if seed is not None
                     else int(np.random.default_rng().integers(0, 2 ** 31 - 1))),
            "picks": [],
            "created_at": now,
            "updated_at": now,
        }
        self._documents.upsert(self._collection_name,
                               {"session_id": session["session_id"]}, session)
        return session

    def get(self, session_id):
        """Load one session by its id.

        Steps:
            1. Look it up by `session_id`.

        Args:
            session_id: Which session to load.

        Returns:
            dict | None: The stored session, or None when no session has that
                id. None is normal -- a stale id in session state gets you here
                -- so callers must handle it rather than assume.
        """
        return self._documents.find_one(self._collection_name,
                                        {"session_id": session_id})

    def list_for_draft(self, draft_id):
        """List every session belonging to one league, live first.

        Feeds the session picker.

        Steps:
            1. Find every session for this draft.
            2. Sort with the live session first, then the sims oldest first.
               `s["mode"] != "live"` is False for the live one, and False sorts
               before True, which puts it at the top.

        Args:
            draft_id: Which league's sessions to list.

        Returns:
            list: One dictionary per session, the live one first. Empty when this
                league has never been drafted.
        """
        sessions = self._documents.find_all(self._collection_name,
                                            {"draft_id": draft_id})
        return sorted(sessions,
                      key=lambda s: (s["mode"] != "live", s.get("created_at", "")))

    def get_or_create_live(self, draft_id):
        """Get this league's live session, starting it if it does not exist yet.

        There is exactly one live session per league -- the real draft. Rolling
        "find it" and "start it" into one call means no caller can accidentally
        create a second one and split the real draft across two records.

        Steps:
            1. Look for an existing session for this draft in live mode.
            2. Return it if there is one.
            3. Otherwise create one with `create` above.

        Args:
            draft_id: Which league's live draft to open.

        Returns:
            dict: The live session, whether it already existed or was just
                created.
        """
        existing = self._documents.find_one(self._collection_name,
                                            {"draft_id": draft_id, "mode": "live"})
        if existing is not None:
            return existing
        return self.create(draft_id, mode="live", name="Live draft")

    def save_picks(self, session_id, picks):
        """Write the whole pick log, replacing whatever was stored.

        Handles BOTH kinds of change with one call. Appending a pick and
        rewinding twenty are the same operation from here: the caller hands over
        the log as it now stands. A separate append would need a separate
        truncate, and a rewind that only ever appended would be a hard bug to
        see.

        Steps:
            1. Overwrite the `picks` field with a copy of the list, so later
               changes to the caller's list do not silently alter what is stored.
            2. Stamp `updated_at`.

        Args:
            session_id: Which session to write to.
            picks: The pick log as it now stands, from `DraftState.picks`.

        Returns:
            None: The write either succeeded or raised.

        Note:
            Rewriting the whole array on every pick is fine here: 180 small
            entries is a tiny document, and one write per pick is nothing. It
            does mean two browser tabs on the same session would overwrite each
            other, which is acceptable for a single-user app.
        """
        self._documents.upsert(
            self._collection_name,
            {"session_id": session_id},
            {"picks": list(picks),
             "updated_at": datetime.now(timezone.utc).isoformat()},
        )

    def rename(self, session_id, name):
        """Change what a session is called, leaving its picks alone.

        Practice sessions are told apart by name, and the useful name is rarely
        the one you thought of before drafting a single player -- "Practice 3"
        becomes "zero RB, pick 10" only once you have tried it.

        Steps:
            1. Reject a blank name, since a session with no label cannot be
               picked out of a list.
            2. Set the name and stamp `updated_at`, touching nothing else.

        Args:
            session_id: Which session to rename.
            name: The new name.

        Returns:
            None: The write either succeeded or raised.

        Raises:
            ValueError: If the name is empty or only whitespace.
        """
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("a session needs a name")
        self._documents.upsert(
            self._collection_name,
            {"session_id": session_id},
            {"name": cleaned,
             "updated_at": datetime.now(timezone.utc).isoformat()},
        )

    def delete(self, session_id):
        """Permanently remove a practice session.

        Steps:
            1. Load the session. Deleting one that does not exist is treated as
               success, since the end state is what was wanted.
            2. Refuse if it is the live session -- see Raises.
            3. Delete it.

        Args:
            session_id: Which session to remove.

        Returns:
            None: Deleting something already gone is not an error.

        Raises:
            ValueError: If the session is the live one. That record is the real
                draft, it cannot be regenerated, and there is no undo. A UI
                confirmation is easy to click through by accident; this is not.
        """
        session = self.get(session_id)
        if session is None:
            return
        if session["mode"] == "live":
            raise ValueError(
                "refusing to delete the live session for this draft; it is the "
                "record of a real draft and cannot be rebuilt"
            )
        self._documents.delete(self._collection_name, {"session_id": session_id})
