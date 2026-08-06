"""Generic cached wrapper around a bulk Mongo collection read.

Every bulk-dump Mongo source (projections, rankings, ADP, player_id_map) is
read through one of these instead of calling db/reader.py directly, so
caching lifecycle lives in exactly one place.
"""

import pandas as pd

from db.reader import read_collection


class CollectionRepo:
    """Cache-forever wrapper around a single Mongo collection.

    Loads once, on first `.read()` call. Call `.refresh()` to force a reload
    (e.g. after rerunning scripts/load_data.py) without restarting the app.

    This matters because a Streamlit app re-runs its whole script on every
    click. Without caching, each click would re-download every collection from
    the database, making the app feel sluggish for data that changes maybe once
    a week.
    """

    def __init__(self, collection_name: str):
        """Note which collection this repository is responsible for.

        Nothing is downloaded here. Building a repository is cheap on purpose,
        so the app can create one per data source at startup without paying for
        any of them until something is actually displayed.

        Steps:
            1. Save the collection name on the instance.
            2. Set the cached table to None, which is this class's way of
               recording "not loaded yet".

        Args:
            collection_name: The MongoDB collection to read, for example
                "espn_projections" or "player_id_map".
        """
        self.collection_name = collection_name
        self._df = None

    def read(self) -> pd.DataFrame:
        """Get this collection as a table, downloading it only the first time.

        This is the method nearly everything calls. Repeated calls are free, so
        callers never need to pass the DataFrame around to avoid re-reading it.

        Steps:
            1. Check whether the cached table is still None, meaning nothing has
               been loaded yet.
            2. If so, call `read_collection` from db/reader.py to pull the whole
               collection down, and store the result.
            3. Return the cached table, whether it was just loaded or already
               there.

        Returns:
            pd.DataFrame: One row per document in the collection, with columns
                taken from the document fields. The exact columns depend on
                which collection this is. Empty with no columns if the
                collection has nothing in it.

        Note:
            The returned object is the cached one, not a copy. Anything that
            modifies it in place changes what every later caller sees, so
            callers that need to alter the data should copy it first.
        """
        if self._df is None:
            self._df = read_collection(self.collection_name)
        return self._df

    def refresh(self) -> pd.DataFrame:
        """Re-download this collection, throwing away whatever was cached.

        Needed because the cache otherwise lasts as long as the app process. If
        you rerun scripts/load_data.py to push new data into MongoDB, the
        running app keeps showing the old numbers until something calls this.

        Steps:
            1. Call `read_collection` from db/reader.py unconditionally, without
               checking the cache.
            2. Overwrite the cached table with the fresh result.
            3. Return it.

        Returns:
            pd.DataFrame: The freshly loaded collection, in the same shape
                `read` describes.
        """
        self._df = read_collection(self.collection_name)
        return self._df
