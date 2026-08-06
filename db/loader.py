"""Replaces the entire contents of a MongoDB collection in one shot.

Used by the data-loading scripts, where a source file is the truth and the
database is just a copy of it. Wiping and rewriting keeps the two in sync
without having to work out which individual rows changed. For collections the
app edits one record at a time, such as notes and draft plans, use
`db/documents.py` instead.
"""

import pandas as pd

from db.connection import get_db


def reload_collection(collection_name, df):
    """Delete everything in a collection and refill it from a DataFrame.

    This is a destructive "wipe and replace". It exists because reloading a
    whole source file is far simpler than working out which rows were added,
    changed, or removed since the last load.

    Steps:
        1. Call `get_db` from `db/connection.py` to get the shared database
           handle, then select the named collection.
        2. Delete every document currently in that collection.
        3. If the DataFrame has any rows, convert it to a list of dictionaries
           (one per row, keyed by column name) and insert them all at once.
        4. Return how many rows were written.

    Args:
        collection_name: Name of the collection to overwrite.
        df: The table to store. Each row becomes one document and each column
            becomes a field on that document, so the column names decide the
            field names. An empty DataFrame is allowed and simply leaves the
            collection empty.

    Returns:
        int: The number of rows written, which is just the row count of `df`.
    """
    collection = get_db()[collection_name]
    collection.delete_many({})
    if not df.empty:
        collection.insert_many(df.to_dict("records"))
    return len(df)


def reload_collection_from_csv(collection_name, csv_path):
    """Load a CSV file and use it to replace a collection's contents.

    A convenience wrapper for the common case where the source of truth is a
    CSV file on disk, which is how the scripts in `scripts/` push reference
    data into the database.

    Steps:
        1. Read the CSV into a DataFrame.
        2. Pass that DataFrame to `reload_collection` above, which wipes the
           collection and inserts the new rows.

    Args:
        collection_name: Name of the collection to overwrite.
        csv_path: Path to the CSV file to read, as a string or Path object.

    Returns:
        int: The number of rows written.

    Raises:
        FileNotFoundError: If no file exists at `csv_path`.
    """
    # Shape depends entirely on the file: pandas reads the first line as column
    # names, one row per remaining line, and guesses a type for each column.
    df = pd.read_csv(csv_path)
    return reload_collection(collection_name, df)
