"""Saves and loads the leagues the user drafts in.

A "draft" here is a saved set of league settings — size, scoring, your slot,
your lineup — not a record of picks made. Everything else in the app is scoped
to one of these: the simulator reads it to know what league to model, and notes
and markings hang off its id.

Storage goes through db/documents.py rather than touching MongoDB directly,
since these records are edited one at a time.
"""

import uuid
from datetime import datetime, timezone

from registry import Collections
from db.documents import find_one, find_all, upsert, delete
from draft_model.config import DEFAULT_STARTING_SLOTS, normalize_keepers


def keeper_docs(keepers) -> list:
    """Turn keeper records into the plain dictionaries MongoDB can store.

    MongoDB holds documents, not Python objects, so the Keeper records the model
    works in have to be flattened before they are saved. Going through
    `normalize_keepers` first means this accepts whatever shape the caller has —
    Keeper objects, dictionaries, or the legacy bare id strings — and always
    writes out the current shape.

    Steps:
        1. Call `normalize_keepers` from draft_model/config.py, which sorts the
           input into keepers that have a team and round, and ids that do not.
        2. Flatten each assigned keeper into a dictionary of its three fields.
        3. Append any unassigned ids as dictionaries with a null team and round,
           so a legacy keeper survives the round trip and still shows up in the
           UI asking to be finished, rather than silently disappearing.

    Args:
        keepers: The keepers to store, in any accepted shape. None or empty gives
            an empty list.

    Returns:
        list: One dictionary per keeper, each with `team`, `round`, and
            `canonical_id`. An unassigned keeper has None for the first two.
    """
    assigned, unassigned = normalize_keepers(keepers)
    docs = [
        {"team": k.team, "round": k.round, "canonical_id": k.canonical_id}
        for k in assigned
    ]
    docs += [
        {"team": None, "round": None, "canonical_id": cid} for cid in unassigned
    ]
    return docs


class DraftService:
    """Creates, reads, updates, and deletes the user's saved league settings.

    A thin layer over the drafts collection. Its main job beyond storage is
    filling in sensible defaults, so a draft saved before a field existed still
    loads with a usable value for it.
    """

    def list_drafts(self):
        """Load every saved draft, for the draft-picker dropdown.

        Called at the top of most pages, since almost everything the app shows
        depends on which league you are looking at.

        Steps:
            1. Call `find_all` from db/documents.py with no filter, which returns
               the whole drafts collection.

        Returns:
            list: One dictionary per saved draft, each with `draft_id`, `name`,
                `num_teams`, `draft_position`, `num_rounds`, `platform`,
                `scoring_format`, `starting_slots`, `keepers`, `roster_size`, and
                `created_at`. Empty when no draft has been created yet.
        """
        return find_all(Collections.DRAFTS)

    def create_draft(self, name, num_teams, draft_position, num_rounds, platform,
                    scoring_format, starting_slots=None, keepers=None, roster_size=None,
                    has_keepers=False):
        """Save a brand new league's settings and return its ID.

        Called from the draft manager page when the user sets up a league. The
        id it returns is what every other feature uses to scope itself to this
        league.

        Steps:
            1. Generate a random unique id. `uuid4().hex` gives a 32-character
               string with no meaning of its own, which is exactly what an
               identifier should be.
            2. Assemble the record, converting the optional arguments to their
               defaults and stamping the creation time in UTC.
            3. Call `upsert` from db/documents.py keyed on the new id. Since the
               id is brand new nothing can match, so this always inserts.

        Args:
            name: The display name for this league.
            num_teams: How many teams are in it.
            draft_position: Your slot, 1-indexed.
            num_rounds: How many rounds are drafted.
            platform: Where the league drafts: "espn", "yahoo", or "sleeper".
            scoring_format: A ScoringFormat's text value, such as "half_ppr".
            starting_slots: Maps a position to how many each team starts, for
                example `{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1,
                "K": 1, "DST": 1}`. Defaults to a standard 12-team lineup.
            keepers: The players kept before the draft, each naming the keeping
                team, the round that team spends, and the player. Passed through
                `keeper_docs` above, so any accepted shape works.
            roster_size: Total roster slots including bench, which is distinct
                from `num_rounds` — a league may draft 15 rounds onto an 18-slot
                roster.
            has_keepers: Whether this is a keeper league at all. When False the
                keepers list is stored empty, so turning keepers off cannot leave
                one quietly in effect.

        Returns:
            str: The new draft's id, which the caller should keep in order to
                refer to this league later.

        Note:
            starting_slots is what makes VORP correct at any league size -- the
            replacement level is derived from it rather than hardcoded, so a
            10-team or superflex league gets the right baseline automatically.

            Keepers store a team and a round rather than an overall pick number,
            because pick numbers move when the league size changes while "team 5,
            round 3" does not. DraftConfig.keeper_picks derives the pick.

            The player is stored as a canonical_id so the UI can show his name.
            A kept team defense can't be represented this way (defenses have no
            canonical_id), which is an accepted limitation rather than an
            oversight.
        """
        draft_id = uuid.uuid4().hex

        doc = {
            "draft_id": draft_id,
            "name": name,
            "num_teams": num_teams,
            "draft_position": draft_position,
            "num_rounds": num_rounds,
            "platform": platform,
            "scoring_format": scoring_format,
            "starting_slots": dict(starting_slots or DEFAULT_STARTING_SLOTS),
            "has_keepers": bool(has_keepers),
            "keepers": keeper_docs(keepers) if has_keepers else [],
            "roster_size": roster_size,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        upsert(Collections.DRAFTS, {"draft_id": draft_id}, doc)
        return draft_id

    def update_draft(self, draft_id, name, num_teams, draft_position, num_rounds,
                     platform, scoring_format, starting_slots=None, keepers=None,
                     roster_size=None, has_keepers=False):
        """Change an existing league's settings, leaving its ID intact.

        The editing counterpart to `create_draft` above. Keeping the id stable
        matters because notes, markings, and saved plans all point at it.

        Steps:
            1. Call `upsert` from db/documents.py, matching on the existing draft
               id and setting only the fields listed here.
            2. Fields not listed, namely `draft_id` and `created_at`, survive
               untouched, because the underlying update sets fields rather than
               replacing the whole record.

        Args:
            draft_id: Which saved draft to change.
            name: The display name for this league.
            num_teams: How many teams are in it.
            draft_position: Your slot, 1-indexed.
            num_rounds: How many rounds are drafted.
            platform: Where the league drafts: "espn", "yahoo", or "sleeper".
            scoring_format: A ScoringFormat's text value, such as "half_ppr".
            starting_slots: Maps a position to how many each team starts.
                Defaults to a standard 12-team lineup.
            keepers: The players kept before the draft, each naming the keeping
                team, the round it spends, and the player.
            roster_size: Total roster slots including bench.
            has_keepers: Whether this is a keeper league. When False the stored
                keeper list is cleared.

        Returns:
            None: The write either succeeded or raised.

        Note:
            Upsert with $set touches only the listed fields, so draft_id and
            created_at survive.

            Changing any of these changes the draft model's fingerprint, which
            means a cached simulation for this draft becomes a cache MISS rather
            than a stale hit. That is the intended behaviour -- results computed
            for a 12-team league should never be served to a 10-team one.
        """
        upsert(Collections.DRAFTS, {"draft_id": draft_id}, {
            "name": name,
            "num_teams": num_teams,
            "draft_position": draft_position,
            "num_rounds": num_rounds,
            "platform": platform,
            "scoring_format": scoring_format,
            "starting_slots": dict(starting_slots or DEFAULT_STARTING_SLOTS),
            "has_keepers": bool(has_keepers),
            "keepers": keeper_docs(keepers) if has_keepers else [],
            "roster_size": roster_size,
        })


    def get_draft(self, draft_id):
        """Load one saved league's settings by its ID.

        The usual way a page gets the settings it needs, and the input to
        `DraftConfig.from_draft_doc` when the model is set up.

        Steps:
            1. Call `find_one` from db/documents.py, matching on the draft id.

        Args:
            draft_id: Which saved draft to load.

        Returns:
            dict | None: The stored settings, with the same fields
                `list_drafts` describes, or None when no draft has that id.
                Callers should handle None, since a stale id in the URL or in
                session state is a normal way to get here.
        """
        return find_one(Collections.DRAFTS, {"draft_id": draft_id})

    def delete_draft(self, draft_id):
        """Permanently remove one saved league.

        Steps:
            1. Call `delete` from db/documents.py, matching on the draft id.

        Args:
            draft_id: Which saved draft to remove.

        Returns:
            None: Deleting an id that does not exist is treated as success.

        Note:
            This removes the draft record only. Notes, markings, and saved plans
            that reference this draft id are left behind and will simply never be
            read again.
        """
        delete(Collections.DRAFTS, {"draft_id": draft_id})


