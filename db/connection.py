"""Opens and shares the single connection to the MongoDB database.

Every other module in `db/` needs a live database handle, and opening a new
network connection for each query would be slow and wasteful. This module opens
one connection the first time it is asked for and hands out that same one
forever after.
"""

import os

from pymongo import MongoClient

# Holds the one connection once it has been created. Starts as None, meaning
# "no connection opened yet". Nothing connects until someone actually asks.
_client = None


def get_client():
    """Get the shared connection to the MongoDB server, opening it if needed.

    Opening a database connection is slow, so the app opens exactly one and
    reuses it. Most code will not call this directly and should call `get_db`
    instead, which returns the specific database rather than the whole server.

    Steps:
        1. Look at the module-level `_client` variable.
        2. If it is still None, no connection exists yet, so create a
           `MongoClient` using the connection string in the `MONGODB_URI`
           environment variable.
        3. Store that client in `_client` so the next call skips step 2.
        4. Return the client.

    Returns:
        MongoClient: The connected MongoDB client, representing the whole
            database server.

    Raises:
        KeyError: If the `MONGODB_URI` environment variable is not set. This
            usually means the local environment file is missing or was not
            loaded before the app started.
    """
    global _client
    if _client is None:
        _client = MongoClient(os.environ["MONGODB_URI"])
    return _client


def get_db():
    """Get the DataHuddle database, where all of this app's collections live.

    A single MongoDB server can host many databases; this app uses exactly one,
    named "data-huddle". Almost every read or write in the codebase starts by
    calling this function.

    Steps:
        1. Call `get_client` above to get the shared server connection, opening
           it on the first call.
        2. Select the "data-huddle" database from that server by name.

    Returns:
        Database: A pymongo database handle. Index into it with a collection
            name, for example `get_db()["player_notes"]`, to get a collection
            you can query.
    """
    return get_client()["data-huddle"]
