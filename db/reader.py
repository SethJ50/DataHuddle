"""Reads an entire MongoDB collection into a pandas DataFrame.

Most of the app works with tabular data, but MongoDB stores records as
dictionaries. This module bridges the two for the simple case: pull everything
out of one collection at once. For reading or writing single records, use
`db/documents.py` instead.
"""

import pandas as pd

from db.connection import get_db


def read_collection(collection_name):
    """Load every record in a MongoDB collection into a DataFrame.

    Use this when downstream code wants a table it can filter, sort, and merge,
    such as loading all projections or all ADP rows for a page to display.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. Ask that collection for every document. The `{"_id": 0}` part tells
           MongoDB to leave out its internal id field, which is not useful in a
           DataFrame.
        3. Wrap the result in a list, since MongoDB hands back a lazy cursor
           rather than a finished list.
        4. Give that list of dictionaries to pandas, which turns each
           dictionary into a row.

    Args:
        collection_name: The name of the collection to read, for example
            "projections" or "adp_snapshots".

    Returns:
        pd.DataFrame: One row per document, with one column per field found in
            the documents. Column names come straight from the document keys,
            so the exact shape depends on what was stored. An empty collection
            gives back an empty DataFrame with no columns at all, which is why
            callers usually check `.empty` before using specific columns.
    """
    # Each document is a dict of field name -> value; pandas turns the list of
    # them into a table with one row per document.
    docs = list(get_db()[collection_name].find({}, {"_id": 0}))
    return pd.DataFrame(docs)
