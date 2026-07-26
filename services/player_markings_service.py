from registry import Collections
from db.documents import find_one, find_all, upsert

class PlayerMarkingsService:
    def get(self, draft_id, canonical_id):
        # Load one player's markings within a draft

        doc = find_one(
            Collections.PLAYER_MARKINGS,
            {"draft_id": draft_id, "canonical_id": canonical_id},
        )
        if doc is None:
            return {"categories": [], "notes": ""}
        return doc

    def save(self, draft_id, canonical_id, categories, notes):
        # Persist a player's markings within a draft (create or update)

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
        # Fetch every player's markings within one draft

        return find_all(Collections.PLAYER_MARKINGS, {"draft_id": draft_id})