import uuid
from datetime import datetime, timezone

from registry import Collections
from db.documents import find_one, find_all, upsert, delete
from draft_model.config import DEFAULT_STARTING_SLOTS

class DraftService:
    def list_drafts(self):
        # Fetches every saved draft, for populating draft-picker dropdown
        return find_all(Collections.DRAFTS)

    def create_draft(self, name, num_teams, draft_position, num_rounds, platform,
                    scoring_format, starting_slots=None, keepers=None, roster_size=None):
        """
        Purpose: Save a new draft's settings.

        Parameters:
            name (str): Display name.
            num_teams, draft_position, num_rounds (int): League shape.
            platform (str): "espn" | "yahoo" | "sleeper".
            scoring_format (str): A ScoringFormat .value.
            starting_slots (dict | None): Position -> starters, e.g.
                {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1}.
                Defaults to a standard 12-team lineup.
            keepers (list | None): canonical_ids kept before the draft.
            roster_size (int | None): Total slots including bench, distinct from
                num_rounds (a league may draft 15 rounds onto an 18-slot roster).

        Returns:
            str: The new draft_id.

        Notes:
            starting_slots is what makes VORP correct at any league size -- the
            replacement level is derived from it rather than hardcoded, so a
            10-team or superflex league gets the right baseline automatically.

            Keepers are stored as canonical_ids so the UI can show names. The
            draft model keys players by ffc_player_id, so the mapping happens
            when a simulation is set up. A kept team defense can't be represented
            this way (defenses have no canonical_id), which is an accepted
            limitation rather than an oversight.
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
            "keepers": list(keepers or []),
            "roster_size": roster_size,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        upsert(Collections.DRAFTS, {"draft_id": draft_id}, doc)
        return draft_id

    def update_draft(self, draft_id, name, num_teams, draft_position, num_rounds,
                     platform, scoring_format, starting_slots=None, keepers=None,
                     roster_size=None):
        """
        Purpose: Update an existing draft's settings in place.

        Parameters: As create_draft, plus draft_id identifying which to change.

        Returns: None.

        Notes:
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
            "keepers": list(keepers or []),
            "roster_size": roster_size,
        })


    def get_draft(self, draft_id):
        # Load one draft (by id)
        return find_one(Collections.DRAFTS, {"draft_id": draft_id})

    def delete_draft(self, draft_id):
        delete(Collections.DRAFTS, {"draft_id": draft_id})


