"""Vendor-schema -> canonical-schema adapter for Fantasy Footballers (UDK)
QB/flex projection collections.

Owns the one genuinely fragile piece of this data source: the UDK CSV export
has duplicate `YDS`/`TDS` headers (once per stat category), which pandas
renames to `YDS.1`/`TDS.1` on read — that positional mapping lives here and
nowhere else. Output is raw stats only; no fantasy points are computed here
(that's services/projections_service.py, via scoring.py).
"""

import pandas as pd

# The ten raw stats every player row ends up carrying, whatever his position.
# A quarterback's receiving numbers are genuinely zero rather than unknown,
# which is why they are filled in rather than left blank.
STAT_COLUMNS = [
    "passing_yards", "passing_tds", "interceptions",
    "rushing_attempts", "rushing_yards", "rushing_tds",
    "receptions", "receiving_yards", "receiving_tds",
    "fumbles_lost",
]


ANALYSTS = ("andy", "mike", "jason")
"""The three Fantasy Footballers who publish separate projections. Their
disagreement is itself a signal -- a player all three see the same way is a very
different proposition from one they split on."""


class FfbProjectionsAdapter:
    """Loads Fantasy Footballers projections, one analyst at a time or all three.

    Each analyst publishes a QB file and a flex file with identical schemas, so
    this takes a repo pair per analyst rather than a single pair. Reading those
    files is fiddly because the vendor's export repeats the column names `YDS`
    and `TDS` for two different stat categories; untangling that is this class's
    main job, and it happens in `_normalize_qb` and `_normalize_flex`.
    """

    def __init__(self, repos_by_analyst: dict):
        """Remember which pair of data sources belongs to each analyst.

        The repositories are handed in rather than created here so tests can
        supply fakes and the app can supply database-backed ones.

        Steps:
            1. Store the mapping on the instance. Nothing is read from the
               database yet; that happens on the first call to `load`.

        Args:
            repos_by_analyst: A dictionary whose keys are analyst names such as
                "andy" and whose values are a two-item tuple of
                (quarterback repository, flex repository). Each repository must
                have a `.read()` method returning a DataFrame. Whichever
                analysts appear here are the ones this adapter can serve.
        """
        self._repos_by_analyst = repos_by_analyst

    @property
    def analysts(self) -> list:
        """List which analysts actually have data sources wired up.

        The module-level `ANALYSTS` names all three Fantasy Footballers, but an
        app or a test may only have data for some of them. Callers that want to
        loop over "whoever is actually available" should use this rather than
        `ANALYSTS`.

        Steps:
            1. Take the keys of the mapping saved in `__init__` and return them
               as a list.

        Returns:
            list: Analyst names, in the order they were supplied to `__init__`.
        """
        return list(self._repos_by_analyst)

    def load(self, analyst: str) -> pd.DataFrame:
        """Load one analyst's projections, with quarterbacks and flex combined.

        Quarterbacks and flex players arrive in two separate files with two
        different layouts. This puts them into a single table with one set of
        column names, which is what everything downstream expects.

        Steps:
            1. Look up the analyst's two repositories. An unknown name raises
               here rather than quietly returning nothing.
            2. Read and reshape the quarterback file with `_normalize_qb`, and
               the flex file with `_normalize_flex`.
            3. Stack the two results with `pd.concat`. `ignore_index=True`
               renumbers the rows so the two sources do not repeat row numbers.
            4. Add a column of zeros for any of the ten `STAT_COLUMNS` neither
               file supplied.
            5. Replace any remaining blanks in those stat columns with 0.

        Args:
            analyst: Which analyst's projections to load, one of "andy",
                "mike", or "jason".

        Returns:
            pd.DataFrame: One row per player, with `name`, `team`, `bye_week`,
                `position`, `rank`, and all ten `STAT_COLUMNS`. Missing stats
                are 0 rather than blank, since a quarterback genuinely has no
                receptions. These are raw stats only; fantasy points are
                calculated later by services/projections_service.py.

        Raises:
            KeyError: If `analyst` is not one of the names passed to
                `__init__`. Failing loudly beats silently returning an empty
                table.
        """
        qb_repo, flex_repo = self._repos_by_analyst[analyst]

        combined = pd.concat(
            [self._normalize_qb(qb_repo.read()), self._normalize_flex(flex_repo.read())],
            ignore_index=True, sort=False,
        )

        for col in STAT_COLUMNS:
            if col not in combined.columns:
                combined[col] = 0
        combined[STAT_COLUMNS] = combined[STAT_COLUMNS].fillna(0)

        return combined

    def load_all(self) -> pd.DataFrame:
        """Load every analyst's projections stacked into one long table.

        Used wherever the app wants to compare or average the three analysts,
        such as blending them into a single projection or showing how much they
        disagree about a player.

        Steps:
            1. Start an empty list to collect one table per analyst.
            2. For each analyst wired up in `__init__`, call `load` above and
               tag every row with an `analyst` column naming who produced it.
            3. Stack all the tagged tables into one with `pd.concat`.

        Returns:
            pd.DataFrame: The same columns as `load`, plus `analyst`. One row
                per player *per analyst*, so a player all three rated appears
                three times.

        Note:
            This is deliberately "long" (one row per player-analyst) rather than
            "wide" (one row per player with a column per analyst). The analysts
            do not cover identical player sets -- Andy has 267 flex players to
            Mike's and Jason's 265 -- so a wide table would be full of blanks and
            every consumer would have to decide what those meant. Long format
            makes "average over whoever actually rated him" a plain groupby.
        """
        frames = []
        for analyst in self._repos_by_analyst:
            frames.append(self.load(analyst).assign(analyst=analyst))
        return pd.concat(frames, ignore_index=True, sort=False)

    def _normalize_qb(self, df):
        """Rename the quarterback file's columns to the app's canonical names.

        This is one half of the fragile piece described in the module docstring.
        The vendor's export uses the header `YDS` twice, once for passing yards
        and once for rushing yards; pandas keeps the first as `YDS` and renames
        the second to `YDS.1`. The same happens for `TDS`. That positional
        assumption is encoded here and nowhere else in the codebase.

        Steps:
            1. If the incoming table has no rows, return an empty DataFrame,
               since indexing columns that are not there would raise.
            2. Build a new table copying each vendor column to its canonical
               name, treating the first `YDS`/`TDS` pair as passing and the
               second (`YDS.1`/`TDS.1`) as rushing.
            3. Set `position` to the constant "QB", since every row in this file
               is a quarterback and the file has no position column.

        Args:
            df: The raw quarterback projections. Expected vendor columns are
                Name, Team, Bye Week, Rank, YDS, TDS, INT, YDS.1, TDS.1, FUM.

        Returns:
            pd.DataFrame: One row per quarterback with canonical column names.
                Receiving stats are absent entirely; `load` fills them with 0.

        Raises:
            KeyError: If the export's headers changed, which is the failure this
                function is most likely to hit.
        """
        if df.empty:
            return pd.DataFrame()

        return pd.DataFrame({
            "name": df["Name"],
            "team": df["Team"],
            "bye_week": df["Bye Week"],
            "position": "QB",
            "rank": df["Rank"],
            "passing_yards": df["YDS"],
            "passing_tds": df["TDS"],
            "interceptions": df["INT"],
            "rushing_yards": df["YDS.1"],
            "rushing_tds": df["TDS.1"],
            "fumbles_lost": df["FUM"],
        })

    def _normalize_flex(self, df):
        """Rename the flex file's columns to the app's canonical names.

        The other half of the duplicate-header problem. In the flex file the
        first `YDS`/`TDS` pair is rushing and the second (`YDS.1`/`TDS.1`) is
        receiving -- the opposite categories from the quarterback file, which is
        exactly why the two cannot share one function.

        Steps:
            1. If the incoming table has no rows, return an empty DataFrame.
            2. Build a new table copying each vendor column to its canonical
               name, treating the first `YDS`/`TDS` pair as rushing and the
               second as receiving.
            3. Take `position` from the file's own `Pos` column, since a flex
               file mixes running backs, receivers, and tight ends.

        Args:
            df: The raw flex projections. Expected vendor columns are Name,
                Team, Bye Week, Pos, Rank, ATTS, YDS, TDS, REC, YDS.1, TDS.1,
                FUM.

        Returns:
            pd.DataFrame: One row per flex player with canonical column names.
                Passing stats are absent entirely; `load` fills them with 0.

        Raises:
            KeyError: If the export's headers changed.
        """
        if df.empty:
            return pd.DataFrame()

        return pd.DataFrame({
            "name": df["Name"],
            "team": df["Team"],
            "bye_week": df["Bye Week"],
            "position": df["Pos"],
            "rank": df["Rank"],
            "rushing_attempts": df["ATTS"],
            "rushing_yards": df["YDS"],
            "rushing_tds": df["TDS"],
            "receptions": df["REC"],
            "receiving_yards": df["YDS.1"],
            "receiving_tds": df["TDS.1"],
            "fumbles_lost": df["FUM"],
        })
