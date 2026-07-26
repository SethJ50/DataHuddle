import pandas as pd

from db.connection import get_db


def read_collection(collection_name):
    docs = list(get_db()[collection_name].find({}, {"_id": 0}))
    return pd.DataFrame(docs)
