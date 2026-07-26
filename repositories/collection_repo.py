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
    """

    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self._df = None

    def read(self) -> pd.DataFrame:
        if self._df is None:
            self._df = read_collection(self.collection_name)
        return self._df

    def refresh(self) -> pd.DataFrame:
        self._df = read_collection(self.collection_name)
        return self._df