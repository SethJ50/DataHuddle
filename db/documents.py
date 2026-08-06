"""Reads and writes MongoDB records one at a time, or in batches.

The other two `db/` modules deal in whole collections: `reader.py` dumps one out
into a table, `loader.py` overwrites one from a CSV. This module is for
collections the app edits piece by piece while it runs, where a single record
changes and the rest must be left alone. Those are the user-owned collections:
player notes, player categories, and draft plans.

The word "upsert" appears throughout and just means "update it if it already
exists, otherwise insert it". That behavior is what makes a job safe to re-run:
running it twice leaves the same data as running it once.
"""

from db.connection import get_db
from pymongo import ASCENDING, UpdateOne

def find_one(collection_name, filter):
    """Fetch a single record from a collection, or nothing if none matches.

    Used whenever the app needs one specific thing, such as the note attached to
    one player or the draft plan for one league.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. Ask MongoDB for the first document matching the filter. The
           `{"_id": 0}` argument tells it to leave out its internal id field,
           which the app never uses.

    Args:
        collection_name: Name of the collection to search, for example
            "player_notes".
        filter: A dictionary describing what to match, where each key is a
            field name and each value is the value that field must have. For
            example `{"player_id": "00-0034796"}` finds the record for that one
            player.

    Returns:
        dict | None: The matching record as a dictionary of field name to
            value, or None when nothing matches. Callers must handle the None
            case, since "no note saved yet" is normal rather than an error.
    """
    return get_db()[collection_name].find_one(filter, {"_id": 0})


def find_all(collection_name, filter=None):
    """Fetch every record in a collection that matches a filter.

    Used when the app needs a group of records rather than one, such as all
    notes for a team or every saved draft plan.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. If no filter was passed, substitute an empty dictionary, which
           MongoDB reads as "match everything".
        3. Run the query, again excluding MongoDB's internal `_id` field.
        4. Wrap the result in a list, because MongoDB returns a lazy cursor
           that can only be walked through once.

    Args:
        collection_name: Name of the collection to search.
        filter: Optional dictionary of field name to required value. Leave it
            out to get the whole collection.

    Returns:
        list[dict]: One dictionary per matching record. An empty list when
            nothing matches, which is normal and not an error.
    """
    return list(get_db()[collection_name].find(filter or {}, {"_id": 0}))


def upsert(collection_name, filter, doc):
    """Save a record, updating the existing one if there is one.

    This is the main "save" used by the app's editing features. Because it
    updates in place when a match exists, the caller does not have to check
    first whether the record is new.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. Look for one document matching the filter.
        3. If found, set the fields from `doc` on it, leaving any fields not
           mentioned in `doc` untouched. If not found, `upsert=True` tells
           MongoDB to create a new document instead.

    Args:
        collection_name: Name of the collection to write to.
        filter: Dictionary identifying which record to update, for example
            `{"player_id": "00-0034796"}`. This is what decides whether the
            write updates something or creates something.
        doc: Dictionary of the fields to set and the values to set them to.
            Fields already on the record but missing from here are kept as they
            are, so this is a partial update rather than a replacement.

    Returns:
        None: The write either succeeded or raised.
    """
    get_db()[collection_name].update_one(filter, {"$set": doc}, upsert=True)


def delete(collection_name, filter):
    """Remove a single record from a collection.

    Backs the app's delete actions, such as clearing a player note. Only one
    record is removed even if several match the filter.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. Delete the first document matching the filter. If nothing matches,
           MongoDB does nothing and reports no error.

    Args:
        collection_name: Name of the collection to delete from.
        filter: Dictionary identifying the record to remove. Be specific here:
            a filter matching many records will still delete one of them, and
            which one is not guaranteed.

    Returns:
        None: Deleting something that does not exist is treated as success.
    """
    get_db()[collection_name].delete_one(filter)

def ensure_index(collection_name, fields, unique=False, name=None):
    """Make sure a collection has a fast lookup path for certain fields.

    Without an index, MongoDB answers a query by reading every document in the
    collection, which gets slower as the collection grows. An index is a
    pre-sorted lookup structure that lets it jump straight to matches instead.
    Call this at the start of any script that writes in bulk.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. Turn the list of field names into the pairs MongoDB expects, each
           one marked ASCENDING so the index is sorted smallest to largest.
        3. Ask MongoDB to create the index. If an identical one already exists
           it quietly does nothing, so calling this repeatedly is safe.

    Args:
        collection_name: Which collection to index.
        fields: Field names in order. Order matters: an index on (a, b) speeds
            up queries that filter on `a`, or on `a` and `b` together, but not
            queries that filter on `b` alone.
        unique: When True, MongoDB rejects any second record that repeats the
            same values for these fields. Use it only when the fields really do
            identify a record, since a violation raises rather than warns.
        name: Optional name for the index. MongoDB invents one if omitted.

    Returns:
        str: The name of the index, either the one passed in or the one MongoDB
            generated.

    Raises:
        pymongo.errors.OperationFailure: If `unique=True` is requested but the
            collection already holds duplicate values for these fields.

    Note:
        Create the index *before* the first bulk write, not after. Every upsert
        has to find its target document first, so without an index each write
        scans a collection that is itself growing, and the job gets slower the
        longer it runs.
    """
    return get_db()[collection_name].create_index(
        [(field, ASCENDING) for field in fields], unique=unique, name=name
    )


def bulk_upsert(collection_name, docs, key_fields, batch_size=1000):
    """Save many records at once, updating any that already exist.

    `upsert` above sends one document per network round trip, which is fine for
    a single edit but painfully slow in bulk: the FFC historical backfill writes
    around 12,800 documents, minutes of pure waiting at typical remote database
    latency. Batching those into groups turns that into seconds.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. Set up a running tally of how many records were matched, inserted,
           and actually changed.
        3. Walk through `docs` in slices of `batch_size`.
        4. For each document in a slice, build an UpdateOne instruction: find
           the record whose `key_fields` match this document's, set the
           document's fields on it, and create it if it is not there.
        5. Send the whole slice in one call. `ordered=False` lets MongoDB keep
           going after a failed document instead of abandoning the rest of the
           batch, which is what you want here, where one bad row should not
           cost you the other 999.
        6. Add that call's counts to the tally and move to the next slice.

    Args:
        collection_name: Target collection.
        docs: The records to write, as a list of dictionaries. Every dictionary
            must contain all of `key_fields`, or building the instruction for
            it raises a KeyError.
        key_fields: The field names that identify a record. A document whose
            key values already exist is updated in place; otherwise it is
            inserted. This is what makes re-running a job harmless.
        batch_size: How many documents to send per round trip. Larger means
            fewer trips but more memory per trip.

    Returns:
        dict: Counts under three keys. "matched" is how many existing records
            the writes found, "upserted" how many new records were created, and
            "modified" how many existing records actually changed, which is
            lower than "matched" when a record was rewritten with the values it
            already had.

    Raises:
        KeyError: If a document is missing one of the `key_fields`.
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
