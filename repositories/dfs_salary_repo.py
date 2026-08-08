"""Stores and serves the weekly Daily Fantasy salaries.

One collection holding every slate ever loaded, keyed by season, week, site and
player. Re-loading a week replaces that week's rows and leaves every other week
alone, so a mid-week price change can be picked up as many times as you like
without disturbing anything.

KEEPING THE HISTORY IS DELIBERATE. Overwriting one current slate would be simpler
and would answer today's question just as well, but it permanently forecloses
"was he cheaper last week", any comparison of price against what actually
happened, and any later attempt to see whether the market was right. Those are
not recoverable after the fact, and a week of prices is a few hundred kilobytes.
"""

import pandas as pd

from registry import Collections

KEY_FIELDS = ("season", "week", "site", "site_player_id")
"""What makes a salary row unique.

The SITE's own player id rather than this app's, because it is the only one every
row has -- a team defence and an unmatched player both arrive without a
`canonical_id`, and keying on that would collapse them all into one row.
"""


class DfsSalaryRepo:
    """Reads and writes the salary collection.

    The database module is injectable so the tests can run without a database,
    the same arrangement `DraftSessionRepo` uses.
    """

    def __init__(self, documents=None):
        """Wire up the repository.

        Args:
            documents: Something exposing `bulk_upsert`, `find_all` and
                `ensure_index`. Left out, the real `db.documents` is used.
        """
        if documents is None:
            from db import documents as real_documents
            documents = real_documents
        self._documents = documents

    def ensure_indexes(self):
        """Create the indexes the reads below rely on.

        Steps:
            1. Index the four key fields together and mark it unique, so the
               same player cannot be stored twice for one slate whatever the
               loader does.
            2. Index season and week together, which is how every read starts.

        Returns:
            None.
        """
        self._documents.ensure_index(Collections.DFS_SALARIES,
                                     list(KEY_FIELDS), unique=True)
        self._documents.ensure_index(Collections.DFS_SALARIES,
                                     ["season", "week"])

    def save_slate(self, frame):
        """Write one slate, replacing whatever was there for that week and site.

        Steps:
            1. Do nothing if there is nothing to write.
            2. Turn the rows into plain dictionaries, dropping the blanks so a
               missing value is absent rather than stored as a NaN nothing else
               understands.
            3. Write them in bulk, matching on the key fields so a reload
               updates in place.

        Args:
            frame: A slate in the shape `adapters.dfs_salary_adapter` produces.

        Returns:
            int: How many rows were written.

        Note:
            An upsert rather than a delete-then-insert. If a later load has FEWER
            players than an earlier one -- a site trimming its slate -- the
            dropped players linger. That is the right trade here: a stale row
            keeps a salary that was once true, where a delete-first load leaves
            nothing at all if the second file turns out to be broken.
        """
        if frame.empty:
            return 0

        records = [
            {key: value for key, value in row.items() if pd.notna(value)}
            for row in frame.to_dict("records")
        ]
        self._documents.bulk_upsert(Collections.DFS_SALARIES, records,
                                    list(KEY_FIELDS))
        return len(records)

    def slate(self, season, week, site=None) -> pd.DataFrame:
        """Read one week's salaries.

        Steps:
            1. Ask for the rows matching that season and week, and that site if
               one was named.
            2. Hand back an empty frame with the right columns if there are
               none, so callers need no special case.

        Args:
            season: Which season, as a year.
            week: Which week.
            site: `"FanDuel"` or `"DraftKings"`, or None for both.

        Returns:
            pd.DataFrame: One row per salaried player, with the columns the
                adapter produced.
        """
        query = {"season": int(season), "week": int(week)}
        if site is not None:
            query["site"] = site

        rows = self._documents.find_all(Collections.DFS_SALARIES, query)
        frame = pd.DataFrame(list(rows))

        if frame.empty:
            from adapters.dfs_salary_adapter import COLUMNS
            return pd.DataFrame(columns=COLUMNS)
        return frame.drop(columns=[c for c in ("_id",) if c in frame.columns])

    def available_slates(self) -> pd.DataFrame:
        """List which weeks have salaries loaded, newest first.

        Lets a page offer only the slates that exist rather than every week of
        the season, most of which have nothing behind them.

        Steps:
            1. Read every row.
            2. Reduce to the distinct season, week and site combinations, and
               count the players in each.
            3. Sort newest first.

        Returns:
            pd.DataFrame: `season`, `week`, `site` and `players`. Empty if
                nothing has been loaded.
        """
        rows = pd.DataFrame(list(
            self._documents.find_all(Collections.DFS_SALARIES)))

        if rows.empty:
            return pd.DataFrame(columns=["season", "week", "site", "players"])

        counted = rows.groupby(["season", "week", "site"],
                               as_index=False).size()
        return (counted.rename(columns={"size": "players"})
                .sort_values(["season", "week"], ascending=False)
                .reset_index(drop=True))
