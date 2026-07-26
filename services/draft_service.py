import uuid
from datetime import datetime, timezone

from registry import Collections
from db.documents import find_one, find_all, upsert, delete

class DraftService:
    def list_drafts(self):
        # Fetches every saved draft, for populating draft-picker dropdown
        return find_all(Collections.DRAFTS)

    def create_draft(self, name, num_teams, draft_position, num_rounds, platform, scoring_format):
        draft_id = uuid.uuid4().hex

        doc = {
            "draft_id": draft_id,
            "name": name,
            "num_teams": num_teams,
            "draft_position": draft_position,
            "num_rounds": num_rounds,
            "platform": platform,
            "scoring_format": scoring_format,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        upsert(Collections.DRAFTS, {"draft_id": draft_id}, doc)
        return draft_id

    def get_draft(self, draft_id):
        # Load one draft (by id)
        return find_one(Collections.DRAFTS, {"draft_id": draft_id})

    def delete_draft(self, draft_id):
        delete(Collections.DRAFTS, {"draft_id": draft_id})