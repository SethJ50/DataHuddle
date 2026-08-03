"""Append-only, dated store of every ADP observation from every source.

The counterpart to the wipe-and-replace collections (`ffc_adp`,
`espn_projections`, ...) which only ever hold "right now". This one never
forgets, which makes it the only possible source of two things:

  1. The training set for the surrogate width model (draft_model/DESIGN.md 5.5)
  2. Any time-series feature -- above all trailing ADP volatility, which
     STDEV_MODEL_LONGTERM.md calls the strongest durable predictor available

All four sources live in ONE collection rather than one each, because every
query that motivates this layer wants them together: cross-source ADP
disagreement is a groupby, not a four-way join. Sources that publish no spread
(everyone except FFC) simply leave those fields null.

See draft_model/DESIGN.md 5.1.
"""

import hashlib
from datetime import datetime, timezone

import pandas as pd

from db.connection import get_db
from db.documents import bulk_upsert, ensure_index
from registry import Collections

# What makes one observation distinct from another. A re-run with the same key
# updates in place rather than appending a duplicate.
KEY_FIELDS = ("source", "season", "format", "num_teams", "player_key", "snapshot_date")


def compute_content_hash(players, key_column, value_columns=("adp",)):
    """
    Purpose:
        Fingerprint a source's payload so an unchanged one can be skipped.

    Parameters:
        players (pd.DataFrame): The normalized rows about to be stored.
        key_column (str): Column identifying each player within this source.
        value_columns (tuple[str]): The columns whose change we actually care
            about. Just `adp` by default -- a player's name being cleaned up
            shouldn't count as the market moving.

    Returns:
        str: A short hex digest.

    Notes:
        Sorted before hashing so row order can't produce a spurious difference.
    """
    subset = players[[key_column, *value_columns]].sort_values(key_column)
    return hashlib.sha256(subset.to_csv(index=False).encode()).hexdigest()[:16]


class AdpSnapshotRepo:
    """Reads and appends dated ADP observations.

    Unlike CollectionRepo (which caches a whole collection in memory forever),
    this deliberately queries live: the collection grows without bound, and the
    questions asked of it are narrow slices rather than "give me everything".
    """

    def __init__(self, collection_name=Collections.ADP_SNAPSHOTS):
        self._collection_name = collection_name

    def _collection(self):
        return get_db()[self._collection_name]

    def ensure_indexes(self):
        """
        Purpose: Create the indexes this collection needs. Call once before any
            bulk write -- see the note in db.documents.ensure_index about why
            "before" matters.

        Returns: None.

        Notes:
            The unique index enforces idempotency at the DATABASE level instead
            of trusting the upsert logic. If the key definition is ever wrong,
            you get a loud duplicate-key error rather than quietly doubled rows,
            which here would corrupt every volatility feature downstream.
        """
        ensure_index(self._collection_name, KEY_FIELDS, unique=True, name="snapshot_key")
        # Secondary index for the common read: "this player's history, in order".
        ensure_index(self._collection_name, ("source", "player_key", "snapshot_date"))

    def latest_content_hash(self, source, season, fmt, num_teams):
        """
        Purpose: The fingerprint of the most recent snapshot for one source/slice,
            so an identical new pull can be skipped.

        Parameters:
            source (str): "ffc" | "espn" | "sleeper" | "yahoo".
            season (int): Season year.
            fmt (str | None): Scoring format as stored, or None for sources
                without one (Yahoo).
            num_teams (int | None): League size as stored, or None.

        Returns:
            str | None: The hash, or None if nothing has been stored yet.
        """
        doc = self._collection().find_one(
            {"source": source, "season": season, "format": fmt, "num_teams": num_teams},
            {"_id": 0, "content_hash": 1},
            sort=[("snapshot_date", -1)],
        )
        return doc.get("content_hash") if doc else None

    def append(self, *, source, season, fmt, num_teams, snapshot_date,
               players, key_column, meta=None, skip_if_unchanged=True):
        """
        Purpose:
            Record one source's ADP as of one date.

        Parameters:
            source (str): "ffc" | "espn" | "sleeper" | "yahoo".
            season (int): The season this ADP describes.
            fmt (str | None): Scoring format, or None where the source has none.
            num_teams (int | None): League size, or None. For FFC this is
                provenance ONLY -- verified not to change the data at all.
            snapshot_date (str): "YYYY-MM-DD". A date, not a timestamp, so two
                pulls on the same day collapse into one observation.
            players (pd.DataFrame): Normalized rows. For FFC these come from
                adapters.ffc_adapter.normalize_players -- columns
                ffc_player_id, name, position, team, adp, stdev, high, low,
                times_drafted, bye. Platform sources supply whatever subset they
                have; missing spread columns are simply absent.
            key_column (str): Which column identifies a player within this source
                (FFC: "ffc_player_id"; platforms: usually a normalized name).
            meta (dict | None): The source's own description of the pull. For FFC
                this is total_drafts / start_date / end_date / rounds -- sample
                size and the true observation window.
            skip_if_unchanged (bool): When True and the payload is byte-identical
                to the previous snapshot, write nothing.

        Returns:
            dict: {'written': int, 'skipped': bool, 'content_hash': str}

        Notes:
            The skip is not an optimization, it is a correctness guard. Platform
            ADP arrives via manually-refreshed CSVs; snapshotting an un-refreshed
            one weekly would append identical rows, making trailing volatility
            compute to exactly 0.0. Zero volatility reads as a confident
            measurement, which is worse than a gap.
        """
        content_hash = compute_content_hash(players, key_column)

        if skip_if_unchanged:
            previous = self.latest_content_hash(source, season, fmt, num_teams)
            if previous == content_hash:
                return {"written": 0, "skipped": True, "content_hash": content_hash}

        pulled_at = datetime.now(timezone.utc).isoformat()

        docs = []
        for row in players.to_dict("records"):
            doc = dict(row)
            doc.update({
                "source": source,
                "season": season,
                "format": fmt,
                "num_teams": num_teams,
                "snapshot_date": snapshot_date,
                "player_key": str(row[key_column]),
                "pulled_at": pulled_at,
                "content_hash": content_hash,
                "meta": meta or {},
            })
            docs.append(doc)

        result = bulk_upsert(self._collection_name, docs, KEY_FIELDS)
        return {"written": len(docs), "skipped": False,
                "content_hash": content_hash, "counts": result}

    def read(self, source=None, season=None, fmt=None, player_key=None):
        """
        Purpose: Pull a slice of history back as a DataFrame.

        Parameters:
            source, season, fmt, player_key: Optional filters; omit for all.

        Returns:
            pd.DataFrame -- one row per (player, snapshot_date), carrying both
            the stored ADP fields and the provenance columns (source, season,
            format, num_teams, snapshot_date, pulled_at, content_hash).
            Empty DataFrame when nothing matches.
        """
        query = {}
        if source is not None:
            query["source"] = source
        if season is not None:
            query["season"] = season
        if fmt is not None:
            query["format"] = fmt
        if player_key is not None:
            query["player_key"] = str(player_key)

        return pd.DataFrame(list(self._collection().find(query, {"_id": 0})))

    def coverage(self):
        """
        Purpose: What history actually exists -- the sanity check to run after a
            backfill, and the thing --dry-run reports before one.

        Returns:
            pd.DataFrame with columns source, season, format, snapshot_date,
            players. Sorted newest first. Empty if nothing is stored.

        Notes:
            Uses an aggregation so counting doesn't drag every document over the
            wire; this collection is expected to reach tens of thousands of rows.
        """
        pipeline = [
            {"$group": {
                "_id": {"source": "$source", "season": "$season",
                        "format": "$format", "snapshot_date": "$snapshot_date"},
                "players": {"$sum": 1},
            }},
        ]
        rows = [
            {**row["_id"], "players": row["players"]}
            for row in self._collection().aggregate(pipeline)
        ]
        if not rows:
            return pd.DataFrame(columns=["source", "season", "format", "snapshot_date", "players"])

        return (
            pd.DataFrame(rows)
            .sort_values(["source", "season", "format"], ascending=[True, False, True])
            .reset_index(drop=True)
        )
