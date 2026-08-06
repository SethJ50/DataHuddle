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
    """Reduce a source's ADP payload to a short fingerprint string.

    A hash is a short string computed from data such that the same data always
    gives the same string, and different data almost certainly gives a different
    one. Comparing two fingerprints is how `append` below decides whether a new
    pull actually contains anything new.

    Steps:
        1. Narrow the table to just the identifying column and the value columns
           that matter, ignoring everything else.
        2. Sort by the identifying column, so the same data in a different row
           order still fingerprints the same.
        3. Render that to CSV text, hash it with SHA-256, and keep the first 16
           characters, which is plenty to tell payloads apart here.

    Args:
        players: The normalized rows about to be stored, one row per player.
        key_column: The column identifying each player within this source, for
            example "ffc_player_id".
        value_columns: The columns whose change we actually care about. Just
            `adp` by default -- a player's name being cleaned up should not
            count as the market moving.

    Returns:
        str: A 16-character hexadecimal fingerprint.

    Raises:
        KeyError: If `key_column` or any of `value_columns` is not in the table.
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
        """Note which collection holds the snapshot history.

        Steps:
            1. Save the collection name on the instance. No connection is opened
               here; every method opens one when it runs.

        Args:
            collection_name: Which collection to read and write. Defaults to the
                real `adp_snapshots` collection named in registry.py; tests pass
                a throwaway name so they never touch real history.
        """
        self._collection_name = collection_name

    def _collection(self):
        """Get a live handle on the snapshots collection.

        A one-line helper so no other method has to repeat the lookup. Deliberately
        not cached: this collection is queried in narrow slices rather than
        loaded whole, so there is nothing worth holding onto.

        Steps:
            1. Call `get_db` from db/connection.py for the shared database
               handle, then index into it by collection name.

        Returns:
            Collection: A pymongo collection you can query, insert into, or
                aggregate over.
        """
        return get_db()[self._collection_name]

    def ensure_indexes(self):
        """Create the two indexes this collection needs to stay fast and correct.

        Call this once before any bulk write -- see the note in
        `db.documents.ensure_index` about why "before" rather than "after"
        matters for write speed.

        Steps:
            1. Call `ensure_index` from db/documents.py to build a unique index
               over KEY_FIELDS, which is what actually enforces one row per
               observation.
            2. Build a second, non-unique index over source, player, and date to
               speed up the most common read: one player's history, in order.

        Returns:
            None: Both calls are safe to repeat; MongoDB does nothing if the
                index already exists.

        Raises:
            pymongo.errors.OperationFailure: If the collection already holds
                rows that violate the unique key.

        Note:
            The unique index enforces idempotency at the DATABASE level instead
            of trusting the upsert logic. If the key definition is ever wrong,
            you get a loud duplicate-key error rather than quietly doubled rows,
            which here would corrupt every volatility feature downstream.
        """
        ensure_index(self._collection_name, KEY_FIELDS, unique=True, name="snapshot_key")
        # Secondary index for the common read: "this player's history, in order".
        ensure_index(self._collection_name, ("source", "player_key", "snapshot_date"))

    def latest_content_hash(self, source, season, fmt, num_teams):
        """Get the fingerprint of the most recent snapshot for one source and slice.

        Used by `append` below to decide whether a new pull is worth storing. If
        the fingerprints match, the source has not changed since last time.

        Steps:
            1. Query the collection for documents matching this source, season,
               format, and league size.
            2. Sort by snapshot date descending, so the newest comes first, and
               take only that one, asking for just the `content_hash` field.
            3. Return that hash, or None if no snapshot has ever been stored for
               this slice.

        Args:
            source: Which platform the data came from: "ffc", "espn", "sleeper",
                or "yahoo".
            season: The season year, as a number.
            fmt: Scoring format as stored, or None for sources that have none
                (Yahoo).
            num_teams: League size as stored, or None.

        Returns:
            str | None: The stored fingerprint, or None if nothing has been
                stored for this slice yet, which is normal on a first run.
        """
        doc = self._collection().find_one(
            {"source": source, "season": season, "format": fmt, "num_teams": num_teams},
            {"_id": 0, "content_hash": 1},
            sort=[("snapshot_date", -1)],
        )
        return doc.get("content_hash") if doc else None

    def append(self, *, source, season, fmt, num_teams, snapshot_date,
               players, key_column, meta=None, skip_if_unchanged=True):
        """Record one source's ADP as it stood on one date.

        The only way rows enter this collection. Every argument is
        keyword-only (that is what the bare `*` in the signature means), because
        there are too many similar-looking values to pass positionally without
        eventually swapping two by mistake.

        Steps:
            1. Fingerprint the payload with `compute_content_hash` above.
            2. If skipping is enabled, compare that against
               `latest_content_hash` above and return without writing when they
               match.
            3. Record the current UTC time, so every row from this run shares one
               "when we pulled it" stamp.
            4. Turn each player row into a document, copying the row's own
               columns and adding the provenance fields, the snapshot date, the
               player key as a string, and the fingerprint.
            5. Hand the whole list to `bulk_upsert` from db/documents.py, keyed
               on KEY_FIELDS, so re-running the same day updates rather than
               duplicates.

        Args:
            source: "ffc", "espn", "sleeper", or "yahoo".
            season: The season this ADP describes, as a year.
            fmt: Scoring format, or None where the source has none.
            num_teams: League size, or None. For FFC this is provenance ONLY --
                verified not to change the data at all.
            snapshot_date: The observation date as "YYYY-MM-DD". A date, not a
                timestamp, so two pulls on the same day collapse into one
                observation rather than becoming two.
            players: Normalized rows, one per player. For FFC these come from
                `adapters.ffc_adapter.normalize_players`, with columns
                `ffc_player_id`, `name`, `position`, `team`, `adp`, `stdev`,
                `high`, `low`, `times_drafted`, and `bye`. Platform sources
                supply whatever subset they have; missing spread columns are
                simply absent.
            key_column: Which column identifies a player within this source.
                FFC uses "ffc_player_id"; platforms usually use a normalized
                name.
            meta: The source's own description of the pull. For FFC this is
                total_drafts, start_date, end_date, and rounds -- the sample size
                and the true observation window.
            skip_if_unchanged: When True and the payload fingerprints identically
                to the previous snapshot, write nothing at all.

        Returns:
            dict: Always has "written" (how many documents were sent), "skipped"
                (True when the payload was unchanged), and "content_hash". When
                something was written it also carries "counts", the matched /
                upserted / modified tally from `bulk_upsert`.

        Note:
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
        """Pull a slice of stored history back as a table.

        Every filter is optional, so this covers everything from "one player's
        full history" to "the entire collection". Pass filters where you can:
        this collection is expected to reach tens of thousands of rows.

        Steps:
            1. Build a query dictionary, adding an entry only for the filters
               that were actually supplied. An empty dictionary means "match
               everything".
            2. Convert `player_key` to a string if given, since it is always
               stored as text even when the source's id is a number.
            3. Run the query, excluding MongoDB's internal `_id` field, and load
               the results into a DataFrame.

        Args:
            source: Optional platform name to filter to.
            season: Optional season year to filter to.
            fmt: Optional scoring format to filter to.
            player_key: Optional single player to filter to.

        Returns:
            pd.DataFrame: One row per player per snapshot date, carrying both
                the stored ADP fields and the provenance columns `source`,
                `season`, `format`, `num_teams`, `snapshot_date`, `pulled_at`,
                and `content_hash`. An empty DataFrame when nothing matches.
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
        """Summarize what history actually exists, without downloading it.

        The sanity check to run after a backfill, and what the ingest script's
        --dry-run reports before one. It answers "which source, season, format,
        and date combinations do I have, and how many players in each".

        Steps:
            1. Build an aggregation pipeline that groups the documents by
               source, season, format, and snapshot date, counting rows in each
               group. Grouping happens inside MongoDB, so only the small summary
               travels over the network.
            2. Run it and flatten each result, lifting the grouping fields out of
               the nested `_id` key and alongside the count.
            3. If nothing came back, return an empty table that still has the
               right column names, so callers can use those columns
               unconditionally.
            4. Otherwise build the table and sort it: source alphabetically,
               season newest first, format alphabetically.

        Returns:
            pd.DataFrame: Columns `source`, `season`, `format`, `snapshot_date`,
                and `players` (how many player rows that snapshot holds). Empty,
                but with those columns, if nothing is stored.

        Note:
            Uses an aggregation so counting does not drag every document over the
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
