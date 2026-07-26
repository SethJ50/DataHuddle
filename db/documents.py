"""Generic CRUD helpers for keyed Mongo documents.

Counterpart to reader.py (bulk collection dump) and loader.py (wipe-and-replace
from CSV) for collections that are read/written one document at a time —
player_notes, player_categories, draft_plans.
"""

from db.connection import get_db


def find_one(collection_name, filter):
    return get_db()[collection_name].find_one(filter, {"_id": 0})


def find_all(collection_name, filter=None):
    return list(get_db()[collection_name].find(filter or {}, {"_id": 0}))


def upsert(collection_name, filter, doc):
    get_db()[collection_name].update_one(filter, {"$set": doc}, upsert=True)


def delete(collection_name, filter):
    get_db()[collection_name].delete_one(filter)
