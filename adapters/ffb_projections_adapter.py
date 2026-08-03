"""Vendor-schema -> canonical-schema adapter for Fantasy Footballers (UDK)
QB/flex projection collections.

Owns the one genuinely fragile piece of this data source: the UDK CSV export
has duplicate `YDS`/`TDS` headers (once per stat category), which pandas
renames to `YDS.1`/`TDS.1` on read — that positional mapping lives here and
nowhere else. Output is raw stats only; no fantasy points are computed here
(that's services/projections_service.py, via scoring.py).
"""

import pandas as pd

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
    this takes a repo pair per analyst rather than a single pair.
    """

    def __init__(self, repos_by_analyst: dict):
        """
        Parameters:
            repos_by_analyst (dict): analyst name -> (qb_repo, flex_repo).
        """
        self._repos_by_analyst = repos_by_analyst

    @property
    def analysts(self) -> list:
        """Which analysts actually have collections wired up."""
        return list(self._repos_by_analyst)

    def load(self, analyst: str) -> pd.DataFrame:
        """
        Purpose: One analyst's projections, QB and flex combined.

        Parameters:
            analyst (str): "andy", "mike" or "jason".

        Returns:
            pd.DataFrame -- one row per player with name, team, bye_week,
            position, rank, and the ten STAT_COLUMNS. Missing stats are 0, since
            a quarterback genuinely has no receptions.

        Raises:
            KeyError: Unknown analyst -- better than silently returning nothing.
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
        """
        Purpose: Every analyst's projections stacked into one long table.

        Returns:
            pd.DataFrame -- the same columns as load(), plus `analyst`. One row
            per (player, analyst).

        Notes:
            Long rather than wide on purpose. The analysts don't cover identical
            player sets -- Andy has 267 flex players to Mike's and Jason's 265 --
            so a wide table would be full of NaN and every consumer would have to
            decide what that meant. Long format makes "average over whoever
            actually rated him" a plain groupby.
        """
        frames = []
        for analyst in self._repos_by_analyst:
            frames.append(self.load(analyst).assign(analyst=analyst))
        return pd.concat(frames, ignore_index=True, sort=False)

    def _normalize_qb(self, df):
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