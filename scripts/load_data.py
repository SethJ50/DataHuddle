"""
Clear and reload every MongoDB collection from the CSV files in data/,
including subfolders (e.g. data/ffb/). data/raw/ is skipped — it holds
scratch inputs (saved HTML pages), not data to load.

Each CSV's filename stem (without extension) is used as its collection name,
e.g. data/ffb/ffb_qb_projections.csv -> the 'ffb_qb_projections' collection.

Requires the MONGODB_URI environment variable to be set.

Usage:
    python scripts/load_data.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db.loader import reload_collection_from_csv

DATA_DIR = PROJECT_ROOT / "data"
SKIP_DIRS = {"raw"}


def main():
    for csv_path in sorted(DATA_DIR.rglob("*.csv")):
        relative_parts = csv_path.relative_to(DATA_DIR).parts[:-1]
        if any(part in SKIP_DIRS for part in relative_parts):
            continue
        collection_name = csv_path.stem
        count = reload_collection_from_csv(collection_name, csv_path)
        print(f"Loaded {count} rows into '{collection_name}'")


if __name__ == "__main__":
    main()
