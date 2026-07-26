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


class FfbProjectionsAdapter:
    def __init__(self, qb_collection_repo, flex_collection_repo):
        self._qb_collection_repo = qb_collection_repo
        self._flex_collection_repo = flex_collection_repo

    def load(self) -> pd.DataFrame:
        qb = self._normalize_qb(self._qb_collection_repo.read())
        flex = self._normalize_flex(self._flex_collection_repo.read())

        combined = pd.concat([qb, flex], ignore_index=True, sort=False)

        for col in STAT_COLUMNS:
            if col not in combined.columns:
                combined[col] = 0
        combined[STAT_COLUMNS] = combined[STAT_COLUMNS].fillna(0)

        return combined

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