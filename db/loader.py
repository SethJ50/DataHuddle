import pandas as pd

from db.connection import get_db


def reload_collection(collection_name, df):
    """Clear a collection and replace its contents with the given DataFrame."""
    collection = get_db()[collection_name]
    collection.delete_many({})
    if not df.empty:
        collection.insert_many(df.to_dict("records"))
    return len(df)


def reload_collection_from_csv(collection_name, csv_path):
    df = pd.read_csv(csv_path)
    return reload_collection(collection_name, df)
