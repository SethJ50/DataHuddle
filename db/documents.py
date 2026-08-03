"""Generic CRUD helpers for keyed Mongo documents.

Counterpart to reader.py (bulk collection dump) and loader.py (wipe-and-replace
from CSV) for collections that are read/written one document at a time —
player_notes, player_categories, draft_plans.
"""

from db.connection import get_db
from pymongo import ASCENDING, UpdateOne

def find_one(collection_name, filter):
    return get_db()[collection_name].find_one(filter, {"_id": 0})


def find_all(collection_name, filter=None):
    return list(get_db()[collection_name].find(filter or {}, {"_id": 0}))


def upsert(collection_name, filter, doc):
    get_db()[collection_name].update_one(filter, {"$set": doc}, upsert=True)


def delete(collection_name, filter):
    get_db()[collection_name].delete_one(filter)

def ensure_index(collection_name, fields, unique=False, name=None):
    """
    Purpose:
        Guarantee an index exists on a set of fields, so lookups that would
        otherwise scan the whole collection stay fast as it grows.

    Parameters:
        collection_name (str): Which collection to index.
        fields (sequence[str]): Field names, in order. Order matters -- an index
            on (a, b) helps queries filtering on `a` or on `a and b`, but not on
            `b` alone.
        unique (bool): If True, Mongo REJECTS a second document with the same
            values. Use when the field set is genuinely an identity.
        name (str | None): Optional index name; Mongo generates one if omitted.

    Returns:
        str: The index name.

    Notes:
        Safe to call repeatedly -- Mongo no-ops when an identical index already
        exists, so this belongs at the start of any script that writes in bulk.

        Order matters in a different sense too: create the index BEFORE the first
        bulk write, not after. Every upsert has to find its target document
        first, so without the index each write scans a collection that is itself
        growing -- the job gets slower the longer it runs.
    """
    return get_db()[collection_name].create_index(
        [(field, ASCENDING) for field in fields], unique=unique, name=name
    )


def bulk_upsert(collection_name, docs, key_fields, batch_size=1000):
    """
    Purpose:
        Insert-or-update many documents in a handful of round trips instead of
        one per document.

    Parameters:
        collection_name (str): Target collection.
        docs (list[dict]): Documents to write. Every doc must contain all of
            `key_fields`.
        key_fields (sequence[str]): The fields that identify a document. A doc
            whose key already exists is updated in place; otherwise it's inserted.
            This is what makes re-running a job harmless.
        batch_size (int): Documents per round trip.

    Returns:
        dict with 'matched', 'upserted', 'modified' counts.

    Notes:
        Why this exists: upsert() sends one document per network round trip. The
        FFC historical backfill writes ~12,800 documents, which at typical remote
        Mongo latency is minutes of pure waiting. Batched, it's seconds.

        ordered=False lets Mongo keep going after a failed document rather than
        abandoning the rest of the batch -- appropriate here, where one bad row
        shouldn't cost you the other 999.
    """
    collection = get_db()[collection_name]
    totals = {"matched": 0, "upserted": 0, "modified": 0}

    for start in range(0, len(docs), batch_size):
        batch = docs[start:start + batch_size]
        operations = [
            UpdateOne({field: doc[field] for field in key_fields}, {"$set": doc}, upsert=True)
            for doc in batch
        ]
        if not operations:
            continue

        result = collection.bulk_write(operations, ordered=False)
        totals["matched"] += result.matched_count
        totals["upserted"] += len(result.upserted_ids)
        totals["modified"] += result.modified_count

    return totals
